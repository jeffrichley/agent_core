# Spec: add `SupervisorConfig` block to `BusConfig` with boot logging (issue #270)

## Goal

Add a `SupervisorConfig` nested dataclass to `packages/core/src/agent_core/bus/core.py` holding the 10 supervision knobs listed in the issue, attach it to `BusConfig`, log the resolved values once at `Bus.start()` INFO, wire the YAML runner to populate it from a `supervisor:` sub-block under `bus:`, and cover the whole thing with unit tests. This is the foundational T1 slice; T3's `EndpointSupervisor` will read from `bus.config.supervisor` directly.

Issue: https://github.com/jeffrichley/agent_core/issues/270  
Design spec: `docs/superpowers/specs/2026-07-13-agent-core-supervision-design.md`

## Acceptance criteria

- `SupervisorConfig` dataclass exists in `packages/core/src/agent_core/bus/core.py` with exactly these 10 fields and defaults:

  | Field | Type | Default |
  |---|---|---|
  | `restart_backoff_base_seconds` | `int` | `1` |
  | `restart_backoff_factor` | `int` | `2` |
  | `restart_backoff_cap_seconds` | `int` | `60` |
  | `restart_jitter` | `str` | `"full"` |
  | `restarts_before_quarantine` | `int` | `5` |
  | `probe_interval_seconds` | `int` | `300` |
  | `delivery_backoff_base_seconds` | `int` | `2` |
  | `delivery_backoff_factor` | `int` | `2` |
  | `delivery_backoff_cap_seconds` | `int` | `60` |
  | `deliver_failures_before_breaker` | `int` | `5` |

- `BusConfig` has a `supervisor: SupervisorConfig` field that defaults to a fresh `SupervisorConfig()` per instance.
- `max_delivery_attempts` on `BusConfig` is left unchanged.
- `SupervisorConfig.__post_init__` raises `ValueError` for: any `_seconds` field ≤ 0; any `_factor` field < 1; `restarts_before_quarantine < 1`; `deliver_failures_before_breaker < 1`; `restart_jitter` not in `{"full", "equal", "none"}`.
- `Bus.start()` emits a single `log.info(...)` containing all 10 field values, placed immediately after `await self._store.connect()`.
- `build_bus_from_config` in `runner.py` reads a `supervisor:` sub-dict under `bus:` in YAML; each of the 10 keys is overridable; missing keys fall back to `SupervisorConfig` defaults.
- Unit tests confirm: defaults present on a freshly constructed `SupervisorConfig()`; overrides take when passed explicitly; `ValueError` is raised for each category of bad value; the INFO log fires during `Bus.start()`.

## Approach

No GoF pattern applies here — this is straightforward data-holding with `__post_init__` validation plus a single log call. The relevant engineering principle is SRP: `SupervisorConfig` groups the 10 supervision knobs so T3's `EndpointSupervisor` receives a single, well-typed parameter object rather than 10 individual fields scattered across `BusConfig`.

**`SupervisorConfig` dataclass** is added to `core.py` immediately before `BusConfig`. It uses `@dataclass` (not Pydantic) to stay consistent with `BusConfig`'s existing type. `__post_init__` raises `ValueError` with a descriptive message for each constraint violation. The valid set for `restart_jitter` is `{"full", "equal", "none"}` — `"full"` (the default, all jitter within the cap window) and `"none"` (no jitter) are the two the T3 supervisor will implement; `"equal"` is included as a recognized future value so a YAML with it doesn't silently fall through to an error at T3 implementation time.

**`BusConfig.supervisor` field** uses `dataclasses.field(default_factory=SupervisorConfig)` — `field` must be added to the `dataclasses` import already present in `core.py`. Do NOT write `supervisor: SupervisorConfig = SupervisorConfig()` at the class level; even though `SupervisorConfig` holds only immutable primitives today, the mutable-default idiom is wrong and will cause a `dataclasses.FrozenInstanceError`-style lint warning.

**Boot log in `Bus.start()`** is inserted after `await self._store.connect()` and before the endpoint-start `try` block (lines 129–144 of the current `core.py`). Logging before the endpoints loop ensures the knobs are visible even if an endpoint fails to start. All 10 values must appear; a single `log.info(...)` call is sufficient. Example format (Worker may choose a different layout):
```
supervisor config: restart_backoff=1s×2 cap=60s jitter=full quarantine_after=5 probe=300s delivery_backoff=2s×2 cap=60s breaker_after=5
```

**Runner wiring** (`runner.py` lines 70–80) follows the existing flat-field pattern exactly. After the existing `bus_cfg_raw = raw.get("bus", {})` line, add:
```python
sup_cfg_raw = bus_cfg_raw.get("supervisor", {}) or {}
supervisor = SupervisorConfig(
    restart_backoff_base_seconds=sup_cfg_raw.get("restart_backoff_base_seconds", 1),
    ...
)
```
Then pass `supervisor=supervisor` to `BusConfig(...)`. The `SupervisorConfig` import is already in the same file's module (`core.py` → `runner.py` already imports `BusConfig` from there).

**Test file**: new `packages/core/tests/bus/test_supervisor_config.py`. Root `pyproject.toml` sets `asyncio_mode = "auto"`, so `async def test_*` needs no decorator. Use `tmp_path` for any test that instantiates a `Bus`.

## Sub-requests (topologically sorted)

1. **Add `SupervisorConfig` dataclass to `core.py`** — define it before `BusConfig`; 10 fields with defaults; `__post_init__` validation raising `ValueError` for each constraint; add `field` to the `dataclasses` import.
2. **Add `supervisor` field to `BusConfig`** — `supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)`.
3. **Add supervisor boot log to `Bus.start()`** — one `log.info(...)` after `await self._store.connect()`, all 10 field values explicit.
4. **Wire `supervisor` into `build_bus_from_config` (`runner.py`)** — parse `bus.supervisor:` YAML sub-dict; construct `SupervisorConfig`; pass as `supervisor=` kwarg to `BusConfig(...)`.
5. **Write unit tests** — new `packages/core/tests/bus/test_supervisor_config.py`; cover defaults, overrides, each validation branch, and the boot INFO log.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/core.py` | Add `SupervisorConfig` dataclass (before `BusConfig`); add `field` to `dataclasses` import; add `supervisor` field to `BusConfig`; add INFO log in `Bus.start()` after `await self._store.connect()` |
| `packages/core/src/agent_core/bus/runner.py` | After `bus_cfg_raw = raw.get("bus", {})`, parse `sup_cfg_raw`; construct `SupervisorConfig`; add `supervisor=supervisor` to `BusConfig(...)` constructor call; add `SupervisorConfig` to the import from `agent_core.bus.core` |
| `packages/core/tests/bus/test_supervisor_config.py` | New file: unit tests — defaults, overrides, validation rejects (parametrized), boot log via `caplog` |

No other files change. Existing `test_runner.py` and `test_core_lifecycle.py` pass without modification because the new `supervisor` field defaults silently.

## Alternatives considered

1. **Flat fields directly on `BusConfig`** — add 10 fields inline, matching the existing pattern for `redelivery_timeout_seconds` etc. Ruled out: the issue and design spec both say "[supervisor] config *section*", implying a named group; T3's `EndpointSupervisor.__init__` should receive `SupervisorConfig` as a typed unit, not 10 separate kwargs.
2. **Pydantic `BaseModel` for `SupervisorConfig`** — built-in field validators, no `__post_init__` needed. Ruled out: `BusConfig` is a plain `@dataclass`; mixing Pydantic only for the nested type would make `dataclasses.fields(BusConfig)` asymmetric and deviate from the established convention without any gain at this scope.
3. **`TypedDict` instead of a dataclass** — lightweight; no constructor, no `__post_init__`. Ruled out: validation is an explicit acceptance criterion; `TypedDict` provides no hook for it without wrapping in a factory function.

## Open questions

None. All 10 fields, their types, their defaults, and the validation constraints are explicit in the issue. The log format is intentionally unspecified — any single INFO line containing all 10 resolved values satisfies the criterion.

## Out of scope

- Implementing `EndpointSupervisor` state machine (T3)
- `BusHandle.spawn()` tracked-task API (T2)
- Degraded boot changes to `Bus.start()` (T4)
- Delivery retry backoff or `next_attempt_at` persistence column (T5)
- ack-vs-nack fixes in endpoints (T6)
- Adding `supervisor:` examples to any YAML config or documentation files
- Changes to `max_delivery_attempts` on `BusConfig` (explicitly preserved per issue)
