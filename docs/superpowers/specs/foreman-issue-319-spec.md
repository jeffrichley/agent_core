# Spec: Pydantic daemon-config schema + real `validate_config` (issue #319)

## Goal

Create a validated Pydantic schema (`DaemonConfig` and sub-models) for the daemon's YAML config covering the `bus`, `http`, `bus_hooks`, `mcp_audit`, and `endpoints` sections with `extra="forbid"` so typo'd parameter names are caught at boot with a clear error message instead of a silent `KeyError` mid-boot. Activate the currently-no-op `validate_config` hookimpl in `BuiltinRuntimePlugin` to invoke that schema. Replace the raw `dict.get()` parsing in `bus/runner.py` with typed attribute access on the parsed model.

Design authority: `docs/superpowers/specs/2026-07-14-per-being-config-isolation-design.md` §D2. Issue: https://github.com/jeffrichley/agent_core/issues/319. Schema in `agent_core.bus.config` is importable by the hatchery for correct-by-construction generation (Cluster β dependency).

## Acceptance criteria

- `packages/core/src/agent_core/bus/config.py` exists and exports `BusBootError`, `DaemonConfig`, and the six sub-models. `from agent_core.bus.config import DaemonConfig, BusBootError` has no side effects.
- `DaemonConfig.model_validate({"buus": {}})` raises `pydantic.ValidationError` (typo in top-level key caught by `extra="forbid"`).
- `DaemonConfig.model_validate({"bus": {"storage_pathh": "x"}})` raises `pydantic.ValidationError` (typo in `bus:` sub-key caught).
- `DaemonConfig.model_validate({"http": {"bind_portt": 9000}})` raises `pydantic.ValidationError`.
- `DaemonConfig.model_validate({})` succeeds and all fields carry the same defaults as the current inline `runner.py` `dict.get()` defaults (e.g. `storage_path == "~/.agent-core/bus.sqlite"`, `bind_host == "127.0.0.1"`, `bind_port == 8788`).
- `BuiltinRuntimePlugin.validate_config` in `packages/core/src/agent_core/plugins/manager.py` is no longer a `return None` no-op — it calls `DaemonConfig.model_validate(raw_config)` and re-raises any `pydantic.ValidationError` as `BusBootError`.
- `build_bus_from_config` raises `BusBootError` when passed a YAML with an unknown top-level key (e.g. `buus: {}`).
- All raw `dict.get()` / `raw.get()` / `bus_cfg_raw.get()` / `sup_cfg_raw.get()` / `audit_cfg.get()` / `http_cfg.get()` accesses in `build_bus_from_config` are replaced with typed attribute access on the parsed `DaemonConfig` object.
- `from agent_core.bus.runner import BusBootError` continues to work (backwards-compat re-export; no test-file changes required).
- `packages/core/tests/bus/test_config.py` covers: valid minimal config round-trips cleanly; all `extra="forbid"` guards fire correctly at every nesting level; all default values match those documented in the current `runner.py`; an `EndpointEntryConfig` with an arbitrary `params` dict is valid (content of `params` is unconstrained); `HookEntryConfig` likewise.
- `packages/core/tests/bus/test_runner.py` gains a test that an unknown top-level key raises `BusBootError` at `build_bus_from_config` call time.
- `just check` passes (ruff lint-clean + full test suite + 85 % coverage gate).

## Approach

No GoF pattern applies — this is a straightforward data-boundary hardening. Guiding principles: **SRP** — `bus/config.py` owns the on-disk data contract (shape + defaults); `runner.py` owns the boot sequence; `BuiltinRuntimePlugin` owns built-in validation. **DIP** is loosely in play: the `validate_config` hookspec lets third-party plugins extend validation without coupling them to `runner.py`.

**Circular import rationale — `BusBootError` moves to `bus/config.py`.**
`runner.py` already imports from `plugins/manager.py` (`create_plugin_manager` etc.). If `manager.py`'s `validate_config` hookimpl needed to import `BusBootError` from `runner.py`, that would create the cycle `runner.py → manager.py → runner.py`. The fix is to define `BusBootError` in the new leaf module `bus/config.py` and have both `runner.py` and `manager.py` import it from there. `bus/cli.py` already imports `BusBootError` from `runner.py` — update that import too. To avoid touching test files, `runner.py` re-exports `BusBootError` with a bare re-import at module scope:
```python
from agent_core.bus.config import BusBootError  # noqa: F401 (re-export for compat)
```

**New file `packages/core/src/agent_core/bus/config.py`** defines `BusBootError` plus seven Pydantic `BaseModel` sub-classes, all with `model_config = ConfigDict(extra="forbid")`:

| Model | Corresponds to |
|---|---|
| `SupervisorSectionConfig` | `bus.supervisor:` block |
| `BusSectionConfig` | `bus:` block (contains `supervisor: SupervisorSectionConfig`) |
| `HttpConfig` | `http:` block |
| `HookEntryConfig` | one entry in `bus_hooks.pre_publish` / `pre_deliver` |
| `BusHooksConfig` | `bus_hooks:` block |
| `McpAuditConfig` | `mcp_audit:` block |
| `EndpointEntryConfig` | one entry in `endpoints:` |
| `DaemonConfig` | root — all the above as optional fields with `Field(default_factory=…)` |

`storage_path` remains `str` (not `Path`) because YAML delivers it as a string; the runner already calls `.expanduser()`. `params` fields on `HookEntryConfig` and `EndpointEntryConfig` are `dict[str, Any]` — arbitrary plugin content is allowed *inside* `params`; `extra="forbid"` only blocks unrecognised keys *at the entry struct level* (i.e., extra siblings of `type`/`name`/`params`).

**`plugins/manager.py` change — real `validate_config`:**
Replace the `return None` body of `BuiltinRuntimePlugin.validate_config` with:
```python
import pydantic
from agent_core.bus.config import BusBootError, DaemonConfig
try:
    DaemonConfig.model_validate(raw_config)
except pydantic.ValidationError as exc:
    raise BusBootError(f"daemon config validation failed:\n{exc}") from exc
```
Keep the import inside the method body for now (avoids touching the module-level import block).

**`bus/runner.py` change — replace `dict.get()` with typed access:**
After the existing call to `plugin_manager.hook.validate_config(raw_config=raw)` at line 67, add:
```python
cfg = DaemonConfig.model_validate(raw)
```
Then replace every subsequent raw-dict access pattern:

| Old (dict.get) | New (typed) |
|---|---|
| `raw.get("bus", {})` | `cfg.bus` |
| `bus_cfg_raw.get("storage_path", "~/.agent-core/bus.sqlite")` | `cfg.bus.storage_path` |
| `bus_cfg_raw.get("supervisor", {}) or {}` | `cfg.bus.supervisor` |
| `sup_cfg_raw.get("restart_backoff_base_seconds", 1)` | `cfg.bus.supervisor.restart_backoff_base_seconds` |
| `bus_cfg_raw.get("redelivery_timeout_seconds", 300)` | `cfg.bus.redelivery_timeout_seconds` |
| `raw.get("mcp_audit", {}) or {}` | `cfg.mcp_audit` |
| `audit_cfg.get("enabled", True)` | `cfg.mcp_audit.enabled` |
| `audit_cfg.get("log_root", "~/.agent-core/bus/mcp-audit")` | `cfg.mcp_audit.log_root` |
| `audit_cfg.get("timezone", "US/Eastern")` | `cfg.mcp_audit.timezone` |
| `audit_cfg.get("skip_tools", []) or []` | `cfg.mcp_audit.skip_tools` |
| `(raw.get("bus_hooks", {}) or {}).get(stage, []) or []` | `getattr(cfg.bus_hooks, stage)` (where `stage ∈ {"pre_publish", "pre_deliver"}`) |
| `entry["type"]` (in hooks loop) | `entry.type` |
| `entry.get("params", {})` (in hooks loop) | `entry.params` |
| `raw.get("http", {})` | `cfg.http` |
| `http_cfg.get("bind_host", "127.0.0.1")` | `cfg.http.bind_host` |
| `http_cfg.get("bind_port", 8788)` | `cfg.http.bind_port` |
| `raw.get("endpoints", []) or []` | `cfg.endpoints` |
| `entry["type"]` (in endpoints loop) | `entry.type` |
| `entry["name"]` (in endpoints loop) | `entry.name` |
| `entry.get("params", {})` (in endpoints loop) | `entry.params` |
| `entry.get("description", "")` | `entry.description` |

The env-var overrides for `slow_deliver_warn_seconds` and `watchdog_timeout_seconds` remain in the runner after accessing `cfg.bus.*`:
```python
slow_deliver_warn_seconds=float(os.environ.get("BUS_SLOW_DELIVER_WARN_SECONDS", cfg.bus.slow_deliver_warn_seconds)),
watchdog_timeout_seconds=int(os.environ.get("BUS_WATCHDOG_TIMEOUT_SECONDS", cfg.bus.watchdog_timeout_seconds)),
```

**Manual entry-field guards that become redundant** once Pydantic parses the endpoints/hooks lists: the `if "type" not in entry` / `if "name" not in entry` checks at runner.py lines 184–186 are now enforced by the schema (both fields are required on `EndpointEntryConfig`). Similarly the `if "type" not in entry` check for hook entries (line 151) is enforced by `HookEntryConfig`. Remove these guards and their associated `BusBootError` raises — the Pydantic parse at line 67 will have already caught them. Keep the subsequent runtime guards (`endpoint_type not in endpoint_types`, etc.) because those are semantic checks beyond the schema.

**Plugin hookspec dict-shape compatibility**: `configure_endpoint_instance` and `wire_endpoints_after_registration` accept `endpoint_config: dict[str, Any]` and `raw_endpoint_configs: dict[str, dict[str, Any]]` respectively. When building these dicts from the typed Pydantic objects, use `.model_dump()`:
```python
# building raw_endpoint_configs for cross-endpoint wiring
raw_endpoint_configs: dict[str, dict[str, Any]] = {
    entry.name: entry.model_dump() for entry in cfg.endpoints
}
# and for configure_endpoint_instance:
plugin_manager.hook.configure_endpoint_instance(
    instance=instance,
    endpoint_name=entry.name,
    endpoint_config=entry.model_dump(),
    services=services,
)
```
Same pattern for `configure_bus_hook_instance(hook_config=entry.model_dump())`.

**`_validate_http` helper** currently accepts `http_cfg: dict`. Update its signature to `http_cfg: HttpConfig` and change the body from `http_cfg.get("bind_host", "127.0.0.1")` to `http_cfg.bind_host`. The helper is private (`_validate_http`) so this is not an API break.

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/bus/config.py`** — define `BusBootError`, `SupervisorSectionConfig`, `BusSectionConfig`, `HttpConfig`, `HookEntryConfig`, `BusHooksConfig`, `McpAuditConfig`, `EndpointEntryConfig`, `DaemonConfig`. All models use `model_config = ConfigDict(extra="forbid")`. Defaults match the current `runner.py` inline defaults exactly.

2. **Modify `packages/core/src/agent_core/bus/runner.py`** — add `from agent_core.bus.config import BusBootError, DaemonConfig` and a bare re-export comment; remove the local `class BusBootError` definition; update `_validate_http` to accept `HttpConfig`; parse `cfg = DaemonConfig.model_validate(raw)` after the `validate_config` hookspec call; replace all raw-dict access with typed access per the table above; remove the redundant manual type/name guards on entry iteration; use `.model_dump()` when passing entries to plugin hookspecs.

3. **Modify `packages/core/src/agent_core/plugins/manager.py`** — implement real `BuiltinRuntimePlugin.validate_config` body (lazy import + `DaemonConfig.model_validate` + `BusBootError` re-raise pattern).

4. **Modify `packages/core/src/agent_core/bus/cli.py`** — change `from agent_core.bus.runner import BusBootError, build_bus_from_config` to `from agent_core.bus.config import BusBootError` + separate `from agent_core.bus.runner import build_bus_from_config`.

5. **Create `packages/core/tests/bus/test_config.py`** — unit tests for all Pydantic models: valid round-trip, `extra="forbid"` fires at root + every nested level, defaults match runner defaults, `params` dict is unconstrained.

6. **Modify `packages/core/tests/bus/test_runner.py`** — add one async test: unknown top-level key (e.g. `{"buus": {}}` in YAML) causes `build_bus_from_config` to raise `BusBootError`.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/bus/config.py` | **New** — `BusBootError` + 7 Pydantic models for the daemon YAML config schema |
| `packages/core/src/agent_core/bus/runner.py` | **Modify** — import from `bus/config.py`, remove local `BusBootError`, parse `DaemonConfig`, replace `dict.get()` with typed access, update `_validate_http`, use `.model_dump()` for plugin hookspec calls |
| `packages/core/src/agent_core/plugins/manager.py` | **Modify** — real `validate_config` body with lazy imports |
| `packages/core/src/agent_core/bus/cli.py` | **Modify** — split import: `BusBootError` from `bus/config.py`, `build_bus_from_config` from `bus/runner.py` |
| `packages/core/tests/bus/test_config.py` | **New** — Pydantic model unit tests |
| `packages/core/tests/bus/test_runner.py` | **Modify** — add unknown-top-level-key test |

## Alternatives considered

1. **Define `DaemonConfig` in `packages/core/src/agent_core/models.py`** (alongside `ToolResult`, `ToolConfig`, `PipelineConfig`): `models.py` is the schema for the Claude Code hook pipeline, not the daemon bus config. Mixing them violates SRP and would confuse the hatchery importer (which needs daemon-bus schema, not hook-pipeline schema). Ruled out.

2. **Leave `BusBootError` in `runner.py` and import it lazily inside `manager.py`**: Technically works (Python resolves lazy imports at call time, not import time) but is a code smell that hides a real dependency edge. Moving `BusBootError` to `bus/config.py` makes the dependency graph explicit and keeps the error class adjacent to the schema it guards. Ruled out in favour of the explicit placement.

3. **Have `validate_config` raise `pydantic.ValidationError` directly** (not `BusBootError`), and let the runner catch and wrap it: This leaks the Pydantic type into the hookspec contract, meaning third-party `validate_config` hookimpls would also need to raise `ValidationError` rather than the project-standard `BusBootError`. Ruled out: inconsistent error surface.

## Open questions

(None — the circular-import path is resolved via `bus/config.py` as a leaf module; all design calls are unambiguous against the codebase as read.)

## Out of scope

- **Cα-2** (per-being fragment isolation, degraded-load, Pepper migration to `endpoints.d/`) — depends on this ticket, separate issue.
- **Cα-3** (`daemon doctor` config-hygiene extension) — depends on this ticket, separate issue.
- **Semantic validators** — the `ZoneInfo` timezone check and the non-loopback `bind_host` guard in `_validate_http` remain as runtime checks in `runner.py`; adding Pydantic `@field_validator` equivalents is out of scope.
- **The `pipelines:` key** — this is the Claude Code hook pipeline config (read by `agent_core.hooks.pipeline`), not a daemon-config key. It is never loaded by `build_bus_from_config`; the daemon config and hook-pipeline config are separate files and separate codepaths.
- **Hatchery schema reuse (Cluster β)** — Cα-1 makes the schema importable; the hatchery integration that uses `DaemonConfig` to generate correct-by-construction configs is a future Cluster β ticket.
- **Test-file import updates for `BusBootError`** — existing test files that do `from agent_core.bus.runner import BusBootError` continue to work via the re-export in `runner.py`; no test-file import changes are required.
