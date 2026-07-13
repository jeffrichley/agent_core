# Spec: add `SupervisorConfig` block to `BusConfig` and log at boot (issue #270)

## Goal

Add a `SupervisorConfig` nested dataclass to `packages/core/src/agent_core/bus/core.py` containing the ten supervision-layer tuning knobs from the design spec, wire it into `BusConfig` as a `supervisor` field, expose it through the YAML runner so values are overridable at config time, and emit the resolved values once at INFO when `Bus.start()` runs. This is the foundational T1 ticket that T3 (the `EndpointSupervisor` state machine) depends on. See [issue #270](https://github.com/jeffrichley/agent_core/issues/270).

## Acceptance criteria

- `BusConfig` has a `supervisor` field of type `SupervisorConfig` whose default-factory produces the ten default values from the issue table:
  - `restart_backoff_base_seconds = 1`
  - `restart_backoff_factor = 2`
  - `restart_backoff_cap_seconds = 60`
  - `restart_jitter = "full"`
  - `restarts_before_quarantine = 5`
  - `probe_interval_seconds = 300`
  - `delivery_backoff_base_seconds = 2`
  - `delivery_backoff_factor = 2`
  - `delivery_backoff_cap_seconds = 60`
  - `deliver_failures_before_breaker = 5`
- `max_delivery_attempts` on `BusConfig` is unchanged.
- `BusConfig(storage_path=…)` constructed with no additional kwargs produces the defaults above (no new required fields).
- The `supervisor:` YAML sub-block under `bus:` in `agent_core.yaml` is read by `build_bus_from_config` in `runner.py` and overrides any of the ten fields.
- `SupervisorConfig.__post_init__` raises `ValueError` for:
  - `restarts_before_quarantine < 1`
  - `restart_backoff_factor < 1`
  - `delivery_backoff_factor < 1`
  - `restart_backoff_base_seconds <= 0`
  - `restart_backoff_cap_seconds <= 0`
  - `delivery_backoff_base_seconds <= 0`
  - `delivery_backoff_cap_seconds <= 0`
  - `probe_interval_seconds <= 0`
  - `deliver_failures_before_breaker < 1`
- `Bus.start()` emits exactly one INFO log entry containing the resolved supervisor config before endpoint start-up begins.
- All new code passes `just check` (ruff + pytest with 80 % patch coverage floor).

## Approach

**Pattern naming.** No GoF pattern applies here; this is straightforward **value-object configuration**: a `SupervisorConfig` dataclass (immutable once constructed, validated in `__post_init__`) nested inside the existing `BusConfig` value object. The engineering principle is SRP: supervision-layer knobs live together in their own object, not scattered across `BusConfig`'s flat field list.

**`SupervisorConfig` dataclass in `core.py`.** Place the new dataclass directly above `BusConfig` in `packages/core/src/agent_core/bus/core.py`. It uses the stdlib `@dataclass` decorator (consistent with `BusConfig`, `EndpointSpec`, and `BusHookSpec` in the same file). Validation lives in `__post_init__`. The `restart_jitter` field is typed `str` (default `"full"`) rather than a `Literal`; its vocabulary is for T3 (the backoff algorithm) to interpret, and constraining it here would over-scope this ticket.

**`supervisor` field on `BusConfig`.** Add `supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)` to `BusConfig`. `asdict` is imported from `dataclasses` alongside the existing `dataclass, field` imports so `Bus.start()` can log a plain `dict` representation. Using `asdict` (rather than `str(config.supervisor)`) produces a JSON-serialisable structure that structured-logging backends can consume.

**Runner update (`runner.py`).** The existing pattern (lines 70–80) reads every `BusConfig` field from `bus_cfg_raw = raw.get("bus", {})` via `.get(key, default)`. Extend it to also read `supervisor_raw = (bus_cfg_raw.get("supervisor") or {})` and construct `SupervisorConfig(**supervisor_raw)`. Passing unknown YAML keys straight into the dataclass will raise `TypeError`; this mirrors the existing runner's behaviour (unknown bus-level keys are currently silently ignored, so the spec requires only that *known* supervisor keys flow through — not that unknown ones error loudly — matching the current convention).

**Boot log in `Bus.start()`.** Insert before the `for spec in self._endpoints_by_name.values()` loop:
```python
log.info("supervisor config: %s", asdict(self.config.supervisor))
```
This satisfies "logged once at INFO" and "before endpoint startup" (so the values are visible even if an endpoint crashes during start).

**New test file.** Create `packages/core/tests/bus/test_supervisor_config.py`. Tests are pure-Python, synchronous where possible (defaults and validation tests), async only for the boot-log test (which starts the bus). All tests follow the `_stub` / fixture pattern established in `test_core_lifecycle.py`.

## Sub-requests (topologically sorted)

1. **Add `SupervisorConfig` dataclass and update imports in `packages/core/src/agent_core/bus/core.py`.**
   - Add `asdict` to the `from dataclasses import …` line (line 16).
   - Insert `@dataclass class SupervisorConfig` (ten fields + `__post_init__`) directly above `@dataclass class BusConfig`.
   - Add `supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)` as the last field of `BusConfig`.
   - Add `log.info("supervisor config: %s", asdict(self.config.supervisor))` as the first statement inside `Bus.start()` after the early-return guard.

2. **Update `build_bus_from_config` in `packages/core/src/agent_core/bus/runner.py`.**
   - After `bus_cfg_raw = raw.get("bus", {})` (line 70), extract `supervisor_raw = (bus_cfg_raw.get("supervisor") or {})`.
   - Construct `supervisor = SupervisorConfig(**supervisor_raw)` (importing `SupervisorConfig` from `agent_core.bus.core`).
   - Add `supervisor=supervisor` to the `BusConfig(…)` constructor call.

3. **Add `packages/core/tests/bus/test_supervisor_config.py`** covering:
   - Default values for all ten fields.
   - Override: construct `SupervisorConfig` with non-default values; assert each overridden field is stored.
   - Validation: one `pytest.raises(ValueError)` test per invalid input class (negative base, factor < 1, quarantine count 0, etc.).
   - Boot log: async test that starts a `Bus` with a custom `SupervisorConfig` and uses `pytest`'s `caplog` fixture to assert one INFO message containing the supervisor config dict is emitted during `start()`.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/core.py` | **Modify.** Add `asdict` to imports. Add `SupervisorConfig` dataclass above `BusConfig`. Add `supervisor` field to `BusConfig`. Add boot INFO log in `Bus.start()`. |
| `packages/core/src/agent_core/bus/runner.py` | **Modify.** Import `SupervisorConfig`. Parse `bus.supervisor` YAML sub-dict; construct `SupervisorConfig` from it; pass to `BusConfig`. |
| `packages/core/tests/bus/test_supervisor_config.py` | **Create.** Unit tests for defaults, overrides, validation, and boot-log emission. |

## Alternatives considered

- **Flat fields on `BusConfig` directly** (no nesting): avoids the new `SupervisorConfig` class and a runner change. Rejected because `BusConfig` already has seven fields; adding ten more makes it unwieldy and loses the conceptual grouping the design spec intends by calling these a "block". T3 will import `SupervisorConfig` by name to read the knobs; nesting gives T3 a clean type to reference.
- **Pydantic model instead of `@dataclass`**: supports validators and richer type coercion. Rejected because (a) `BusConfig` and all sibling configs in `core.py` are stdlib `@dataclass`; mixing in Pydantic here for one sub-config adds an import dependency that isn't justified by any feature the issue asks for, and (b) `__post_init__` is sufficient for the simple range checks required.

## Open questions

None. The ten fields, their defaults, their names, and the validation rules are all stated explicitly in the issue. The config mechanism (`@dataclass` + runner `.get()` reads) is established by the existing `BusConfig` pattern.

## Out of scope

- Implementing the supervision state machine (`EndpointSupervisor`) — that is T3 (#272 or similar).
- Adding `supervisor:` YAML keys to any example configs or docs.
- Validating `restart_jitter` against an allowed vocabulary — T3 will consume it and can enforce allowed values then.
- Changing `max_delivery_attempts` in any way.
- Any changes to test files outside `packages/core/tests/bus/`.
