# Spec: per-being config-fragment isolation + degraded load + migrate Pepper (issue #320)

## Goal

Deliver the config-side of per-being failure isolation (Cα-2): change `bus/runner.py` so a broken YAML fragment quarantines only itself (not the whole bus), and a bad endpoint entry is logged-and-skipped rather than killing boot for every being. Then migrate Pepper's four inline endpoints from the monolith config to `endpoints.d/pepper.yaml`, completing the D1 + D3 deliverables from [`docs/superpowers/specs/2026-07-14-per-being-config-isolation-design.md`](docs/superpowers/specs/2026-07-14-per-being-config-isolation-design.md).

Closes the `[P0][L]` gap (monolithic daemon — a bad entry drops every being) and the `[P1]` mixed-mechanism gap (Pepper inline vs. Wren fragment).

**Dependency**: blocked by Cα-1 (issue to be filed), which must land first to supply the Pydantic endpoint-entry schema that the per-entry validation catches.

## Acceptance criteria

- **Fragment quarantine — YAML error**: a fragment with a YAML syntax error is quarantined (logged at ERROR level naming the file); all other fragments and the monolith endpoints still load; `build_bus_from_config` does not raise.
- **Fragment quarantine — non-list endpoints**: a fragment whose `endpoints:` value is not a list is quarantined (logged at ERROR level naming the file); all other fragments and the monolith endpoints still load; `build_bus_from_config` does not raise.
- **Entry skip — missing `type`**: an endpoint entry without `"type"` is logged at ERROR level and skipped; sibling entries (same fragment or monolith) still load.
- **Entry skip — missing `name`**: an endpoint entry without `"name"` is logged at ERROR level and skipped; sibling entries still load.
- **Entry skip — unknown type**: an endpoint entry whose `"type"` has no registered class is logged at ERROR level and skipped; sibling entries still load.
- **Entry skip — construction failure**: an endpoint entry whose class constructor raises is logged at ERROR level and skipped; sibling entries still load.
- **Name collision remains loud**: an endpoint name collision (duplicate endpoint name across monolith + fragments) still raises `BusBootError` — this is a config conflict, not a degradable validation error.
- **Log content**: every quarantine/skip log record includes the fragment filename (for fragments) or the entry's `name`/`type` hints (for entries).
- **Existing malformed-fragment test updated**: `test_endpoints_d_malformed_fragment_raises_with_filename` is renamed and its assertion changed from `pytest.raises(BusBootError)` to "boot continues + error logged".
- **New tests added**: `test_runner_endpoints_d.py` (or a new `test_runner_endpoints_d_degraded.py`) covers all quarantine and skip scenarios above.
- **Pepper fragment created**: `docs/examples/endpoints.d/pepper.yaml` exists and contains all four Pepper being-endpoints (`pepper`, `briefs.pepper`, `discord-pepper`, `webcam-pepper`).
- **Pepper monolith example updated**: `docs/examples/pepper-agent-core.yaml` has the four being-endpoints removed; a comment directs operators to `endpoints.d/pepper.yaml`.
- `just check` passes (ruff + full test suite with coverage).

## Approach

No GoF pattern applies cleanly. Guiding principles: **OCP** — the degraded-load wrapper adds exception boundaries around the existing validation chain rather than replacing it; **SRP** — all quarantine/skip logic lives inside `build_bus_from_config`, which is the single place responsible for config loading.

### Two-tier degraded load

**Fragment quarantine** (coarser — the whole fragment is quarantined):
Wrap `yaml.safe_load(fragment_path.read_text(...))` in `try/except yaml.YAMLError`. If parsing fails, log at ERROR and `continue` (skip the fragment). Convert the existing `raise BusBootError(...)` for non-list `endpoints:` into an `logger.error(...)` + `continue`.

**Entry skip** (finer — one bad entry is skipped):
Introduce a module-private `_EntryBusBootError(BusBootError)` subclass. All per-entry validation raises (`"type" missing`, `"name" missing`, unknown type, constructor failure, `isinstance` check failure) are changed to raise `_EntryBusBootError`. Wrap the entire per-entry processing block in `try/except _EntryBusBootError as exc: logger.error(...); continue`. This leaves the name-collision path raising plain `BusBootError` (via `bus.register()` → `ValueError` → `BusBootError`), which propagates without being caught — preserving the loud-failure requirement.

**Post-Cα-1 note**: once Cα-1 lands and adds Pydantic schema validation, the Worker executing Cα-2 must read Cα-1's code to determine whether schema violations raise `_EntryBusBootError` (if Cα-1 integrates into the same `raise` chain) or `pydantic.ValidationError` (if Cα-1 adds a separate validate call). If `pydantic.ValidationError` is raised separately, add it to the catch clause: `except (_EntryBusBootError, pydantic.ValidationError)`.

### Logger

Add `import logging` and `logger = logging.getLogger(__name__)` to `runner.py`. The logger name `agent_core.bus.runner` is what tests target with `caplog.at_level(logging.ERROR, logger="agent_core.bus.runner")`.

### Pepper migration

The actual live config (`~/.agent-core/agent_core.yaml`) is not in the repo; the migration is operator-performed. The in-repo deliverable is:
- `docs/examples/endpoints.d/pepper.yaml` — canonical template for what Pepper's fragment should contain (four endpoints; serves as the operator reference)
- Updated `docs/examples/pepper-agent-core.yaml` — removes the four being endpoints (leaving only `handoff-jobs`), adds a comment pointing to the fragment

In the fragment, the `pepper` endpoint's `briefs_orchestrator` param should reference `briefs.pepper` (not `briefs.orchestrator` as in the current monolith example — this corrects the naming inconsistency).

## Sub-requests (topologically sorted)

1. **Add logger to `packages/core/src/agent_core/bus/runner.py`**
   - Add `import logging` at the top (with existing imports)
   - Add `logger = logging.getLogger(__name__)` after the imports, before `class BusBootError`

2. **Add `_EntryBusBootError` to `runner.py`**
   - After the `BusBootError` class definition, add:
     ```python
     class _EntryBusBootError(BusBootError):
         """Degradable per-entry validation failure.

         Raised instead of plain BusBootError so that the per-entry try/except
         in build_bus_from_config can catch entry-level failures without silencing
         name collisions (which raise plain BusBootError via bus.register()).
         """
     ```

3. **Degrade fragment load in `build_bus_from_config`**
   Replace the current fragment-load block (`runner.py` lines 57-65) with:
   ```python
   for fragment_path in sorted(fragments_dir.glob("*.yaml")):
       try:
           fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
       except yaml.YAMLError as exc:
           logger.error(
               "endpoints.d fragment %r: YAML parse error — quarantining fragment, "
               "boot continues: %s",
               fragment_path.name,
               exc,
           )
           continue
       fragment_endpoints = fragment.get("endpoints", []) or []
       if not isinstance(fragment_endpoints, list):
           logger.error(
               "endpoints.d fragment %r: 'endpoints' must be a list, got %r — "
               "quarantining fragment, boot continues",
               fragment_path.name,
               type(fragment_endpoints).__name__,
           )
           continue
       raw.setdefault("endpoints", []).extend(fragment_endpoints)
   ```

4. **Degrade endpoint entry load in `build_bus_from_config`**
   Wrap the existing per-entry `for` loop so validation errors degrade rather than kill boot. Replace all `raise BusBootError(...)` in the endpoint processing block with `raise _EntryBusBootError(...)`, then wrap the entire per-entry block:
   ```python
   for entry in raw.get("endpoints", []) or []:
       name_hint = entry.get("name", "<unknown>") if isinstance(entry, dict) else "<non-dict>"
       type_hint = entry.get("type", "<unknown>") if isinstance(entry, dict) else "<non-dict>"
       try:
           if "type" not in entry:
               raise _EntryBusBootError(
                   f"endpoint entry missing required 'type' field: {entry!r}"
               )
           if "name" not in entry:
               raise _EntryBusBootError(
                   f"endpoint entry missing required 'name' field: {entry!r}"
               )
           endpoint_type = str(entry["type"])
           cls = endpoint_types.get(endpoint_type)
           if cls is None:
               raise _EntryBusBootError(f"unknown endpoint type: {endpoint_type!r}")
           params = entry.get("params", {})
           constructor_params = {k: v for k, v in params.items() if k not in reserved_params}
           try:
               instance = cls(name=entry["name"], **constructor_params)
           except Exception as exc:
               raise _EntryBusBootError(
                   f"endpoint type {endpoint_type!r} does not satisfy Endpoint protocol: {exc}"
               ) from exc
           if not isinstance(instance, Endpoint):
               raise _EntryBusBootError(
                   f"endpoint type {endpoint_type!r} does not satisfy Endpoint protocol"
               )
           plugin_manager.hook.configure_endpoint_instance(
               instance=instance,
               endpoint_name=entry["name"],
               endpoint_config=entry,
               services=services,
           )
           try:
               bus.register(
                   EndpointSpec(endpoint=instance, description=entry.get("description", ""))
               )
           except ValueError as exc:
               # Name collision: loud config conflict, NOT an _EntryBusBootError.
               raise BusBootError(str(exc)) from exc
       except _EntryBusBootError as exc:
           logger.error(
               "endpoint entry name=%r type=%r: %s — skipping entry, boot continues",
               name_hint,
               type_hint,
               exc,
           )
           continue
   ```
   Note: the `endpoints_by_name` dict and `raw_endpoint_configs` dict that are built after this loop for `apply_endpoint_wiring` will naturally exclude skipped entries because skipped entries were never registered on the bus. No change needed to the wiring step.

5. **Update `test_endpoints_d_malformed_fragment_raises_with_filename`**
   In `packages/core/tests/bus/test_runner_endpoints_d.py`:
   - Rename the function to `test_endpoints_d_malformed_fragment_quarantined_boot_continues`
   - Add `build_bus, caplog` as parameters
   - Replace `with pytest.raises(BusBootError, match="bad.yaml"):` with:
     ```python
     with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
         bus, _http = await build_bus(config_path)
     try:
         assert any("bad.yaml" in r.message for r in caplog.records)
     finally:
         await bus.stop()
     ```
   - Add `import logging` to the imports in `test_runner_endpoints_d.py`
   - The `test_endpoints_d_collision_raises_loudly` test is **not changed** — name collisions remain loud.

6. **Add degraded-fragment tests in `test_runner_endpoints_d.py`**
   Add after the existing tests:
   ```python
   @pytest.mark.asyncio
   async def test_yaml_parse_error_quarantines_fragment_boot_continues(build_bus, tmp_path, caplog):
       """A YAML-broken fragment is quarantined; other fragments and main endpoints load."""
       main_yaml = tmp_path / "agent_core.yaml"
       main_yaml.write_text(
           'bus:\n  storage_path: ":memory:"\n'
           'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
           'endpoints:\n  - type: builtin.stub\n    name: main-stub\n'
       )
       frag_dir = tmp_path / "endpoints.d"
       frag_dir.mkdir()
       (frag_dir / "broken.yaml").write_text(": invalid: yaml syntax [[[\n")
       (frag_dir / "good.yaml").write_text(
           'endpoints:\n  - type: builtin.stub\n    name: good-frag-stub\n'
       )
       with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
           bus, _http = await build_bus(main_yaml)
       try:
           names = {ep.name for ep in bus._endpoints()}
           assert "main-stub" in names
           assert "good-frag-stub" in names
           assert any("broken.yaml" in r.message for r in caplog.records)
       finally:
           await bus.stop()
   ```

7. **Add degraded-entry tests in `test_runner_endpoints_d.py`**
   Add the following functions after the fragment tests. Each uses `tmp_path` to write a YAML inline. Each verifies the bad entry is absent, the good sibling is present, and an error was logged:

   ```python
   @pytest.mark.asyncio
   async def test_entry_missing_type_skipped_sibling_loads(build_bus, tmp_path, caplog):
       """Entry missing 'type' is skipped; sibling entries load."""
       main_yaml = tmp_path / "agent_core.yaml"
       main_yaml.write_text(
           'bus:\n  storage_path: ":memory:"\n'
           'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
           'endpoints:\n'
           '  - name: no-type-ep\n    params: {}\n'
           '  - type: builtin.stub\n    name: good-ep\n'
       )
       with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
           bus, _http = await build_bus(main_yaml)
       try:
           names = {ep.name for ep in bus._endpoints()}
           assert "good-ep" in names
           assert "no-type-ep" not in names
           assert any("no-type-ep" in r.message or "'type'" in r.message for r in caplog.records)
       finally:
           await bus.stop()


   @pytest.mark.asyncio
   async def test_entry_missing_name_skipped_sibling_loads(build_bus, tmp_path, caplog):
       """Entry missing 'name' is skipped; sibling entries load."""
       main_yaml = tmp_path / "agent_core.yaml"
       main_yaml.write_text(
           'bus:\n  storage_path: ":memory:"\n'
           'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
           'endpoints:\n'
           '  - type: builtin.stub\n    params: {}\n'
           '  - type: builtin.stub\n    name: good-ep\n'
       )
       with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
           bus, _http = await build_bus(main_yaml)
       try:
           names = {ep.name for ep in bus._endpoints()}
           assert "good-ep" in names
           assert any("'name'" in r.message or "missing" in r.message for r in caplog.records)
       finally:
           await bus.stop()


   @pytest.mark.asyncio
   async def test_entry_unknown_type_skipped_sibling_loads(build_bus, tmp_path, caplog):
       """Entry with unknown type is skipped; sibling entries load."""
       main_yaml = tmp_path / "agent_core.yaml"
       main_yaml.write_text(
           'bus:\n  storage_path: ":memory:"\n'
           'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
           'endpoints:\n'
           '  - type: no.such.endpoint.Type\n    name: bad-ep\n'
           '  - type: builtin.stub\n    name: good-ep\n'
       )
       with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
           bus, _http = await build_bus(main_yaml)
       try:
           names = {ep.name for ep in bus._endpoints()}
           assert "good-ep" in names
           assert "bad-ep" not in names
           assert any(
               "bad-ep" in r.message or "no.such.endpoint.Type" in r.message
               for r in caplog.records
           )
       finally:
           await bus.stop()


   @pytest.mark.asyncio
   async def test_entry_construction_failure_skipped_sibling_loads(
       build_bus, tmp_path, monkeypatch, caplog
   ):
       """Entry whose constructor raises is skipped; sibling entries load.

       Uses the _FakePM pattern from test_runner.py::test_plugin_can_resolve_endpoint_class
       to inject a test.fail type that always fails construction.
       """
       from typing import Any

       class _FailEndpoint:
           def __init__(self, *, name: str, **_: Any) -> None:
               raise RuntimeError("intentional constructor failure")

       class _OkEndpoint:
           def __init__(self, *, name: str, **_: Any) -> None:
               self.name = name

           async def start(self, bus) -> None: ...
           async def deliver(self, envelope) -> None: ...
           async def stop(self) -> None: ...

       class _HookImpl:
           @staticmethod
           def register_endpoint_types() -> dict:
               return {"test.fail": _FailEndpoint, "test.ok": _OkEndpoint}

           @staticmethod
           def validate_config(*, raw_config: dict) -> None:
               return None

           @staticmethod
           def register_bus_hook_types() -> dict:
               return {}

           @staticmethod
           def register_hook_tool_types() -> dict:
               return {}

           @staticmethod
           def configure_endpoint_instance(*, instance, endpoint_name, endpoint_config, services):
               return None

           @staticmethod
           def configure_bus_hook_instance(*, instance, stage, hook_config, services):
               return None

           @staticmethod
           def wire_endpoints_after_registration(*, endpoints, raw_endpoint_configs, services):
               return None

           @staticmethod
           def reserved_endpoint_params() -> list:
               return []

           @staticmethod
           def register_bus_log_projectors() -> dict:
               return {}

           @staticmethod
           def register_cli_subapps(app) -> None:
               return None

           @staticmethod
           def register_envelope_renderers() -> dict:
               return {}

       class _FakePM:
           hook = _HookImpl()

       monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _FakePM())

       main_yaml = tmp_path / "agent_core.yaml"
       main_yaml.write_text(
           'bus:\n  storage_path: ":memory:"\n'
           'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
           'endpoints:\n'
           '  - type: test.fail\n    name: will-fail\n'
           '  - type: test.ok\n    name: good-ep\n'
       )
       with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
           bus, _http = await build_bus(main_yaml)
       try:
           names = {ep.name for ep in bus._endpoints()}
           assert "good-ep" in names
           assert "will-fail" not in names
           assert any("will-fail" in r.message for r in caplog.records)
       finally:
           await bus.stop()
   ```

8. **Create `docs/examples/endpoints.d/pepper.yaml`**
   New file — canonical reference for the Pepper being fragment. Worker should create `docs/examples/endpoints.d/` directory first (it does not currently exist). Content:
   ```yaml
   # Pepper's being endpoints — contributed by this fragment to the daemon config
   # at ~/.agent-core/ via conf.d-style merge (bus/runner.py).
   #
   # The monolith (agent_core.yaml) retains infra and system endpoints:
   #   scheduler, handoff-jobs, stub, and test ones.
   # Only Pepper's being-scoped endpoints live here.
   #
   # Live path: ~/.agent-core/endpoints.d/pepper.yaml
   # Regenerate with: agent-core hatchery regenerate pepper  (future Cluster β feature)
   endpoints:
     - type: builtin.claude_code_mcp
       name: pepper
       description: "Pepper's MCP endpoint."
       params:
         mount: /mcp/pepper
         briefs_orchestrator: briefs.pepper

     - type: builtin.briefs_orchestrator
       name: briefs.pepper
       description: "Pepper's briefs orchestrator."
       params:
         playbooks_path: "C:\\Users\\jeffr\\.pepper\\Memory\\playbooks"
         fetcher_paths:
           - "C:\\Users\\jeffr\\.pepper\\Memory\\briefs\\fetchers"
         destination_paths:
           - "C:\\Users\\jeffr\\.pepper\\Memory\\briefs\\destinations"
         audit_log_path: "~/.agent-core/briefs/audit.jsonl"
         vars:
           agent_root: "C:\\Users\\jeffr\\.pepper"
         default_target_agent: "pepper"

     - type: builtin.discord
       name: discord-pepper
       description: "Discord adapter for Pepper."
       params:
         target: pepper
         token_env: PEPPER_DISCORD_TOKEN
         env_file: "~/.agent-core/discord-pepper.env"

     - type: builtin.webcam
       name: webcam-pepper
       description: "Pepper's webcam endpoint."
       params:
         enabled: true
         captures_root: "~/.agent-core/webcam/pepper"
         audit_log_path: "~/.agent-core/webcam/pepper/audit.jsonl"
         default_camera_index: 0
         default_resolution: [1280, 720]
         max_resolution: [3840, 2160]
         capture_timeout_seconds: 3.0
   ```

9. **Update `docs/examples/pepper-agent-core.yaml`**
   Remove the `pepper` and `briefs.orchestrator` endpoints from the `endpoints:` list (leaving only `handoff-jobs`). Add a comment at the top of the `endpoints:` block:
   ```yaml
   endpoints:
     # System / infra endpoints only — Pepper's being endpoints live in
     # endpoints.d/pepper.yaml, conf.d-merged at daemon startup.
     - type: builtin.handoff_jobs
       name: handoff-jobs
       params:
         mount: /internal/handoff-jobs
   ```
   The `pipelines:` section and the `bus_hooks:` section are unchanged.

## File-level changes

| File | Change |
|------|--------|
| `packages/core/src/agent_core/bus/runner.py` | **Modify** — add `import logging`, `logger = logging.getLogger(__name__)`, `_EntryBusBootError`; replace fragment error from raise to log+continue; wrap per-entry processing with try/except |
| `packages/core/tests/bus/test_runner_endpoints_d.py` | **Modify** — add `import logging`; rename + update `test_endpoints_d_malformed_fragment_raises_with_filename`; add 5 new tests: yaml-error-quarantine, missing-type-skip, missing-name-skip, unknown-type-skip, construction-failure-skip |
| `docs/examples/endpoints.d/pepper.yaml` | **New** — canonical Pepper being-endpoint fragment (four endpoints) |
| `docs/examples/pepper-agent-core.yaml` | **Modify** — remove `pepper` and `briefs.orchestrator` endpoints; add comment directing to fragment |

## Alternatives considered

1. **Degrade at the fragment level only (not per-entry)**: Quarantine entire fragments when any entry in them fails, rather than skipping individual bad entries. Simpler implementation — no `_EntryBusBootError` subclass needed. Ruled out: overkill quarantine. If a fragment has 4 Pepper endpoints and one has a typo'd param, quarantining the whole fragment drops three healthy endpoints. The design doc explicitly says "a bad *entry* is logged and skipped" — the granularity is per-entry.

2. **Use a `skip_entry` boolean sentinel instead of `_EntryBusBootError` subclass**: Track whether a BusBootError is a name-collision vs. a validation error via a local flag before `bus.register()`. Ruled out: fragile — requires careful ordering of the flag assignment and the exception raise; the subclass approach is explicit about which BusBootErrors are degradable and requires no ordering discipline.

3. **Apply degraded load only to fragment entries, not monolith entries**: A typo in the monolith could arguably be "louder" than one in a fragment, since the monolith owns the whole-system config. Ruled out: inconsistent UX. If the bus survives a bad `wren.yaml` entry, it should also survive a bad entry in `agent_core.yaml`. "A bad entry drops just that endpoint" is the invariant regardless of source.

4. **Operator migration runbook in `docs/cutover/` instead of example files**: Write a step-by-step Markdown runbook for moving Pepper's endpoints to the fragment. Ruled out: the example files in `docs/examples/` serve as the canonical operator reference (they're what `docs/guides/add-an-endpoint.md` links to); a separate cutover doc creates a maintenance burden. The example update + inline comments are sufficient.

## Open questions

1. **Cα-1 exception type**: This spec assumes per-entry validation errors raise `BusBootError` (current) or `_EntryBusBootError` (new subclass). If Cα-1's Pydantic schema integration raises `pydantic.ValidationError` separately from the existing `BusBootError` chain, the Worker executing Cα-2 must also catch `pydantic.ValidationError` in the per-entry except clause. The Worker should read Cα-1's implementation before writing the catch clause.

2. **`configure_endpoint_instance` failures**: Currently, if `plugin_manager.hook.configure_endpoint_instance(...)` raises, it propagates uncaught. Should these plugin-wiring failures also be degradable? Not addressed by the issue, so left as-is (propagate loudly). An open follow-up if this becomes a pain point.

## Out of scope

- Pydantic daemon-config schema itself — Cα-1's responsibility; this ticket adds graceful error handling on top of whatever schema Cα-1 produces.
- Config hygiene / drift detection (`daemon doctor` extension) — Cα-3 (#321).
- Hatchery correctness (generate correct `endpoints.d/<being>.yaml` from hatch config) — Cluster β.
- Pruning drift debris files (`wren.yaml.bak-20260702-with-voice`, `testbeing.yaml.cleanup-2026-05-10`, `agent_core-cputest.yaml`) — Cα-3 (#321).
- Wren's `inbound` endpoint living in `endpoints.d/wren.yaml` — explicitly left as-is per D1: "minor known wart, not worth churning".
- Voice/webcam param changes or additions — out of scope; use existing hatchery defaults in the Pepper example fragment.
