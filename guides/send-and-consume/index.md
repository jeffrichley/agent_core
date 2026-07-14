# Send and Consume Envelopes

Every message on the bus is an **envelope** — a universal wire format with a `kind`, routing fields (`from_`/`to`), a typed `payload`, and metadata. This guide shows how to publish an envelope through a `BusHandle`, how the target endpoint receives it in `deliver()`, and how to ack, nack, and thread replies via `correlation_id`.

Sources verified: `packages/core/src/agent_core/bus/envelope.py`, `packages/core/src/agent_core/bus/handle.py`, `packages/core/src/agent_core/bus/core.py`, `packages/core/src/agent_core/endpoints/stub.py`.

______________________________________________________________________

## Envelope anatomy

```
class Envelope(BaseModel):
    id: str                              # unique envelope id (uuid4.hex)
    correlation_id: str                  # groups a request/reply chain
    in_reply_to: str | None = None       # id of the envelope being replied to
    from_: str                           # stamped by BusHandle.publish(); do not set manually
    to: str                              # destination endpoint name
    kind: str                            # payload discriminator (see built-in kinds below)
    payload: EnvelopePayload | dict      # typed model for built-in kinds; dict for plugin kinds
    metadata: dict[str, Any]             # open bag for routing hints, channel ids, etc.
    urgency: Literal["green", "yellow", "red"] = "green"
    expires_at: datetime | None = None   # TTL; undelivered envelopes are expired by a sweep
    created_at: datetime
```

`from_` defaults to `""` — the bus overwrites it at publish time with the publishing endpoint's registered name. Endpoints cannot spoof each other.

### Built-in payload kinds

| `kind`           | Payload class           | Key fields                                                           |
| ---------------- | ----------------------- | -------------------------------------------------------------------- |
| `TextMessage`    | `TextMessagePayload`    | `text: str`, `attachments: list[FileAttachment]`                     |
| `Event`          | `EventPayload`          | `type: str`, `schema_version: str`, `data: dict`                     |
| `ToolInvocation` | `ToolInvocationPayload` | `tool: str`, `args: dict`                                            |
| `Cancellation`   | `CancellationPayload`   | \`reason: str                                                        |
| `Progress`       | `ProgressPayload`       | `status: Literal["working","blocked","complete"]`, `note`, `percent` |
| `Acknowledgment` | `AcknowledgmentPayload` | `of: str` (id of the envelope being acknowledged), `note`            |
| `Notification`   | `NotificationPayload`   | `source: str`, `reason: str`, `landed_at: datetime`, `body: dict`    |

For built-in kinds, the payload model is validated strictly at publish time. The `kind` field on the outer envelope must match `payload.kind`; mismatches raise `ValueError` before the envelope reaches the bus.

Plugin-registered kinds carry `dict` payloads; the plugin is responsible for validation.

______________________________________________________________________

## Publishing an envelope

You publish via the `BusHandle` your endpoint received in `start()`. The handle stamps `from_`, runs `pre_publish` hooks, persists the envelope, and dispatches it.

### TextMessage — the most common pattern

```
import uuid
from datetime import UTC, datetime
from agent_core.bus.envelope import Envelope, TextMessagePayload

async def start(self, bus: BusHandle) -> None:
    self._handle = bus

# Later, from anywhere that has access to self._handle:
async def notify_other_agent(self) -> None:
    envelope = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to="other-agent",
        kind="TextMessage",
        payload=TextMessagePayload(text="Task complete — see results in /tmp/output.json"),
        created_at=datetime.now(UTC),
    )
    await self._handle.publish(envelope)
```

`from_` is left at its default (`""`); the bus overwrites it.

### Fan-out — one envelope, multiple recipients

Pass a list to `publish(envelope, to=[...])`. The bus validates all recipients before inserting any, then delivers a copy to each.

```
await self._handle.publish(envelope, to=["agent-a", "agent-b", "agent-c"])
```

### Reply threading with `in_reply_to`

Preserve `correlation_id` from the original envelope and set `in_reply_to` to its `id`:

```
async def deliver(self, envelope: Envelope) -> None:
    assert self._handle is not None
    reply = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=envelope.correlation_id,   # same chain
        in_reply_to=envelope.id,                  # points at the original
        to=envelope.from_,                        # reply to sender
        kind="TextMessage",
        payload=TextMessagePayload(text="Got it."),
        created_at=datetime.now(UTC),
    )
    await self._handle.publish(reply)
    await self._handle.ack(envelope.id)
```

### StubEndpoint.send() shorthand

`StubEndpoint` offers a `.send()` helper that builds and publishes the envelope for you. Useful in tests:

```
await stub.send(
    to="target",
    kind="TextMessage",
    payload={"kind": "TextMessage", "text": "hello"},
    correlation_id=None,    # auto-generated if omitted
    in_reply_to=None,
    metadata=None,
    expires_at=None,
)
```

______________________________________________________________________

## Receiving and acking in deliver()

The bus calls `endpoint.deliver(envelope)` and waits for it to return before dispatching to any other endpoint. You **must** call `ack` before returning (or as soon as handling is confirmed in a background task).

```
async def deliver(self, envelope: Envelope) -> None:
    assert self._handle is not None

    if envelope.kind == "TextMessage":
        text = envelope.payload.text   # type: ignore[union-attr]
        # ... handle the message ...

    # Ack: tell the bus this envelope is handled. Idempotent.
    await self._handle.ack(envelope.id)
```

### Background work pattern

If handling is slow, return promptly after acking and continue in a background task:

```
import asyncio

async def deliver(self, envelope: Envelope) -> None:
    assert self._handle is not None
    await self._handle.ack(envelope.id)          # ack first
    asyncio.create_task(self._do_work(envelope)) # work in background

async def _do_work(self, envelope: Envelope) -> None:
    # ... long work ...
    result = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=envelope.correlation_id,
        in_reply_to=envelope.id,
        to=envelope.from_,
        kind="Progress",
        payload=ProgressPayload(status="complete", note="done"),
        created_at=datetime.now(UTC),
    )
    await self._handle.publish(result)
```

______________________________________________________________________

## Nack and redelivery

Call `nack` when you want to reject an envelope explicitly:

```
# Requeue for redelivery (default):
await self._handle.nack(envelope.id, requeue=True)

# Dead-letter without retry:
await self._handle.nack(envelope.id, requeue=False)
```

To signal a **temporary** failure and let the bus apply backoff automatically, raise `EndpointUnavailable` from `deliver()` instead of calling `nack` — the bus will requeue and back off from this endpoint until it recovers.

```
from agent_core.bus.protocol import EndpointUnavailable

async def deliver(self, envelope: Envelope) -> None:
    if not self._upstream_available():
        raise EndpointUnavailable("upstream down")
    # ... normal handling ...
    await self._handle.ack(envelope.id)
```

### Redelivery limits

The bus retries up to `BusConfig.max_delivery_attempts` (default: 5). After that, the envelope moves to dead-letter regardless of whether the failure is `EndpointUnavailable` or an in-flight timeout.

______________________________________________________________________

## Metadata for routing hints

`metadata` is an open `dict` for side-channel information that adapters and hooks can inspect without changing the payload shape. Example: routing a TextMessage to a specific Discord channel:

```
envelope = Envelope(
    id=uuid.uuid4().hex,
    correlation_id=uuid.uuid4().hex,
    to="discord",
    kind="TextMessage",
    payload=TextMessagePayload(text="Hello from the bus."),
    metadata={"discord": {"channel_id": "1234567890"}},
    created_at=datetime.now(UTC),
)
await self._handle.publish(envelope)
```

The Discord endpoint reads `envelope.metadata["discord"]["channel_id"]` to pick the target channel. The bus itself does not interpret metadata.

______________________________________________________________________

## Next steps

- [Add an endpoint](https://jeffrichley.github.io/agent_core/guides/add-an-endpoint/index.md) — implement deliver() and the ack contract end-to-end
- [Write an extension](https://jeffrichley.github.io/agent_core/guides/write-an-extension/index.md) — register new envelope kinds + renderers via a plugin
- [Concepts — Envelopes](https://jeffrichley.github.io/agent_core/concepts/index.md) — TTL sweeps, dead-letter, urgency levels
