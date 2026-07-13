# Spec: add `SupervisorConfig` block to `BusConfig` with boot logging (issue #270)

## Goal

Add a `SupervisorConfig` nested dataclass to `packages/core/src/agent_core/bus/core.py` holding the 10 supervision knobs listed in the issue, attach it to `BusConfig`, log the resolved values once at `Bus.start()` INFO, wire the YAML runner to populate it, and cover the whole thing with unit tests. This is the foundational T1 slice; T3 (`EndpointSupervisor`) will read these values directly from `bus.config.supervisor`.

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

- `BusConfig` has a `supervisor: SupervisorConfig` field that defaults to `SupervisorConfig()`.
- `max_delivery_attempts` on `BusConfig` is untouched.
- `SupervisorConfig.__post_init__` raises `ValueError` for: any `_seconds` or `_factor` field ≤ 0; `restarts_before_quarantine < 1`; `deliver_failures_before_breaker < 1`; `restart_jitter` not in `{"full", "equal", "none"}`.
- `Bus.start()` logs a single INFO line containing all 10 field values immediately after `await self._store.connect()`.
- `build_bus_from_config` in `runner.py` reads a `supervisor:` sub-dict under `bus:` in YAML; all 10 keys are overridable; missing keys fall back to `SupervisorConfig` defaults.
- Unit tests: defaults present, overrides take, validation rejects bad values, boot log fires.

## Approach

No GoF pattern applies — this is straightforward data-holding with `__post_init__` validation and a single log call. The principle in play is SRP: `SupervisorConfig` groups the supervision knobs together so T3's `EndpointSupervisor` gets a single, well-typed parameter object rather than ten individual fields scattered across `BusConfig`.

**`SupervisorConfig` dataclass** (add before `BusConfig` in `core.py`): a standard `@dataclass` with `__post_init__` raising `ValueError` on out-of-range values. No Pydantic — `BusConfig` is already a `@dataclass`; mixing would be inconsistent.

**`BusConfig.supervisor` field**: Use `dataclasses.field(default_factory=SupervisorConfig)` so each `BusConfig` instance gets its own mutable default. Do NOT write `supervisor: SupervisorConfig = SupervisorConfig()` at class level — that would share a single instance (mutable-default anti-pattern for dataclasses, though `SupervisorConfig` holds only primitives; still wrong idiom).

**Boot log in `Bus.start()`**: Insert after `await self._store.connect()` and before the endpoint-start loop. Log all 10 fields explicitly by name so operators can confirm values without reading source. Example format:
```
supervisor config: restart_backoff=1s×2 cap=60s jitter=full quarantine_after=5 probe=300s delivery_backoff=2s×2 cap=60s breaker_after=5
```
The exact format is up to the Worker; what matters is that all 10 values appear in a single `log.info(...)` call.

**Runner wiring in `runner.py`**: Follow the exact pattern already used for top-level `BusConfig` fields (lines 70-80). Read `bus_cfg_raw.get("supervisor", {}) or {}` into `sup_cfg_raw`, then pass each key to `SupervisorConfig(...)` with an explicit `.get(key, default)` fallback. Construct the `SupervisorConfig` instance before constructing `BusConfig`.

**Test file**: `packages/core/tests/bus/test_supervisor_config.py`. The test suite uses `asyncio_mode = "auto"` (root `pyproject.toml` line 110), so `async def test_*` functions need no decorator. Use `tmp_path` for any test that needs a `BusConfig` instance.

## Sub-requests (topologically sorted)

1. **Add `SupervisorConfig` dataclass to `core.py`** — define before `BusConfig`; 10 fields with defaults; `__post_init__` validation.
2. **Add `supervisor` field to `BusConfig`** — `supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)`; add `field` to the `dataclasses` import.
3. **Add supervisor boot log to `Bus.start()`** — one `log.info(...)` after `await self._store.connect()`, all 10 field values inline.
4. **Wire `supervisor` in `build_bus_from_config` (`runner.py`)** — parse `bus.supervisor:` YAML sub-dict; construct `SupervisorConfig`; pass as kwarg to `BusConfig(...)`.
5. **Write unit tests** — new file `packages/core/tests/bus/test_supervisor_config.py`; cover defaults, overrides, validation rejects, and boot log.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/core.py` | Add `SupervisorConfig` dataclass; add `supervisor` field to `BusConfig`; add INFO log in `Bus.start()` |
| `packages/core/src/agent_core/bus/runner.py` | Read `bus.supervisor:` YAML dict; construct `SupervisorConfig`; pass to `BusConfig` |
| `packages/core/tests/bus/test_supervisor_config.py` | New file: unit tests for defaults, overrides, validation, boot log |

No other files change. Existing `test_runner.py` and `test_core_lifecycle.py` need no modification — the new field defaults silently, so no existing test breaks.

## Alternatives considered

1. **Flat fields on `BusConfig` directly** — add 10 flat fields to `BusConfig`, consistent with `redelivery_timeout_seconds` et al. Ruled out: the issue and design spec both say "[supervisor] config section", implying a named group; T3's `EndpointSupervisor.__init__` will receive one well-typed parameter (`SupervisorConfig`) rather than ten spread-out fields.
2. **Pydantic `BaseModel` for `SupervisorConfig`** — nice built-in validators. Ruled out: `BusConfig` is a `@dataclass`; mixing Pydantic only for the nested type would be inconsistent and would make field introspection (`dataclasses.fields(BusConfig)`) asymmetric.
3. **No nested type — use a `TypedDict`** — lightweight but no `__post_init__` validation. Ruled out: validation is an explicit acceptance criterion; `@dataclass` with `__post_init__` is the obvious fit.

## Open questions

None. All fields, defaults, and validation constraints are explicit in the issue. The log message format is intentionally left to the Worker (the acceptance criterion is all-values-present, not a specific format string).

## Out of scope

- Implementing `EndpointSupervisor` state machine (T3, issue #272 or similar)
- `BusHandle.spawn()` tracked-task API (T2)
- Degraded boot changes to `Bus.start()` (T4)
- Delivery retry backoff or `next_attempt_at` persistence column (T5)
- ack-vs-nack fixes in endpoints (T6)
- Adding `supervisor:` examples to any YAML config files or documentation
- Changes to `max_delivery_attempts` (explicitly left alone per issue)
