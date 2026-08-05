# Spec: non-loopback bind gate (has_auth_hook ← bus_auth_mode != "off") (issue #505)

## Goal

Complete Dβ-2c of the bus transport auth cluster: verify that `has_auth_hook` is derived from `bus_auth_mode` (not hardcoded `False`), update the stale `BACKLOG` comments that predate this wiring, tighten the `_validate_http` error message to name the fix, and add the two missing tests that prove a non-loopback bind is permitted once `bus_auth_mode` is `warn` or `enforce`. See issue #505 and the design doc at `docs/superpowers/specs/2026-07-15-bus-transport-auth-design.md` (§Architecture/2, §Ticket decomposition Dβ-2).

## Acceptance criteria

- `packages/core/src/agent_core/bus/runner.py`: `has_auth_hook` is assigned as `daemon_cfg.bus_auth_mode != "off"` (not hardcoded `False`).
- The module docstring of `runner.py` (first six lines) no longer contains the text `BACKLOG: auth for non-loopback bind`.
- The `BusBootError` raised by `_validate_http` no longer references `BACKLOG`; the message names `bus_auth_mode` and tells the operator how to fix the configuration.
- `packages/core/tests/bus/test_runner.py` contains `test_non_loopback_bind_warn_mode_permitted`: calling `build_bus_from_config` with `bind_host="0.0.0.0"` and `bus_auth_mode="warn"` raises no error.
- `packages/core/tests/bus/test_runner.py` contains `test_non_loopback_bind_enforce_mode_permitted`: calling `build_bus_from_config` with `bind_host="0.0.0.0"` and `bus_auth_mode="enforce"` raises no error.
- The existing `test_non_loopback_bind_refused` test (default `bus_auth_mode="off"`) still passes after the error message update.
- `just check` exits 0.

## Approach

No GoF pattern applies — this is a straightforward feature completion under SRP. The single responsible unit is `_validate_http` + the `has_auth_hook` derivation immediately above it. The design doc (§Architecture/2) specifies exactly this coupling: "`has_auth_hook` flips from a hardcoded `False` to `bus_auth_mode != 'off'`, so `_validate_http` will permit a non-loopback bind only once auth is actually enforced — the loopback invariant and the auth mode become a single coupled decision."

**Current state.** The implementation of the code change is already in `packages/core/src/agent_core/bus/runner.py` at line 167:

```python
has_auth_hook = daemon_cfg.bus_auth_mode != "off"
```

This was added as part of the Dβ-2b middleware work. The `bus_auth_mode` config field and the auth middleware (`BusAuthMiddleware`) are both present and tested. What remains is:

1. Two stale `BACKLOG` strings that now mislead maintainers into thinking the feature is unimplemented.
2. Two test gaps: `warn` and `enforce` modes at the non-loopback gate are untested.

**Error message.** The existing `BusBootError` in `_validate_http` says `"v1 supports loopback only; see BACKLOG for the auth hook trigger."` That advice is now wrong — the auth hook is implemented. The replacement must name `bus_auth_mode` and tell the operator what value to set, so the error is self-healing.

**Tests.** The existing `test_non_loopback_bind_refused` covers `bus_auth_mode="off"` (the default) with a non-loopback bind. It uses `build_bus_from_config` directly because the error fires before the bus object is created. The two new tests do the opposite: the guard passes, a bus object is returned, and the `build_bus` fixture (conftest.py:54-79) handles cleanup. With `endpoints: []`, no `MCPHostable` objects exist, so `http_host` is `None` — the tests can assert on that as a sanity check.

## Sub-requests (topologically sorted)

1. **Verify `has_auth_hook` in `runner.py`.** At `packages/core/src/agent_core/bus/runner.py` around line 167, confirm the assignment reads:
   ```python
   has_auth_hook = daemon_cfg.bus_auth_mode != "off"
   ```
   If it still reads `has_auth_hook = False`, change it to the above. The surrounding comment block (lines 164–166) already describes this relationship; leave the comment intact.

2. **Remove the `BACKLOG` reference from the `runner.py` module docstring.** The current docstring (lines 1–6) ends with:
   ```
   enforces v1 invariants: loopback-only bind unless an auth hook is configured
   (BACKLOG: auth for non-loopback bind).
   ```
   Replace the parenthetical so the docstring reads:
   ```
   enforces v1 invariants: loopback-only bind unless auth is active
   (bus_auth_mode != "off").
   ```

3. **Update the `_validate_http` error message.** In `_validate_http` (lines 53–58 of `runner.py`), change the `BusBootError` raise from:
   ```python
   raise BusBootError(
       f"http.bind_host={http_cfg.bind_host!r} is non-loopback but no auth hook is configured. "
       "v1 supports loopback only; see BACKLOG for the auth hook trigger."
   )
   ```
   to:
   ```python
   raise BusBootError(
       f"http.bind_host={http_cfg.bind_host!r} is non-loopback but bus_auth_mode is 'off'. "
       "Set bus_auth_mode to 'warn' or 'enforce' in the daemon YAML to permit a non-loopback bind."
   )
   ```
   The word "loopback" still appears in "non-loopback", so the existing `test_non_loopback_bind_refused` (which uses `match="loopback"`) continues to pass without modification.

4. **Add `test_non_loopback_bind_warn_mode_permitted` to `packages/core/tests/bus/test_runner.py`.** Inside `class TestRunner`, add:
   ```python
   async def test_non_loopback_bind_warn_mode_permitted(self, tmp_path: Path, build_bus):
       config = {
           "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
           "bus_auth_mode": "warn",
           "endpoints": [],
       }
       p = tmp_path / "warn_cfg.yaml"
       p.write_text(yaml.dump(config))
       bus, http = await build_bus(p)  # must not raise BusBootError
       assert http is None  # no MCPHostable endpoints configured
   ```

5. **Add `test_non_loopback_bind_enforce_mode_permitted` to `packages/core/tests/bus/test_runner.py`.** Inside `class TestRunner`, add:
   ```python
   async def test_non_loopback_bind_enforce_mode_permitted(self, tmp_path: Path, build_bus):
       config = {
           "http": {"bind_host": "0.0.0.0", "bind_port": 8788},
           "bus_auth_mode": "enforce",
           "endpoints": [],
       }
       p = tmp_path / "enforce_cfg.yaml"
       p.write_text(yaml.dump(config))
       bus, http = await build_bus(p)  # must not raise BusBootError
       assert http is None  # no MCPHostable endpoints configured
   ```

6. **Run the gate.**
   ```bash
   just check
   ```
   Expected: all tests pass, coverage ≥ 85%, patch coverage ≥ 80%.

## File-level changes

| File | Change | What changes |
|---|---|---|
| `packages/core/src/agent_core/bus/runner.py` | Modify | (a) Verify `has_auth_hook = daemon_cfg.bus_auth_mode != "off"` (no-op if already correct); (b) update module docstring to remove `BACKLOG: auth for non-loopback bind`; (c) update `_validate_http` error message to reference `bus_auth_mode` instead of `BACKLOG` |
| `packages/core/tests/bus/test_runner.py` | Modify | Add `test_non_loopback_bind_warn_mode_permitted` and `test_non_loopback_bind_enforce_mode_permitted` inside `class TestRunner` |

No other files change. No new imports are needed in `test_runner.py` (it already imports `build_bus_from_config`, `BusBootError`, `yaml`, and `Path`; the new tests use `build_bus` which is already a conftest fixture).

## Alternatives considered

1. **Move the bind-gate check into a Pydantic `@model_validator` on `DaemonConfig`.** Both `http.bind_host` and `bus_auth_mode` are on `DaemonConfig`, so validation is structurally possible there. However, Pydantic validators raise `pydantic.ValidationError`, not `BusBootError` — breaking the existing test and all existing error-handling callers that catch `BusBootError`. All other boot-time cross-field checks in `runner.py` also happen post-parse (e.g., mcp_audit timezone resolution, hook-type resolution). Moving this one rule into Pydantic for no functional gain would be inconsistent. Ruled out.

2. **Permit non-loopback only under `enforce`, not `warn`.** In `warn` mode the middleware still passes unauthenticated requests (it only logs them), so the bind_host guard in `warn` mode offers only partial security. The design spec (decision 3, §Architecture/2) explicitly specifies that both `warn` and `enforce` unlock non-loopback: `warn` is the migration window where operators run in production to confirm every being authenticates before flipping to `enforce`. Tightening the guard to `enforce`-only would break the migration rollout path. Ruled out.

3. **Add a third test for loopback + `bus_auth_mode="off"` explicitly.** The existing `cfg_path` fixture and all happy-path tests in `TestRunner` bind to `127.0.0.1` with default `bus_auth_mode="off"` — loopback is already covered implicitly by the entire suite. An explicit test would be redundant and signal incomplete understanding of what this ticket adds. Ruled out.

## Open questions

None. The implementation is already in place; the codebase is fully grounded.

## Out of scope

- ASGI `BusAuthMiddleware` behavior under `warn`/`enforce` — covered by `test_bus_auth_middleware.py` (Dβ-2b).
- Busproxy outbound signing (Dβ-3).
- Hatchery keypair provisioning (Dβ-4).
- Migration for existing beings (Dβ-5).
- Any change to `DaemonConfig`, `HttpConfig`, or `EndpointEntryConfig` schema — those are already correct.
- The `http_host.py` module docstring (it says "Loopback bind only is enforced upstream in `bus/runner.py`" — this remains accurate; no change needed).
