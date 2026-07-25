# The Bus

The bus (`agent_core.bus.core.Bus`) is the heart of agent-core: an in-process async router that connects every participant in the system. Its job is to accept an envelope from a sender, write it durably, deliver it to the right endpoint, and confirm the outcome — all without the sender needing to care about whether the recipient is currently available.

## What the bus does

**Route.** Every endpoint registers under a unique name before the bus starts. When an envelope arrives, the bus looks up the registered endpoint by `envelope.to` and calls its `deliver()` method.

**Persist.** Before dispatching, the bus writes the envelope to a SQLite mailbox (`storage_path` in `BusConfig`). This means a crash between publish and ack does not lose the message: envelopes survive restarts and are replayed when the endpoint comes back online.

**Await acknowledgment.** Dispatch is synchronous per envelope: the bus awaits `deliver()` before moving on. The endpoint must eventually call `bus.ack(envelope_id)` to confirm handling. Until it does, the envelope remains in an in-flight state in the mailbox.

**Retry.** A redelivery sweep runs periodically (`redelivery_sweep_seconds`). Any envelope that has been in-flight longer than `redelivery_timeout_seconds` is either requeued for another attempt or moved to dead-letter once `max_delivery_attempts` is exhausted.

**TTL sweep.** Envelopes can carry an `expires_at` timestamp. A separate TTL sweep loop (`ttl_sweep_seconds`) marks expired-and-undelivered envelopes as `expired` so they never get delivered after their window closes.

**Dead-letter.** An envelope ends up dead-lettered if the endpoint raises a non-`EndpointUnavailable` exception during `deliver()`, if it exceeds `max_delivery_attempts`, or if a `pre_deliver` hook drops it. Dead-lettered envelopes stay in the mailbox for inspection; they are not retried.

**Supervise endpoints.** The supervisor layer monitors endpoint health. If an endpoint raises during `start()` or repeatedly fails delivery, the supervisor backs off (exponential with jitter) and eventually quarantines the endpoint before probing it again. See [Endpoints](endpoints.md) for how this interacts with the deliver/ack contract.

## Delivery lifecycle

```
publish()
  │
  ├─ pre_publish hooks (may mutate or drop)
  │
  ├─ insert into SQLite mailbox (status: pending)
  │
  └─ dispatch()
       │
       ├─ pre_deliver hooks (may mutate or drop → dead-letter if dropped)
       │
       ├─ mark in_flight (timeout = now + redelivery_timeout_seconds)
       │
       └─ endpoint.deliver(envelope)
            │
            ├─ endpoint calls ack()  → status: acked  ✓
            │
            ├─ raises EndpointUnavailable → status: pending (requeue)
            │
            └─ raises other exception  → status: dead_letter
```

Redelivery sweep requeues envelopes that stay in-flight past the timeout; a separate TTL sweep expires envelopes past their `expires_at`.

## Bus hooks

The bus has two pipeline stages where hooks can intercept envelopes:

- **`pre_publish`** — runs before persistence. A hook can mutate (e.g., stamp metadata) or drop (return `None`) the envelope before it is written to the mailbox.
- **`pre_deliver`** — runs just before `endpoint.deliver()` is called. A hook that drops an envelope here causes it to move directly to dead-letter (it was already persisted).

## Why SQLite?

SQLite gives the bus durability with zero external dependencies. The mailbox is a single file on disk. Envelopes written before a crash are there when the process restarts; the bus drains them per endpoint as each endpoint comes online. The schema uses WAL mode for concurrent reads during tailing and audit queries.

## Configuration knobs

`BusConfig` controls the bus-level tuning; `SupervisorConfig` (nested at `BusConfig.supervisor`) controls the supervision layer. Conceptually:

| Area | What you tune |
|---|---|
| Storage | `storage_path`, `acked_retention_days` |
| Delivery | `redelivery_timeout_seconds`, `max_delivery_attempts`, `max_pending_per_endpoint` |
| Sweep cadence | `ttl_sweep_seconds`, `redelivery_sweep_seconds` |
| Restart backoff | `restart_backoff_base_seconds`, `restart_backoff_factor`, `restart_backoff_cap_seconds`, `restart_jitter` |
| Quarantine | `restarts_before_quarantine`, `probe_interval_seconds` |
| Circuit breaker | `delivery_backoff_base_seconds/factor/cap`, `deliver_failures_before_breaker` |

For the complete key list with types and defaults, see [Bus config keys](../reference/bus-config.md).

!!! note "Fan-out is atomic"
    When you publish to multiple recipients (via `BusHandle.publish(envelope, to=[...])`) the bus pre-validates all recipients and mailbox capacities before writing any envelope. Either all recipients accept the envelope, or none do.
