# agent-core Theme A — Runtime resilience & supervision (slice 1)

**Epic:** #262 · **Theme:** #264 · **Date:** 2026-07-13
**Status:** design approved (Jeff), ready to slice into foreman sub-tickets of #264.

## Goal

Add an **in-process supervision layer** to the bus so that one endpoint's failure is isolated, contained, retried, auto-recovered, and surfaced — instead of taking every being down (the 2026-07-09 outage class). This is the foreman-buildable slice; the OS-level process supervisor + tray icon (survives whole-process death) are separate paired work under #265.

## Scope

**In:** per-endpoint failure isolation, degraded boot, per-endpoint circuit-breaker restart, per-message delivery retry backoff, ack-vs-nack fix, fire-and-forget task tracking, structured degraded-state event + queryable status.

**Out (deferred):**
- Active liveness/heartbeat detection of a *silently-wedged* (not-crashed) endpoint → Theme E (#268); the supervisor is built to consume such a signal later.
- Human-visible surfacing (icon badge, OS toast, Discord push) → the tray icon (#265) and any future subscriber consume the event this slice emits. The supervisor itself has **no** notification-channel coupling.
- OS-level "restart whole process on death" supervisor → #265 (paired).

## Architecture

A central **`EndpointSupervisor`** owns one state record per endpoint:

```
EndpointState {
  name: str
  status: active | restarting | quarantined
  breaker: closed | open | half_open
  consecutive_failures: int
  last_error: str | None
  next_probe_at: datetime | None
}
```

Central registry (not a per-endpoint self-supervising wrapper) so `bus status` and future consumers (tray icon) read all health from one place.

### Failure signals (crash-driven, this slice)
1. An endpoint's `start()` raising (at boot or on restart).
2. An endpoint's **supervised background task** raising — via a new tracked-task API on `BusHandle` (replaces today's leaky `create_task`).
3. A run of **5 consecutive non-transient `deliver()` failures** on one endpoint (conservative; tunable; drop-able if it proves false-positive-prone).

### Circuit-breaker state machine (per endpoint)
- **CLOSED** (healthy) — normal operation.
- On failure → restart (`stop()`→`start()`) with exponential backoff + full jitter; `status=restarting`.
- After **`restarts_before_quarantine`** consecutive failed restarts → **OPEN** (`status=quarantined`): stop retrying; mail addressed to it stays persisted/pending; emit `EndpointStateChanged(quarantined)`.
- After **`probe_interval`** → **HALF_OPEN**: one restart attempt. Success → CLOSED, `drain_for` pending mail, emit `EndpointStateChanged(recovered)`. Failure → back to OPEN, reset probe timer.

### Degraded boot
Replace all-or-nothing `Bus.start()` (`core.py:130-144`): try each endpoint's `start()`+`drain_for`; on failure, quarantine that endpoint and continue — do **not** tear down the others. Bus comes up with whatever started. Zero endpoints started still comes up (degraded-empty) with a CRITICAL log.

### Delivery retry backoff (per-message, distinct from the per-endpoint breaker)
The requeue path (`core.py:224`, `:280`) currently re-dispatches with zero delay, so a fast-failing endpoint burns all `max_delivery_attempts` instantly. Add a `next_attempt_at` column; the redelivery sweep only re-dispatches an envelope once its backoff has elapsed. Transient (`EndpointUnavailable`) → backoff-requeue; terminal → dead-letter (unchanged). `max_delivery_attempts` stays 5.

### ack-vs-nack fix
Endpoints that currently **ack** transient failures (inbound acks all paths except `_handle is None`; discord acks on 429/5xx) switch to **nack-requeue / raise `EndpointUnavailable`**, so recoverable mail is not silently dropped.

### Fire-and-forget task tracking
Hoist the tracked-task pattern (already correct in `handoff_jobs`/`claude_code_mcp`) into a shared `BusHandle.spawn(coro)` helper that: registers the task, routes its unhandled exception into the supervisor's failure signal, and cancels it on `stop()`. Migrate the known leaks (inbound `_bus_publish_adapter`, voice synthesis tasks, discord typing/reaction tasks).

## Config — new `[supervisor]` block in `BusConfig` (all logged at boot)

| Knob | Default |
|---|---|
| `restart_backoff_base_seconds` | 1 |
| `restart_backoff_factor` | 2 |
| `restart_backoff_cap_seconds` | 60 |
| `restart_jitter` | full |
| `restarts_before_quarantine` | 5 |
| `probe_interval_seconds` | 300 |
| `delivery_backoff_base_seconds` | 2 |
| `delivery_backoff_factor` | 2 |
| `delivery_backoff_cap_seconds` | 60 |
| `deliver_failures_before_breaker` | 5 |
| `max_delivery_attempts` | 5 (existing, unchanged) |

## Testing

Pure-Python, cross-platform, fully unit-testable in Linux CI (foreman-provable):
- Circuit-breaker transitions driven by an injected fake clock (no real sleeps) — closed→restarting→open→half_open→closed and →open.
- Degraded boot: a fixture endpoint whose `start()` raises → bus still starts, that endpoint quarantined, others active, event emitted.
- Delivery backoff: `next_attempt_at` respected by the sweep; transient vs terminal routing.
- Tracked-task API: a spawned task that raises feeds the failure signal and is cancelled on stop.
- ack/nack: transient failure requeues (not acked) via strict fakes that mirror the real endpoints.

## Foreman ticket slices (sub-tickets of #264)

1. `[supervisor]` config block in `BusConfig` (+ boot log). *(foundational)*
2. `BusHandle.spawn()` tracked-task API + migrate known leaky sites.
3. `EndpointSupervisor` + circuit-breaker state machine (fake-clock unit tests). *(the core; depends on 1)*
4. Degraded boot + wire supervisor into `Bus.start()`/`stop()` + `EndpointStateChanged` event + `degraded` field in `bus status`. *(depends on 3)*
5. Delivery retry backoff (`next_attempt_at` + sweep respect + transient/terminal). *(persistence + core)*
6. ack-vs-nack fixes in inbound + discord endpoints.
