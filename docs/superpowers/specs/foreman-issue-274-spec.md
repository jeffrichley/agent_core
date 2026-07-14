# Spec: delivery retry backoff (`next_attempt_at`) for transient endpoint failures (issue #274)

## Goal

Add per-message delivery backoff so a fast-failing endpoint cannot burn through all `max_delivery_attempts` (5) instantly. A new `next_attempt_at` timestamp column gates when a pending envelope is eligible for re-dispatch. `Bus._dispatch` sets it on every `EndpointUnavailable` using the `delivery_backoff_*` knobs already present on `SupervisorConfig` (landed in T1 / issue #270). The redelivery sweep respects this gate when choosing which pending envelopes to re-dispatch. Terminal failures still dead-letter immediately; `max_delivery_attempts` is unchanged.

Issue: https://github.com/jeffrichley/agent_core/issues/274
Design spec: `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md` (§ "Delivery retry backoff", ticket slice 5)

## Acceptance criteria

- `next_attempt_at TIMESTAMP` column added to the `envelopes` table via a forward migration; existing rows default to NULL (meaning "due now").
- `Persistence.requeue_with_backoff(id_, next_attempt_at)` method sets `state='pending', in_flight_until=NULL, next_attempt_at=?`.
- `Persistence.requeue(id_)` (plain, no backoff) also clears `next_attempt_at=NULL` so explicit nacks are immediately re-eligible.
- `Persistence.reset_for_replay` also sets `next_attempt_at=NULL`.
- `Persistence.list_pending(endpoint, *, now=None)` — when `now` is provided, adds `AND (next_attempt_at IS NULL OR next_attempt_at <= ?)` filter; when `now=None` (unchanged default), returns all pending rows.
- `Bus` accepts an optional `clock: Clock | None = None` kwarg; the clock is stored as `self._clock` and also passed to `Persistence(…, clock=self._clock)` in `Bus.start()`.
- `_compute_delivery_backoff(attempt, config)` module-level function in `core.py` returns `random.uniform(0, min(cap, base * factor**(attempt-1)))`.
- On `EndpointUnavailable` in `Bus._dispatch`: read the row to get `delivery_count` (already incremented by `mark_in_flight`); if `delivery_count >= max_delivery_attempts`, dead-letter instead of requeueing; otherwise call `requeue_with_backoff(id_, clock.now() + timedelta(seconds=_compute_delivery_backoff(delivery_count, supervisor)))`.
- `Bus.run_redelivery_sweep_once` modified: Part 1 (stale in-flight) calls `requeue_with_backoff` (backoff computed from `delivery_count` already in the row) instead of `requeue + _dispatch`; Part 2 (new) iterates all registered endpoints, calls `list_pending(ep, now=now)` for each, and dispatches every due-pending envelope.
- A transient-failing endpoint retries at increasing (jittered, capped) intervals rather than instantly; still dead-letters after `max_delivery_attempts`.
- The sweep skips envelopes whose `next_attempt_at` is strictly in the future (relative to the `now` passed to the sweep).
- Terminal (non-`EndpointUnavailable`) failures still dead-letter immediately on first failure.
- Unit tests use `FakeClock` / `now=` parameters — no real sleeps.

## Approach

No GoF pattern applies — these are targeted additions to two existing code paths (requeue in `_dispatch`, dispatch-gating in the sweep) plus a storage-layer column. The relevant principle is **SRP**: `Persistence` owns the column's SQL semantics; `Bus` owns the business rule (when and how to compute the delay). Neither layer crosses into the other's concern.

### Persistence layer (`persistence.py`)

**Schema change**: add `next_attempt_at TIMESTAMP` to `_SCHEMA` after `in_flight_until`. SQLite's `CREATE TABLE IF NOT EXISTS` will include it for new databases; the migration block handles existing ones.

**Migration**: use the same PRAGMA-driven, idempotent pattern as the existing `urgency` migration (lines 99–106): read `PRAGMA table_info(envelopes)` into a set of column names, and `ALTER TABLE envelopes ADD COLUMN next_attempt_at TIMESTAMP` only when the column is absent. NULL is the implicit default for `ALTER TABLE ADD COLUMN`, which satisfies "existing rows default to due now."

**`requeue_with_backoff(id_, next_attempt_at: datetime)`** — new method alongside the existing `requeue`:
```python
async def requeue_with_backoff(self, id_: str, next_attempt_at: datetime) -> None:
    conn = self._require_conn()
    await conn.execute(
        """UPDATE envelopes
           SET state = 'pending',
               in_flight_until = NULL,
               next_attempt_at = ?
           WHERE id = ?""",
        (next_attempt_at.isoformat(), id_),
    )
    await conn.commit()
```

**`requeue(id_)`** — update to clear `next_attempt_at`:
```python
async def requeue(self, id_: str) -> None:
    conn = self._require_conn()
    await conn.execute(
        "UPDATE envelopes SET state = 'pending', in_flight_until = NULL, next_attempt_at = NULL WHERE id = ?",
        (id_,),
    )
    await conn.commit()
```
This ensures that when `Bus._nack(…, requeue=True)` is called (explicit endpoint nack), the envelope is immediately eligible — no inherited backoff.

**`reset_for_replay`** — add `next_attempt_at = NULL` to the SET clause so a replayed dead-letter starts fresh.

**`list_pending(endpoint, *, now=None)`** — when `now` is provided, the WHERE clause becomes:
```sql
WHERE to_endpoint = ? AND state = 'pending'
  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
ORDER BY created_at ASC
```
When `now` is None, the original WHERE clause is used (no time filter). This is intentional: `drain_for` at startup calls `list_pending` without `now` to drain all persisted mail regardless of any pre-crash backoff timestamps — the right behavior for a freshly started endpoint.

### Core layer (`core.py`)

**`_compute_delivery_backoff(attempt: int, config: SupervisorConfig) -> float`** — module-level function (not a method) for easy unit-testing:
```python
def _compute_delivery_backoff(attempt: int, config: SupervisorConfig) -> float:
    """Full-jitter exponential backoff for message delivery retries.

    attempt: delivery_count after the failed attempt (1-indexed).
    Returns: seconds to wait before the next delivery attempt.
    """
    cap = config.delivery_backoff_cap_seconds
    raw = config.delivery_backoff_base_seconds * (config.delivery_backoff_factor ** (attempt - 1))
    return random.uniform(0, min(raw, cap))
```

With the default config (base=2, factor=2, cap=60): attempt 1 → [0, 2s]; attempt 2 → [0, 4s]; attempt 3 → [0, 8s]; attempt 4 → [0, 16s]; attempt 5 is dead-lettered (not requeued).

**New imports in `core.py`**:
```python
import random
from agent_core.clock import Clock, SystemClock
```

**`Bus.__init__`** gains `clock: Clock | None = None`:
```python
def __init__(self, config: BusConfig, *, clock: Clock | None = None):
    self.config = config
    self._clock: Clock = clock or SystemClock()
    ...
```

**`Bus.start()`** passes clock to `Persistence`:
```python
self._store = Persistence(self.config.storage_path, clock=self._clock)
```

**`Bus._dispatch` — EndpointUnavailable handler** (currently lines 286–295):
```python
if isinstance(exc, EndpointUnavailable):
    row = await store.row(envelope.id)
    attempt = row["delivery_count"]  # already incremented by mark_in_flight
    if attempt >= self.config.max_delivery_attempts:
        await store.mark_dead_letter(
            envelope.id,
            reason=f"exceeded {self.config.max_delivery_attempts} delivery attempts (transient)",
        )
        log.info(
            "endpoint %s transient failure; exceeded %d attempts; dead-lettering %s",
            envelope.to, self.config.max_delivery_attempts, envelope.id,
        )
    else:
        backoff_secs = _compute_delivery_backoff(attempt, self.config.supervisor)
        next_attempt_at = self._clock.now() + timedelta(seconds=backoff_secs)
        await store.requeue_with_backoff(envelope.id, next_attempt_at)
        log.info(
            "endpoint %s unavailable (attempt %d); envelope %s requeued until %s: %s",
            envelope.to, attempt, envelope.id, next_attempt_at.isoformat(), exc,
        )
```

**`Bus.run_redelivery_sweep_once` — Part 1 change**: replace `await self._store.requeue(env.id)` + `await self._dispatch(env)` with:
```python
backoff_secs = _compute_delivery_backoff(row["delivery_count"], self.config.supervisor)
next_attempt_at = now + timedelta(seconds=backoff_secs)
await self._store.requeue_with_backoff(env.id, next_attempt_at)
```
(The `now` variable is already established at the top of `run_redelivery_sweep_once`; `row` is already read a few lines above in the existing code.)

**`Bus.run_redelivery_sweep_once` — Part 2 (new)**: after the Part 1 loop closes, add:
```python
# Part 2: dispatch pending envelopes whose backoff has elapsed.
for endpoint_name in self._endpoints_by_name:
    try:
        due = await self._store.list_pending(endpoint_name, now=now)
        for env in due:
            await self._dispatch(env)
    except Exception:
        log.exception("delivery backoff sweep error for endpoint %s; skipping", endpoint_name)
```

Part 2 piggybacks on the existing redelivery sweep loop (runs every `redelivery_sweep_seconds`, default 10 s). No new sweep loop or config knob is needed — YAGNI. Any envelope with a backoff ≤ cap (60 s) will be retried within the next sweep cycle.

### Why `drain_for` is unchanged

`drain_for` calls `list_pending` without `now`, getting all pending regardless of `next_attempt_at`. This is intentional for two scenarios: (1) initial startup drain, where all pre-existing pending mail should be dispatched; (2) T4 degraded boot calling `drain_for` after an endpoint recovers — the endpoint just came back, so immediately retrying is correct. The sweep's time-gate is the primary enforcement mechanism, not `drain_for`.

### Clock injection rationale

`Bus._dispatch` cannot accept a `now=` parameter (it's an internal method called from many places without a clock context). Adding `self._clock` to `Bus` is the minimal, consistent approach — it mirrors the existing `Persistence(…, clock=clock)` seam already used in test suite fixtures.

## Sub-requests (topologically sorted)

1. **Add `next_attempt_at` to schema + migration** (`persistence.py`): add the column to the `_SCHEMA` string after `in_flight_until`; add the PRAGMA-driven migration block in `connect()` after the existing urgency block; update `requeue()` to also set `next_attempt_at=NULL`; update `reset_for_replay` to also set `next_attempt_at=NULL`.
2. **Add `requeue_with_backoff` + update `list_pending`** (`persistence.py`): new `async def requeue_with_backoff(self, id_: str, next_attempt_at: datetime) -> None`; add `now: datetime | None = None` kwarg to `list_pending` and add the conditional WHERE clause.
3. **Add imports + clock seam + `_compute_delivery_backoff`** (`core.py`): add `import random` and `from agent_core.clock import Clock, SystemClock`; add `_compute_delivery_backoff` module-level function; add `clock: Clock | None = None` kwarg to `Bus.__init__`; pass `clock=self._clock` to `Persistence(…)` in `Bus.start()`.
4. **Wire backoff into `Bus._dispatch`** (`core.py`): replace the `EndpointUnavailable` handler's `await store.requeue(envelope.id)` with the row-read + max-attempt check + `requeue_with_backoff` block.
5. **Wire backoff into `Bus.run_redelivery_sweep_once`** (`core.py`): Part 1 uses `requeue_with_backoff`; add Part 2.
6. **Write persistence tests** (add to `packages/core/tests/bus/test_persistence.py` and add new `packages/core/tests/bus/test_persistence_backoff_migration.py`): `requeue_with_backoff` stores the timestamp; `list_pending(ep, now=past)` excludes future-backoff rows; `list_pending(ep, now=future)` includes them; `list_pending(ep)` (no now) returns all; `requeue` clears `next_attempt_at`; migration adds the column idempotently.
7. **Write core backoff tests** (new `packages/core/tests/bus/test_core_delivery_backoff.py`): `_compute_delivery_backoff` pure-function coverage; dispatch sets `next_attempt_at` on transient failure; dispatch dead-letters at max attempts; sweep Part 1 sets backoff and does not immediately re-dispatch; sweep Part 2 dispatches due-pending and skips future-backoff pending; terminal failure still dead-letters immediately.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/persistence.py` | Add `next_attempt_at TIMESTAMP` to `_SCHEMA`; migration block in `connect()`; update `requeue()` to clear `next_attempt_at`; update `reset_for_replay` to clear `next_attempt_at`; add `requeue_with_backoff(id_, next_attempt_at)`; add `now` kwarg to `list_pending` with conditional WHERE clause |
| `packages/core/src/agent_core/bus/core.py` | Add `import random` and `from agent_core.clock import Clock, SystemClock`; add `_compute_delivery_backoff(attempt, config)` module-level function; add `clock` kwarg to `Bus.__init__`; pass `clock=self._clock` to `Persistence` in `Bus.start()`; replace `EndpointUnavailable` handler in `_dispatch`; replace Part 1 requeue + dispatch in `run_redelivery_sweep_once`; add Part 2 to `run_redelivery_sweep_once` |
| `packages/core/tests/bus/test_persistence.py` | Add: `test_requeue_with_backoff_stores_timestamp`, `test_list_pending_filters_future_next_attempt_at`, `test_list_pending_includes_past_next_attempt_at`, `test_list_pending_no_now_returns_all`, `test_requeue_clears_next_attempt_at` |
| `packages/core/tests/bus/test_persistence_backoff_migration.py` | **New file**: `test_next_attempt_at_migration_adds_column` (creates a legacy DB without the column, connects, asserts column present and existing rows readable); `test_next_attempt_at_migration_idempotent` |
| `packages/core/tests/bus/test_core_delivery_backoff.py` | **New file**: see test list in sub-requests item 7 |

No other files change. Existing `test_core_sweeps.py` tests still pass: `test_stale_in_flight_within_attempt_limit_requeues` asserts `state in ("pending", "in_flight")` — the new behavior leaves state as `pending` after Part 1 (no immediate re-dispatch), and Part 2 skips it because `next_attempt_at` is at or after `now`; the assertion holds either way. `test_exhausted_attempts_dead_letter` is unaffected: delivery_count ≥ max → dead-letter path unchanged.

## Alternatives considered

1. **Add a third sweep loop (`run_delivery_backoff_sweep_once`)** — a dedicated sweep just for dispatching due-pending envelopes, with its own `sweep_seconds` knob. Ruled out: YAGNI. The redelivery sweep already runs every 10 s (far within any backoff interval up to 60 s cap); piggy-backing Part 2 costs no extra config surface and keeps the runner wiring unchanged.
2. **Filter `list_pending` always by wall-clock time (remove `now=None` fallback)** — simpler API, but breaks the `drain_for` semantics at startup: if a backoff was set before a crash and the bus restarts within that window, the envelopes would not be drained, leaving the endpoint idle until the sweep fires. The optional `now` parameter preserves the existing startup-drain behavior.
3. **Carry `delivery_count` through `_dispatch` rather than re-reading the row** — avoid an extra `await store.row(…)` on the `EndpointUnavailable` path by threading the count through the call. Ruled out: `_dispatch` doesn't know the count before `mark_in_flight` increments it, and reading from the DB after `mark_in_flight` is already consistent with how `run_redelivery_sweep_once` works. The extra row read is one small SQL lookup on an exceptional path; no measurable overhead.

## Open questions

None. The column name, migration pattern, backoff formula (full jitter, base=2, factor=2, cap=60), the two call sites, and the sweep Part 2 dispatch mechanism are all unambiguous from the issue and the design spec. `SupervisorConfig.delivery_backoff_*` fields are already present (T1 landed).

## Out of scope

- `EndpointSupervisor` circuit-breaker (T3) and degraded boot (T4): `Bus.start()` is not touched beyond passing `clock=self._clock` to `Persistence`.
- `BusHandle.spawn()` tracked-task API (T2): no changes to `handle.py`.
- ack-vs-nack fixes in inbound and discord endpoints (T6): those are a separate ticket (#275); T5 is independent.
- Adding a dedicated partial index on `(next_attempt_at, state)`: the existing `idx_envelopes_to_state` covers the per-endpoint time-gated query adequately for the expected mailbox sizes.
- Changing `max_delivery_attempts` or `redelivery_timeout_seconds` defaults.
- Changes to `drain_for` signature (it remains a single positional-arg method).
