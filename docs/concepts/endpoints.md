# Endpoints & Supervision

An endpoint is any addressable participant on the bus. It might be an AI agent, a Discord adapter, a job scheduler, or a test stub — what matters is that it has a registered name and implements three async methods. The bus calls those methods; the endpoint does whatever its job is and signals back.

## The protocol

```python
class Endpoint(Protocol):
    name: str

    async def start(self, bus: BusHandle) -> None: ...
    async def deliver(self, envelope: Envelope) -> None: ...
    async def stop(self) -> None: ...
```

**`start(bus)`** is called once when the bus starts, after the endpoint is registered. The `BusHandle` argument is the endpoint's identity-bound view of the bus (see below). Open connections, start background loops, and subscribe to external sources here. Pending envelopes addressed to this endpoint are drained and dispatched immediately after `start()` returns.

**`deliver(envelope)`** is called by the bus for each envelope addressed to this endpoint. The bus awaits this call, so it must return promptly. Long-running work should be handed off to a background task; `deliver()` should return after acking, then publish a `Progress` or reply envelope when the work completes.

**`stop()`** is called in reverse-registration order during a graceful shutdown. Close connections and flush state here.

## The deliver/ack contract

The `deliver()` method has an important invariant: **it must eventually call `bus.ack(envelope.id)`**. Until ack is called, the envelope remains in-flight in the SQLite mailbox. If the process crashes before ack, the redelivery sweep will redeliver the envelope when the process restarts.

```python
async def deliver(self, envelope: Envelope) -> None:
    # Return promptly: hand off to background work, ack immediately.
    asyncio.create_task(self._handle(envelope))

async def _handle(self, envelope: Envelope) -> None:
    try:
        # ... do the work ...
        await self._bus.ack(envelope.id)
    except Exception:
        await self._bus.nack(envelope.id, requeue=True)
```

## Signaling failure

Two outcomes are possible when `deliver()` raises instead of completing normally:

**`EndpointUnavailable`** signals a temporary failure. The bus requeues the envelope (status becomes `pending`) and it will be retried on the next redelivery sweep. Use this when the endpoint is temporarily unable to accept work — for example, a downstream service is unreachable. The supervisor also uses this signal as input to the circuit breaker (see below).

**Any other exception** is terminal. The envelope moves to dead-letter and will not be retried. Use this for programming errors, malformed payloads, or any case where retrying would be futile.

!!! warning "deliver() blocks the dispatch loop"
    The bus awaits `deliver()` before dispatching the next envelope. An endpoint that blocks in `deliver()` for a long time will stall all other endpoints. Return quickly; do the work in a background task.

## BusHandle — identity-bound publishing

Each endpoint receives a `BusHandle` bound to its name at `start()` time. This is the only surface through which an endpoint should interact with the bus:

- **`handle.publish(envelope, to=...)`** — send an envelope. The bus stamps `from_` to this endpoint's name automatically.
- **`handle.ack(envelope_id)`** — mark an envelope handled. Idempotent.
- **`handle.nack(envelope_id, requeue=True)`** — reject an envelope; requeue it or dead-letter it.
- **`handle.endpoints()`** — list currently registered endpoints (name + description snapshot).

The identity binding is a security property: an endpoint cannot publish on behalf of another endpoint, regardless of what it sets in the envelope fields.

## EndpointSpec — registering with a description

Endpoints are registered via `EndpointSpec(endpoint=..., description="...")`. The description is surfaced in the `handle.endpoints()` directory listing, which agents use to discover what participants are available on the bus.

## Supervision

The bus supervisor monitors endpoint health so the system is self-healing rather than fail-stop.

**Restart backoff.** If an endpoint's `start()` raises, or if the supervisor determines an endpoint needs to be restarted, it waits before retrying. The wait follows exponential backoff: starting at `restart_backoff_base_seconds`, multiplying by `restart_backoff_factor` on each failure, capped at `restart_backoff_cap_seconds`. Jitter (full, equal, or none) spreads restarts so endpoints do not thunderherd after a shared dependency recovers.

**Quarantine.** After `restarts_before_quarantine` consecutive restart failures, the supervisor stops retrying immediately and instead probes the endpoint every `probe_interval_seconds`. This prevents a broken endpoint from burning CPU in a tight restart loop.

**Circuit breaker.** A separate circuit breaker tracks delivery failures. After `deliver_failures_before_breaker` consecutive failures, the breaker opens: the bus pauses delivery to that endpoint and applies `delivery_backoff_base_seconds/factor/cap` before retrying. When a delivery succeeds, the breaker resets.

The combination means an endpoint that is temporarily unavailable (raises `EndpointUnavailable`) degrades gracefully: the bus backs off delivery, the envelopes stay safely in the mailbox, and delivery resumes when the endpoint recovers — without operator intervention.

For exact `SupervisorConfig` field names and defaults, see the [API Reference](../reference/index.md).
