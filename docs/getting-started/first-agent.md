# Your First Agent / Endpoint

This page walks through the core moving parts: the `Endpoint` protocol, the `StubEndpoint` adapter, and how envelopes flow through the bus. Every claim below is grounded in the source — see `packages/core/src/agent_core/bus/protocol.py`, `endpoints/stub.py`, `bus/handle.py`, and `bus/core.py`.

## The Endpoint protocol

Every participant on the bus implements three async methods:

```python
from agent_core.bus.protocol import Endpoint
from agent_core.bus.handle import BusHandle
from agent_core.bus.envelope import Envelope

class Endpoint(Protocol):
    name: str

    async def start(self, bus: BusHandle) -> None:
        """Bus is ready. Store the handle; open connections; start background loops."""

    async def deliver(self, envelope: Envelope) -> None:
        """An envelope addressed to you has arrived.

        You MUST call bus.ack(envelope.id) when handling completes.
        Raise EndpointUnavailable for a transient failure — the bus will retry.
        Any other exception dead-letters the envelope.

        deliver() is awaited before the bus dispatches to any other endpoint,
        so return promptly. Long work belongs in a background task.
        """

    async def stop(self) -> None:
        """Graceful shutdown. Close connections; flush state."""
```

!!! note "Protocol conformance"
    `Endpoint` is `@runtime_checkable`, so the bus verifies conformance when you register an endpoint. Missing any of the three methods raises at load time, not at delivery time.

## StubEndpoint — a ready-made dev adapter

`agent_core.endpoints.stub.StubEndpoint` is a minimal in-memory implementation of the protocol. It records every delivered envelope on an `.inbox` list and provides a `.send()` helper for publishing from its own identity.

```python
from agent_core.endpoints.stub import StubEndpoint

# auto_ack=True (default): the stub acks each delivered envelope automatically.
echo = StubEndpoint(name="echo", auto_ack=True)
```

After the bus calls `start()`, the stub stores the `BusHandle` it receives and uses it for both acking and for `.send()`.

### send() signature

```python
await echo.send(
    to="other-endpoint",
    kind="TextMessage",
    payload={"text": "hello"},
    # optional:
    correlation_id=None,   # auto-generated uuid4.hex if omitted
    in_reply_to=None,
    metadata=None,
    expires_at=None,
)
```

Calling `.send()` before the bus has called `start()` raises `RuntimeError`.

## BusHandle — your identity-bound view of the bus

When the bus starts an endpoint, it hands it a `BusHandle` bound to that endpoint's name. The handle stamps `from_` on every envelope you publish — you cannot spoof another endpoint's identity.

Key methods on `BusHandle`:

```python
# Publish an envelope. `to` overrides envelope.to and accepts a list for fan-out.
await handle.publish(envelope, to="target")

# Confirm successful handling. Idempotent.
await handle.ack(envelope_id)

# Reject. requeue=True schedules redelivery; False dead-letters.
await handle.nack(envelope_id, requeue=True)

# Snapshot of currently registered endpoints.
endpoints = handle.endpoints()   # list[EndpointInfo]
```

## Constructing and running the Bus

`Bus` lives in `agent_core.bus.core`. It takes a `BusConfig` (which requires at minimum a `storage_path`), accepts endpoint registrations via `Bus.register(EndpointSpec(...))`, and is driven with `await bus.start()` / `await bus.stop()`.

!!! warning "Config-driven path preferred in production"
    Constructing a `Bus` directly requires wiring `BusConfig`, `EndpointSpec`, and the async lifecycle yourself. In practice, the daemon runner handles all of that from `agent_core.yaml`. The snippet below shows the minimal direct path for clarity — use it for tests or one-off scripts, not for production services.

```python
import asyncio
from pathlib import Path

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.endpoints.stub import StubEndpoint
from agent_core.bus.envelope import Envelope
import uuid
from datetime import UTC, datetime

async def main() -> None:
    sender = StubEndpoint(name="sender")
    receiver = StubEndpoint(name="receiver")

    bus = Bus(BusConfig(storage_path=Path("/tmp/demo.sqlite")))
    bus.register(EndpointSpec(endpoint=sender))
    bus.register(EndpointSpec(endpoint=receiver))

    await bus.start()

    # StubEndpoint.send() builds and publishes an envelope from "sender".
    await sender.send(
        to="receiver",
        kind="TextMessage",
        payload={"text": "hello from sender"},
    )

    # With auto_ack=True, the envelope is already acked.
    print(receiver.inbox)   # [Envelope(kind='TextMessage', ...)]

    await bus.stop()

asyncio.run(main())
```

### What happens under the hood

1. `bus.start()` opens the SQLite store, then calls `endpoint.start(handle)` for each registered endpoint in registration order.
2. After each `start()`, the bus drains any persisted-but-pending envelopes addressed to that endpoint.
3. `sender.send(...)` calls `handle.publish(envelope)`. The handle stamps `from_="sender"`, runs `pre_publish` hooks, persists the envelope, then dispatches synchronously to `receiver.deliver(envelope)`.
4. `receiver.deliver()` appends to `.inbox` and calls `handle.ack(envelope.id)`.
5. `bus.stop()` calls `endpoint.stop()` in reverse registration order, then closes the store.

## Writing a custom endpoint

Implement the three protocol methods. You do not need to inherit from any base class — structural typing is enough.

```python
from agent_core.bus.handle import BusHandle
from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import EndpointUnavailable

class LoggingEndpoint:
    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: BusHandle | None = None

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        print(f"[{self.name}] received {envelope.kind!r} from {envelope.from_!r}")
        assert self._handle is not None
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        self._handle = None
```

!!! tip "Long-running work"
    `deliver()` is awaited before the bus dispatches to any other endpoint. If your handler does I/O or model calls, start a background task, ack immediately, and publish a follow-up envelope when the work is done.

## Error signalling

| What you raise | Bus behaviour |
|---|---|
| `EndpointUnavailable` | Envelope requeued; bus retries on backoff |
| Any other exception | Envelope moved to dead-letter; error logged |

Import `EndpointUnavailable` from `agent_core.bus.protocol`.

## Next steps

- [Running the daemon](daemon.md) — run a supervised multi-endpoint process from `agent_core.yaml`
- [Concepts — Bus](../concepts/bus.md) — envelope lifecycle, hooks, delivery guarantees
