# Offload boundary + hardened `deliver()` contract — Design (#296)

**Ticket:** agent_core#296 (Theme A #264 · epic #262) · **Priority: P0 [M]**
**Date:** 2026-07-14
**Status:** approved design, pre-implementation

## Problem

The bus dispatches **strictly serially and globally**: `Bus._dispatch` does
`await endpoint.deliver(envelope)` (`bus/core.py`) and the `Endpoint` protocol
docstring is explicit — *"the bus awaits this call before dispatching the next
envelope to ANY endpoint."* One slow `deliver()` anywhere therefore stalls
delivery to every endpoint (scheduler, inbound, briefs, MCP), the structural
hazard behind the 2026-07-09 outage class.

The eval line-item names two culprits: dispatch awaited synchronously, and
`VoiceEndpoint` building its TTS backend synchronously in `__init__`. Reading
the current code refines this:

- **`deliver()` is already handled correctly by shipped endpoints.**
  `VoiceEndpoint.deliver()` spawns a background task and returns; the synthesis
  itself already runs via `asyncio.to_thread(...)` off the loop. `handoff_jobs`
  uses an internal queue + worker and returns after enqueue. The prompt-return
  contract is already *documented* in the protocol.
- **The one active bug is construction.** `VoiceEndpoint.__init__` builds
  `QwenTTSBackend` (a GPU model load) and warms voices **synchronously, on the
  event loop**, during construction/boot. *That* freezes.

So "offload boundary" splits into two problems with different answers:
**(A) heavy construction on the loop** — concrete, active, mechanical; and
**(B) a slow `deliver()` stalling the serial loop** — currently latent (all
shipped endpoints comply), defended only by prose today.

## Scope decision (approved)

**Fix construction + harden the contract. Do NOT re-architect the dispatch
loop.** Concurrent per-endpoint dispatch / per-`deliver()` timeout-and-cancel
(the "bus defends its own loop against any endpoint") is explicitly deferred to
its own ticket, to be opened only if the watchdog below produces evidence of a
real contract-violating endpoint. Serial dispatch and the existing
in-flight / ack / backoff / dead-letter semantics stay **exactly as-is**.

Rationale: the prompt-`deliver()` contract and the `BusHandle.spawn()` primitive
(T2a #290) already exist and every shipped endpoint complies; the active defect
is construction-time. A concurrency rewrite trades the current simple
serial-ordering + backpressure guarantee for defense against a hypothetical bad
endpoint — not justified without evidence.

## Design

### ① Endpoint protocol contract (`bus/protocol.py`)

Strengthen the `Endpoint` docstrings from advisory to **normative (MUST)**:

- `__init__` MUST be cheap — no model loads, blocking I/O, or network.
- Heavy or slow setup belongs in `start(hook)`, which is async and awaited
  during boot (and, with T4 #273, quarantinable on failure/timeout).
- `deliver()` MUST return promptly. Long work is offloaded to a tracked
  background task via `bus.spawn(...)` (the T2a primitive), and the endpoint
  acks (on enqueue or on completion, per its semantics) rather than awaiting
  the work inline.

No signature change — this codifies the existing intent so it is testable and
so adopter endpoints have an unambiguous rule.

### ② VoiceEndpoint construction fix (`agent-core-voice/src/agent_core_voice/endpoint.py`)

- **`__init__`**: store parameters only (`model_path`, config, voice registry
  spec, `backend`). Remove the `QwenTTSBackend(...)` construction and the
  `prepare_voice` voice-warming loop.
- **`start(hook)`**: build the backend off the loop thread —
  `self._backend = await asyncio.to_thread(QwenTTSBackend, ...)` — then warm the
  configured voices via `asyncio.to_thread(...)`. Boot still waits for this
  (start() is awaited), but the loop thread stays responsive and a slow/failed
  load is T4-quarantinable instead of a hard freeze. Backend is warm before the
  first request → predictable low first-synthesis latency; a broken model
  fails fast, visibly, at boot rather than on a user's first request.
- **`deliver()`**: unchanged (already spawns + `to_thread`). Add a defensive
  guard: if a request arrives before `start()` has set `_backend`, raise
  `EndpointUnavailable` so the bus requeues it (should not happen — start()
  completes before `drain_for` — but fail safe).
- **`for_test(backend=…)`**: the injected fake sets `_backend` directly, so
  `start()` sees a backend already present and skips construction. Tests stay
  fast, no model load.

### ③ Slow-`deliver()` watchdog (`bus/core.py _dispatch`)

Wrap the `await endpoint.deliver(envelope)` with a monotonic timer. If elapsed
exceeds `slow_deliver_warn_seconds`, emit a structured **`SlowDeliverWarning`**
event (`endpoint`, `envelope_id`, `elapsed_seconds`) through the event
bus / structured log.

**Warn-only.** It does not cancel, time out, or alter delivery semantics. It
turns a future contract-violating endpoint from an invisible stall into a
signal — observability that Theme E and T4's supervisor can escalate on later.

### ④ Config

Add `slow_deliver_warn_seconds` to the supervisor/bus config: default `5`,
`> 0` guard (a non-positive value disables the warning — mirrors the inactivity
watchdog's guard convention), env-overridable per the config-provenance chain.

## Non-goals (YAGNI / deferred)

- No concurrent per-endpoint dispatch; no per-`deliver()` timeout + cancel.
  (Deferred "bus self-defense" ticket, gated on watchdog evidence.)
- No process pool and no change to where synthesis runs — `VoiceEndpoint`
  already uses `asyncio.to_thread` correctly.
- No change to the in-flight-timeout redelivery sweep, ack model, or backoff.

## Testing

- **Construction:** `__init__` touches no backend — a spy/sentinel backend
  factory records construction and the test asserts it is NOT called until
  `start()`. After `start()`, `_backend` is present.
- **Off-thread proof:** a `start()` whose backend factory blocks (sleep) does
  not freeze the loop — a concurrent coroutine/timer still makes progress while
  `start()` runs, proving the load is off the loop thread.
- **`for_test` path:** injected fake backend → `start()` is a no-op, no
  construction attempted.
- **Watchdog:** a fake endpoint with a slow `deliver()` (exceeds threshold)
  emits exactly one `SlowDeliverWarning` with correct fields; a fast `deliver()`
  emits none; a non-positive `slow_deliver_warn_seconds` disables it.
- **Regression:** existing `VoiceEndpoint.deliver()` and bus dispatch/ack tests
  unchanged and green.

## Interfaces produced (for the blocked-on-this tickets)

This ticket establishes: heavy setup in `start()` off-thread; `deliver()`
prompt with long work in tracked `spawn()` tasks; and the `SlowDeliverWarning`
signal. Both downstream tickets **shrink** once this lands (they are
`blocked_by #296`):

- **#297 (async redelivery sweep):** the sweep re-dispatches inline, but under
  the enforced prompt-`deliver()` contract each `_dispatch` returns promptly, so
  the "one slow endpoint stalls redelivery for all mail" failure mode is
  largely removed. #297 likely reduces to applying the same `SlowDeliverWarning`
  timing to the sweep path (and is moot if the contract holds).
- **#300 (graceful drain of in-flight `deliver()`s):** with `deliver()` prompt
  and real work in tracked `spawn()` tasks — which `Bus.stop()` already drains
  (T2a #290) — "draining in-flight `deliver()`s" reduces to confirming the
  tracked-task drain covers the work, plus optionally awaiting the (now brief)
  in-flight `deliver()` calls.

Re-plan #297 and #300 after #296 merges; expect both smaller than their current
bodies imply.
