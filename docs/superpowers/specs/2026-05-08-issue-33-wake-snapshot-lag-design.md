# Issue #33 — Wake-builder snapshot lag (Design)

> **Status:** Approved 2026-05-08. Ready for implementation plan.
>
> **Issue:** [#33](https://github.com/jeffrichley/agent_core/issues/33) — Wake-builder count + urgency_max snapshot lags actual queue state.
>
> **Roadmap:** RED tier of `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md`.

## Problem

Wake-channel notifications occasionally report `count` and `urgency_max` values that disagree with what `list_pending` returns moments later. Pepper has caught this multiple times per session: wake says `count=3`, `list_pending` returns 1; wake reports `urgency_max="yellow"` when every envelope in the queue is green. Envelope content is always correct — only the wake-builder's metadata snapshot is off.

Functionally harmless (the agent treats `list_pending` as authoritative), but corrosive: the agent suspects failures that don't exist, wastes turns investigating phantom backlogs, and learns to distrust the wake's urgency signal — defeating its purpose as a triage primitive.

### Root cause

The race is **not** inside the snapshot. `_build_summary()` (`packages/core/src/agent_core/endpoints/claude_code_mcp.py:558`) reads `self._pending` atomically at debounce-fire time. The seam is between the snapshot moment and the agent's next consumption moment, during which the agent is still draining a previous batch:

- `t=1.2`: debounce fires → snapshot reads `_pending=[e2,e3,e4]`, count=3.
- `t=1.2..1.3`: agent finishes processing e2, e3 from its prior `list_pending` batch, ack'd via `handle()`.
- `t=1.3`: agent receives the wake, calls `list_pending` → sees [e4], count=1.

This explains the "count=3, only 1 pending" sighting exactly.

The issue body's suggested fix shape (a) — recompute inside `list_pending`'s transaction — does not close this race. The wake is delivered *before* the agent's next `list_pending`; by then the queue has changed.

The fix shape that closes the race is **moving the metadata to the consumption point**: the wake notification carries no queue-state metadata; `list_pending` returns metadata alongside the envelopes it returns, computed atomically from the same `self._pending` read. Consumer (Pepper) confirmed this is the contract they want: zero added round-trip, urgency_max still available at the natural triage point.

## Out of scope

- No backward-compat shim. Pepper is the only consumer; clean cutover at daemon restart, validated on testbot first per the durable hands-off rule.
- No changes to envelope ordering, ack semantics, debounce timing, or persistence.
- No changes to `Bus.snapshot_for_agent`'s firing path — only its emitted shape.
- No changes to per-envelope urgency on individual envelopes.

## Design

### Wake notification — minimal contract

`notifications/claude/channel` `params` becomes:

```python
{
    "content": f"INBOX: pending ({endpoint})",
    "meta": {
        "endpoint": str,
        "fired_at": str,  # ISO-8601 UTC
    },
}
```

No `count`, `urgency_max`, `urgency_counts`, `by_sender`. The wake is a pure "go look" signal. `content` is a fixed, non-lying string referencing only the endpoint identity.

### `list_pending` — wrapped return shape

`list_pending(batch_window_seconds: int = 0)` returns:

```python
{
    "meta": {
        "count": int,
        "urgency_max": "red" | "yellow" | "green",  # "green" when count == 0
        "urgency_counts": {"red": int, "yellow": int, "green": int},
        "by_sender": [{"from": str, "count": int, "kinds": [str, ...]}, ...],
        "endpoint": str,
        "fetched_at": str,  # ISO-8601 UTC; semantically distinct from wake's fired_at
    },
    "items": [
        # When batch_window_seconds == 0: bare envelope dicts (today's behavior).
        # When > 0: {"type": "single", "envelope": {...}} or {"type": "batch", ...}.
    ],
}
```

`meta` and `items` are computed in the same synchronous block of `_call_list_pending` — no `await` between the read of `self._pending` and the construction of either field. Race-free by event-loop semantics: identical to the atomicity guarantee `_build_summary` had today.

`meta.count` counts envelopes (when batch entries are present, sums their inner envelopes — i.e., `meta.count == len(self._pending)` regardless of batching).

`meta.urgency_max` is `"green"` when `count == 0` (preserves current default; avoids forcing a None-check on the consumer).

### `Bus.snapshot_for_agent` — same minimal wake contract

The relay-connect synthetic wake follows the new wake shape: `{content, meta: {endpoint, fired_at}}`. One contract for everything wake-shaped — the relay-connect "freshness advantage" collapses the moment the agent reads the next envelope, so it's not worth a second contract. Forward-compatible: future wake sources (synthetic wakes from webhooks, inter-agent pushes) inherit the contract.

### `_build_summary` split

The current `_build_summary(urgency_floor: ...) -> dict` becomes two builders:

- `_build_wake_summary(self) -> dict` — emits the minimal wake shape. No parameters. No `urgency_floor`.
- `_build_list_pending_response(self, batch_window_seconds: int = 0) -> dict` — emits the wrapped `{meta, items}` shape. Called from `_call_list_pending`.

The `_debounce_urgency_floor` field and the `urgency_floor` argument plumbing through `_fire_after_debounce` are removed. Their only purpose was biasing the wake's `urgency_max` — which no longer exists on the wake.

The `snapshot()` public method (used by `Bus.snapshot_for_agent`) becomes a thin wrapper around `_build_wake_summary()`.

### Instructions / contract documentation

Two strings document the wake contract to the agent and need synchronization with the new shape:

- `claude_code_mcp.py` `__init__`'s `instructions=` kwarg passed to `FastMCP` (lines ~169-185).
- `agent_core_channel/stdio_server.py`'s wake-contract description string (lines ~36-42).

Both rewrite to reflect: "wake meta carries `endpoint` and `fired_at` only; call `list_pending` for authoritative queue state, including `meta.count` and `meta.urgency_max`."

## Edge cases

| Case | Behavior |
|---|---|
| Empty inbox at wake time | Wake fires anyway with the fixed content. Agent calls `list_pending` → `meta.count=0`, no items. No-op. |
| Two `list_pending` calls in quick succession | Each returns its own atomically-consistent `{meta, items}`. No state mutation. Safe. |
| `urgency_max` when `count == 0` | Returns `"green"`. Consistent with today's default; no None-check needed on consumer. |
| Daemon restart mid-cutover | Pre-restart: old contract. Post-restart: new contract. Consumer (testbot, then Pepper) must be parser-ready before the daemon flips. Same dance as #44 / #42. |
| Race during the debounce window | Eliminated by construction — wake carries no race-prone fields; `list_pending` reads atomically. |

## Testing

### Regression test (new)

In `test_notify_mail_arrived.py`: deterministic sequence reproducing the original bug shape.

1. Queue 3 envelopes via `queue_for_pickup`. Trigger a debounce.
2. Before the debounce fires, drain 2 via `handle()`.
3. Let the debounce fire.
4. Assert the wake notification's `meta` contains exactly `{"endpoint", "fired_at"}` — no `count`, no `urgency_max`. Assert `content` equals the fixed string.
5. Call `list_pending`. Assert `meta.count == 1`, `meta.urgency_max` matches the remaining envelope's urgency, `len(items) == 1`.

This is the test that would have failed under the current implementation and passes under the new one.

### Invariant test (new)

In `test_claude_code_mcp.py` (or a new `test_list_pending_meta_invariants.py`): parametrize over varied states (0/1/N envelopes, mixed urgencies, with and without `batch_window_seconds`). For each, call `list_pending` and assert:

- `meta.count == sum(envelopes per item)` (handles batching).
- `meta.urgency_max` equals the highest urgency across all envelopes in items (or `"green"` if empty).
- `meta.urgency_counts` per-tier sums match per-tier counts in items.
- `meta.by_sender` reconstructs from items.

Catches future drift if anyone ever computes meta separately from items.

### Drift-guard test (new)

A test asserting wake-meta keys are exactly `{"endpoint", "fired_at"}`. Same shape as the per-kind summarizer drift-guard from PR #49 (`packages/core/tests/test_bus_tail_summaries.py`). Cheap insurance against re-introducing `count` / `urgency_max` to the wake.

### Existing-test updates (mechanical)

- `test_notify_mail_arrived.py` — drop wake-meta assertions on `count` / `urgency_max` / `by_sender`. Keep wake-fire timing / cancel / coalesce assertions.
- `test_bus_snapshot_for_agent.py` — assert the new minimal snapshot shape.
- `test_bus_daemon_push_integration.py` — push-path assertions follow the new wake shape.
- `test_claude_code_mcp.py` + `test_claude_code_mcp_urgency_ordering.py` + `test_claude_code_mcp_batching.py` — wrap `list_pending` return assertions in the new shape; move urgency-ordering assertions to inspect `meta.urgency_max` and `items` order.
- `test_stdio_server.py` + `test_end_to_end_relay.py` — wake-shape assertions drop `count` / `urgency_max`.

## Components touched

**Source:**
- `packages/core/src/agent_core/endpoints/claude_code_mcp.py` — `_build_summary` split, `_call_list_pending` return shape, `snapshot()` wrapper, `_fire_after_debounce` simplification, `instructions` string.
- `packages/agent-core-channel/src/agent_core_channel/stdio_server.py` — wake-contract description string.
- `packages/agent-core-channel/README.md` — only if it references the old wake meta fields.

**Tests:** as enumerated above.

**Docs:**
- `docs/cutover/notification-surfaces.md` — sync to new wake contract if it documents meta fields.

## References

- Practice-run runbook: `docs/cutover/testbot-practice-run-2026-05-05.md` — Phase 6 "Bus-meta observation" + round-2 verification notes.
- Recent precedents for the cutover validation pattern: PR #46 (#44), PR #47 (#42), PR #48 (#39), PR #49 (#16).
- `_build_summary` current implementation: `packages/core/src/agent_core/endpoints/claude_code_mcp.py:558`.
- `_call_list_pending` current implementation: `packages/core/src/agent_core/endpoints/claude_code_mcp.py:389`.
