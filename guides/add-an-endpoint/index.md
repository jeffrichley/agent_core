# Add an Endpoint

An **endpoint** is any participant on the bus — a Discord adapter, an AI agent's MCP surface, a stub for tests, or a custom adapter you write. This guide walks through implementing the `Endpoint` protocol, registering it with the daemon, and handling the deliver/ack contract correctly.

Sources verified: `packages/core/src/agent_core/bus/protocol.py`, `packages/core/src/agent_core/endpoints/stub.py`, `packages/core/docs/plugins.md`.

______________________________________________________________________

## Step 1 — Implement the protocol

Every endpoint satisfies three async methods. No base class is needed — structural typing is enough.

```
# my_package/endpoints/greeter.py
from agent_core.bus.handle import BusHandle
from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import EndpointUnavailable


class GreeterEndpoint:
    """A minimal endpoint that logs every TextMessage it receives."""

    def __init__(self, name: str, prefix: str = "Hello") -> None:
        self.name = name          # required: the bus routes by this name
        self._prefix = prefix
        self._handle: BusHandle | None = None

    async def start(self, bus: BusHandle) -> None:
        """Bus is ready. Store the handle; open connections; start background loops."""
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        """An envelope addressed to this endpoint has arrived.

        Rules:
        - MUST call self._handle.ack(envelope.id) when handling completes.
        - Raise EndpointUnavailable for a temporary failure — the bus will
          requeue and retry on backoff.
        - Any other exception dead-letters the envelope.
        - Return promptly. Long work goes in a background task.
        """
        assert self._handle is not None
        try:
            if envelope.kind == "TextMessage":
                text = envelope.payload.text  # type: ignore[union-attr]
                print(f"{self._prefix} from {envelope.from_!r}: {text}")
        except OSError as exc:
            # Transient I/O failure — bus will retry.
            raise EndpointUnavailable(str(exc)) from exc

        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        """Graceful shutdown. Close connections, flush state."""
        self._handle = None
```

Return promptly from deliver()

The bus awaits `deliver()` before dispatching to any other endpoint. For slow work — model calls, network I/O, heavy computation — ack immediately and hand off to a tracked background task via `handle.spawn()`. `spawn()` wraps `asyncio.create_task()` with task registration and failure routing so exceptions are not silently lost.

```
async def deliver(self, envelope: Envelope) -> None:
    assert self._handle is not None
    # Ack first so the bus can move on; do the slow work in the background.
    await self._handle.ack(envelope.id)
    self._handle.spawn(
        self._process(envelope),
        name=f"greeter-process-{envelope.id[:8]}",
    )

async def _process(self, envelope: Envelope) -> None:
    """Slow work goes here — model calls, HTTP requests, etc."""
    if envelope.kind == "TextMessage":
        text = envelope.payload.text  # type: ignore[union-attr]
        print(f"{self._prefix} (processed): {text}")
```

Use `asyncio.create_task()` only if you are inside a method that does not have access to the `BusHandle` (e.g. a utility helper). For endpoint `deliver()` implementations, always prefer `handle.spawn()`.

### Error table

| What you raise        | Bus behaviour                                         |
| --------------------- | ----------------------------------------------------- |
| `EndpointUnavailable` | Envelope requeued; bus retries on exponential backoff |
| Any other exception   | Envelope moved to dead-letter; error logged           |
| Nothing (ack called)  | Envelope marked acked; delivery complete              |

______________________________________________________________________

## Step 2 — Wire it in `agent_core.yaml`

The daemon reads `agent_core.yaml` and instantiates each endpoint by `type:`. Built-in type aliases ship with the core package (e.g. `builtin.stub`). Your own endpoint types are registered via a plugin (Step 3).

```
# agent_core.yaml
bus:
  storage_path: "~/.agent-core/bus.sqlite"

http:
  bind_host: "127.0.0.1"
  bind_port: 8788

endpoints:
  - type: my_package.greeter       # resolved via your plugin's register_endpoint_types()
    name: greeter
    params:
      prefix: "Greetings"
```

The `name:` field becomes the bus routing address — envelopes addressed `to: greeter` land in `GreeterEndpoint.deliver()`. The `params:` dict is passed as keyword arguments to `__init__` (after `name`).

Config-driven production path

In production the daemon runner owns construction, persistence, and lifecycle. Direct `Bus` construction is for tests and one-off scripts — see [Getting Started](https://jeffrichley.github.io/agent_core/getting-started/first-agent/index.md) for that pattern.

______________________________________________________________________

## Step 3 — Register the type via a plugin

The daemon resolves `type:` strings through a plugin registry. Register your endpoint class by contributing a `register_endpoint_types()` hookimpl:

```
# my_package/agent_core_plugin.py
import pluggy

hookimpl = pluggy.HookimplMarker("agent_core")


@hookimpl
def register_endpoint_types() -> dict:
    from my_package.endpoints.greeter import GreeterEndpoint
    return {"my_package.greeter": GreeterEndpoint}
```

Then declare the entry point in `pyproject.toml`:

```
[project.entry-points."agent_core"]
my_plugin = "my_package.agent_core_plugin"
```

After `pip install -e .` (or a wheel install), `uv run agent-core daemon start` will discover your plugin and resolve the `my_package.greeter` type.

______________________________________________________________________

## Step 4 — Run it

```
uv run agent-core daemon start
```

The daemon starts the bus, calls `endpoint.start(handle)` for each registered endpoint in order, drains any persisted-but-pending envelopes, then enters its run loop. On shutdown it calls `endpoint.stop()` in reverse order.

______________________________________________________________________

## Using StubEndpoint for tests

`agent_core.endpoints.stub.StubEndpoint` is a ready-made in-memory implementation for tests. It auto-acks, records every delivered envelope on `.inbox`, and provides a `.send()` helper to publish from its own identity.

```
import asyncio
from pathlib import Path
from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.endpoints.stub import StubEndpoint

async def test_delivery() -> None:
    sender = StubEndpoint(name="sender")
    receiver = StubEndpoint(name="receiver", auto_ack=True)

    bus = Bus(BusConfig(storage_path=Path("/tmp/test.sqlite")))
    bus.register(EndpointSpec(endpoint=sender))
    bus.register(EndpointSpec(endpoint=receiver))
    await bus.start()

    await sender.send(
        to="receiver",
        kind="TextMessage",
        payload={"kind": "TextMessage", "text": "ping"},
    )

    assert len(receiver.inbox) == 1
    assert receiver.inbox[0].kind == "TextMessage"

    await bus.stop()

asyncio.run(test_delivery())
```

See [Send and consume envelopes](https://jeffrichley.github.io/agent_core/guides/send-and-consume/index.md) for the full envelope field reference.

______________________________________________________________________

## Next steps

- [Send and consume envelopes](https://jeffrichley.github.io/agent_core/guides/send-and-consume/index.md) — the full publish/ack/nack cycle
- [Write an extension](https://jeffrichley.github.io/agent_core/guides/write-an-extension/index.md) — package layout and the full pluggy recipe
- [Concepts — Endpoints](https://jeffrichley.github.io/agent_core/concepts/index.md) — delivery guarantees, supervision, circuit-breaking
