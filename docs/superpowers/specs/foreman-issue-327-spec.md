# Spec: hatch→run handoff — venv build + `.mcp.json` generation + daemon reload/probe (issue #327)

## Goal

Complete the Cβ-3 cluster ticket so a "successful hatch" produces a live, wired being: build the being's slim sidecar venv (C2-1), write the canonical `.mcp.json` via C2-2's generator, and replace `daemon_check = "skipped"` with a real daemon reload + HTTP health probe that fails loudly when the endpoint is not registered. Closes the `[P1][S]` `.mcp.json`-never-generated item and the `[P2][S]` no-hatch→run-handoff item from the hatchery-correctness design.

Design authority: [`docs/superpowers/specs/2026-07-14-hatchery-correctness-design.md`](docs/superpowers/specs/2026-07-14-hatchery-correctness-design.md) (D1 + D4). Issue: https://github.com/jeffrichley/agent_core/issues/327.

**Blocked by #315 and #316.** The Worker must not begin until both blockers are merged and their modules (`agent_core.venv.builder` from #315 and C2-2's generator from #316) are present in the workspace.

## Acceptance criteria

- **Venv build step in hatcher**: `Hatcher.hatch()` calls C2-1's `build_being_venv(being_name_lower)` (from `agent_core.venv.builder`) as a new step inside the `if not self._config.init_missing:` block. The stable venv path (`~/.<being>/.venv`) is appended to `self._tracked_writes` so rollback removes the symlink/junction on failure.
- **Venv rollback handles symlinks**: `Hatcher._rollback()` gains an `elif path.is_symlink(): path.unlink()` branch before the `is_dir()` check, so the stable venv symlink/junction is correctly removed on rollback without following it into the venv content.
- **`.mcp.json` via C2-2 generator**: `Hatcher.hatch()` calls C2-2's canonical generator (see Open questions for the expected module/function; Worker reads the actual #316 implementation) to write `~/.<being>/.mcp.json` with the stable `~/.<being>/.venv` interpreter path, all three sidecars (`agent-core-busproxy`, `agent-core-channel`, `agent-core-notify`), and the correct `--agent`/`--daemon-url`. The path is appended to `_tracked_writes`.
- **`uvx`-based template retired**: `packages/agent-core-hatchery/templates/config/.mcp.json.j2` is deleted; `"config/.mcp.json.j2"` is removed from `file-classes.yaml`'s `config:` list; `.mcp.json.j2` is removed from `_render_config_tree`'s `dest_map` in `hatcher.py`. C2-2's generator is the sole source of the file.
- **`init_missing` skips new steps**: venv build and `.mcp.json` generation are both guarded by `if not self._config.init_missing:` (same guard as the existing daemon-fragment writes).
- **Dependency injection for testability**: `Hatcher.__init__` accepts two keyword-only arguments `_venv_builder: Callable | None = None` and `_mcp_json_gen: Callable | None = None`. When non-None, these replace the real C2-1 and C2-2 callables respectively; tests use these to avoid subprocess-heavy operations.
- **Daemon probe module**: `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_probe.py` exists and exports `reload_and_probe(config: HatchConfig, *, timeout: float = 15.0, runner=subprocess.run) -> DaemonCheckStatus`.
- **Real daemon reload**: `reload_and_probe` stops the daemon (`agent-core daemon stop` subprocess) and starts it (`agent-core daemon start` subprocess). Both commands are called with `check=False` and `capture_output=True`; failures are swallowed (daemon may not be running); the outcome is determined by the HTTP probe, not the subprocess return code.
- **HTTP health probe**: After starting the daemon, `reload_and_probe` polls `http://{bind_host}:{bind_port}/mcp/{endpoint_name}/` (values read from the daemon config YAML at `config.resolved_daemon_config_dir() / "agent_core.yaml"`) until one of: (a) any non-404 HTTP response → `"reachable_and_registered"`; (b) HTTP 404 → `"reachable_but_missing"`; (c) polling timeout (default 15 s, 0.5 s interval) → `"unreachable"`. Uses stdlib `urllib.request`; no new runtime dependencies.
- **Loud failure on `reachable_but_missing`**: `cli.py` exits with code 1 when `daemon_check_status == "reachable_but_missing"` (daemon up, fragments written, endpoint not visible — a real defect). When `daemon_check_status == "unreachable"`, the hatch succeeds (exit 0) with the report warning (daemon may be intentionally stopped; endpoint registers on next daemon start).
- **CLI wiring**: `daemon_check_status = "skipped"` in `cli.py` is replaced by `daemon_check_status = reload_and_probe(cfg) if not cfg.init_missing else "skipped"`.
- **Tests for new hatcher steps** in `packages/agent-core-hatchery/tests/test_hatcher_venv_mcp.py`:
  - `test_venv_builder_called_with_being_name_lower` — verifies `_venv_builder` is called with `"testbeing"`.
  - `test_stable_venv_path_tracked_for_rollback` — verifies stable venv path is in `_tracked_writes`.
  - `test_mcp_json_gen_called_after_venv_build` — verifies venv build is called before `.mcp.json` generation (call-order check only; path tracking is verified by a separate test).
  - `test_mcp_json_path_tracked_for_rollback` — verifies the path returned by `_mcp_json_gen` is appended to `_tracked_writes`.
  - `test_venv_builder_not_called_on_init_missing` — verifies `_venv_builder` is not called when `init_missing=True`.
  - `test_mcp_json_gen_not_called_on_init_missing` — verifies `_mcp_json_gen` is not called when `init_missing=True`.
  - `test_rollback_removes_stable_venv_symlink` (POSIX only) — creates a real symlink, verifies `_rollback()` removes it.
- **Tests for daemon probe** in `packages/agent-core-hatchery/tests/test_daemon_probe.py`:
  - `test_probe_returns_registered_on_non_404_response`
  - `test_probe_returns_missing_on_404_response`
  - `test_probe_returns_unreachable_on_connection_refused`
  - `test_probe_timeout_returns_unreachable`
  - `test_reload_stops_then_starts_daemon`
  - `test_daemon_http_config_read_from_yaml`
- **Updated `test_hatcher_config.py`**: `test_mcp_json_rendered_at_vault_root` is rewritten to use `_mcp_json_gen=` injection (no real C2-2 call) and removes the `command == "uvx"` assertion; now checks that generator was called and `.mcp.json` exists with the path the stub wrote.
- `just check` passes (ruff + full test suite with coverage ≥ 85 %).

## Approach

No GoF pattern applies cleanly. Guiding principles:

**SRP** — `daemon_probe.py` is a pure-functions module (subprocess calls + HTTP polling); `hatcher.py`'s two new private methods are the dispatch site; `cli.py` owns the "fail loudly" decision. No function does more than one thing.

**DIP** — `Hatcher.__init__` accepts `_venv_builder` and `_mcp_json_gen` callables so tests inject no-ops instead of touching real processes. This mirrors `build_being_venv(runner=…)` from C2-1 and `daemon/release.py:fetcher` from the daemon install path — the established pattern in this repo.

**Step order in `hatch()`**: the venv must exist before the `.mcp.json` generator runs (the generator writes an absolute interpreter path; if the venv doesn't exist the path would be correct but unverified). Both happen after `_render_config_tree()` and before validation. Both are inside `if not self._config.init_missing:`.

**Rollback gap fix**: The existing `_rollback()` only handles regular files and empty directories. A stable venv path is a symlink (POSIX) or directory junction (Windows). `path.is_symlink()` returns `True` on both platforms for symlinks/junctions (Python 3.12+). Adding `elif path.is_symlink(): path.unlink()` before the `is_dir()` branch closes the gap. The versioned venv directory (`~/.<being>/.agent-core/venvs/<version>/`) is NOT rolled back — GC is C2-3's job.

**Daemon config HTTP coordinates**: read from `config.resolved_daemon_config_dir() / "agent_core.yaml"` using `yaml.safe_load`. Key: `raw.get("http", {}).get("bind_host", "127.0.0.1")` and `raw.get("http", {}).get("bind_port", 8789)`. Default to prod port 8789 if the key is absent (config may be minimal). Fallback: if the file does not exist, return `"unreachable"` immediately without attempting restart.

**Subprocess daemon control**: use `["agent-core", "daemon", "stop"]` and `["agent-core", "daemon", "start"]` — the installed CLI script. `FileNotFoundError` (agent-core not on PATH) and `subprocess.TimeoutExpired` are caught and treated as "could not restart"; the probe then times out and returns `"unreachable"`.

**Probe implementation**: `urllib.request.urlopen(url, timeout=2.0)` inside a `time.monotonic()`-bounded loop. `urllib.error.HTTPError` with code 404 → `"reachable_but_missing"`. Any other `HTTPError` (405, 200, etc.) → `"reachable_and_registered"` (daemon is up and endpoint is mounted). `urllib.error.URLError` / `OSError` → retry after 0.5 s sleep until deadline.

**Retiring `.mcp.json.j2`**: removing it from `dest_map` in `_render_config_tree` is a two-line diff. Deleting the file and updating `file-classes.yaml` is required because `test_real_manifest_classifies_all_template_files()` in `test_file_classes.py` walks the real template tree and fails on any classified-but-deleted or present-but-unclassified file. Delete the `.j2` file AND remove its entry from `file-classes.yaml` atomically.

**Updated `test_mcp_json_rendered_at_vault_root`**: the `hatched` fixture already calls `Hatcher(cfg).hatch()`. After our changes, `hatch()` calls the injected builders. The test should pass `_mcp_json_gen=<stub>` via a local fixture or monkeypatch. The stub writes a minimal valid `.mcp.json` to the expected path and returns that path, so downstream assertions can confirm the file exists. Remove the `command == "uvx"` check (C2-2 controls that format now).

## Sub-requests (topologically sorted)

1. **Read C2-1 (#315) and C2-2 (#316) implementations.** Before writing any code, open `packages/core/src/agent_core/venv/builder.py` and C2-2's module (Worker: locate it in the repo — the design doc calls it `agent_core.mcp.generator` or similar; read #316's spec and find the actual path). Write down: (a) exact import path for `build_being_venv`; (b) exact import path, function name, and signature for C2-2's generator. These determine the lazy-import lines in `hatcher.py` and the DI callable types.

2. **Fix `Hatcher._rollback()` to handle symlinks** in `hatcher.py`.
   Insert before the `elif path.is_dir()` branch:
   ```python
   elif path.is_symlink():
       path.unlink()
   ```

3. **Delete `packages/agent-core-hatchery/templates/config/.mcp.json.j2`** (file delete, not edit).

4. **Remove `"config/.mcp.json.j2"` from `packages/agent-core-hatchery/templates/file-classes.yaml`** — locate the `config:` list entry and delete that line.

5. **Update `Hatcher.__init__` in `hatcher.py`** to accept `_venv_builder` and `_mcp_json_gen` keyword-only arguments (both `Callable | None = None`). Store as `self._venv_builder` and `self._mcp_json_gen`.

6. **Add `Hatcher._build_being_venv(self, result: HatchResult) -> None`** in `hatcher.py`. Uses `self._venv_builder` if set, else lazy-imports `build_being_venv` from `agent_core.venv.builder`. Appends stable venv path to `self._tracked_writes` and `result.files_written`.

7. **Add `Hatcher._generate_mcp_json(self, result: HatchResult) -> None`** in `hatcher.py`. Uses `self._mcp_json_gen` if set, else lazy-imports C2-2's generator (Worker: use the module path from step 1). Appends returned path to `self._tracked_writes` and `result.files_written`.

8. **Update `Hatcher.hatch()` in `hatcher.py`**: inside the `if not self._config.init_missing:` block, after the daemon-fragment writes (`DaemonConfigWriter(self._config).write_all()`), add:
   ```python
   self._build_being_venv(result)
   self._generate_mcp_json(result)
   ```

9. **Remove `".mcp.json.j2"` from `_render_config_tree`'s `dest_map`** in `hatcher.py`. Four-entry dict becomes three-entry dict.

10. **Create `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_probe.py`**. See File-level changes for exact content.

11. **Update `cli.py`** — import `reload_and_probe` from `agent_core_hatchery.daemon_probe`; replace:
    ```python
    daemon_check_status = "skipped"  # Phase 5 keeps this simple; live daemon check is Phase 6 e2e.
    ```
    with:
    ```python
    if not cfg.init_missing:
        daemon_check_status = reload_and_probe(cfg)
    else:
        daemon_check_status = "skipped"
    ```
    After `write_hatching_report(...)`, add:
    ```python
    if daemon_check_status == "reachable_but_missing":
        typer.secho(
            "⚠  Daemon probe: endpoint not registered after reload. See HATCHING-REPORT.md.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    ```

12. **Create `packages/agent-core-hatchery/tests/test_hatcher_venv_mcp.py`**. See File-level changes for exact content.

13. **Create `packages/agent-core-hatchery/tests/test_daemon_probe.py`**. See File-level changes for exact content.

14. **Inject DI stubs into all bare `Hatcher(cfg)` callsites without stubs** — three files need updating:

    **`test_hatcher_basic.py`**: Add these module-level stubs near the top of the file (after imports, before the first test):
    ```python
    def _noop_venv_builder(target: str) -> Path:
        return Path.home() / f".{target}" / ".venv"

    def _noop_mcp_gen(**kwargs) -> Path:
        return Path.home() / ".fake" / ".mcp.json"
    ```
    Inject both into every `Hatcher(cfg)` call with `init_missing=False` — the callsites at lines 19, 48, 52, 62, 74, 97, 114, and 139. Pattern:
    ```python
    # before
    Hatcher(cfg).hatch()
    # after
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()
    ```
    The `test_hatch_refuses_if_vault_exists` test has two calls: both the first hatch (line 48) and the re-hatch (line 52) need stubs injected; the second call raises `VaultExistsError` but still needs the stubs so the constructor is valid. The topup call at line 82 (`init_missing=True`) needs no stubs — the new steps are guarded.

    **`test_hatcher_config.py`**: Update the `hatched` fixture (line 21) per the fixture snippet in the Modifications section. Additionally, inject stubs into the two standalone first-hatch calls in:
    - `test_init_missing_preserves_user_edits_to_config` (line 107): `Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_stub_mcp_gen_for_fixture(cfg.resolved_vault_root())).hatch()`
    - `test_init_missing_restores_deleted_config` (line 131): same pattern.
    Both files share the `_noop_venv_builder` helper and `_stub_mcp_gen_for_fixture` already defined for the fixture.

    Also update `test_mcp_json_rendered_at_vault_root` per the exact replacement snippet in the Modifications section — remove the `command == "uvx"` assertion; keep the three-sidecar and file-exists assertions.

    **`test_validators.py`**: Inject stubs into the `_hatched_cfg` helper's `Hatcher(cfg)` call at line 21:
    ```python
    def _hatched_cfg(tmp_path):
        cfg = HatchConfig(
            being_name="TestBeing",
            primary_human_name="Tester",
            vault_root=str(tmp_path),
            daemon_config_dir=str(tmp_path / ".agent-core"),
        )
        Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=lambda **kw: tmp_path / ".mcp.json",
        ).hatch()
        return cfg
    ```
    The `_mcp_json_gen` lambda returns a path without writing the file — validator tests only inspect daemon fragments, not `.mcp.json`. If any validator test is later updated to check `.mcp.json`, switch to a writing stub. Also add `from pathlib import Path` to the imports if not already present.

15. **Run `just check`** — fix any lint/coverage gaps.

## File-level changes

| File | Change |
|------|--------|
| `packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py` | **Modify** — (a) extend `__init__` with `_venv_builder`/`_mcp_json_gen` DI kwargs; (b) add `_build_being_venv` and `_generate_mcp_json` methods; (c) call both in `hatch()` inside `if not self._config.init_missing:`; (d) remove `".mcp.json.j2"` from `_render_config_tree`'s `dest_map`; (e) fix `_rollback()` for symlinks |
| `packages/agent-core-hatchery/src/agent_core_hatchery/cli.py` | **Modify** — import `reload_and_probe`; replace `daemon_check_status = "skipped"` with conditional probe call; exit 1 on `reachable_but_missing` |
| `packages/agent-core-hatchery/templates/config/.mcp.json.j2` | **Delete** — retired; C2-2 generator owns `.mcp.json` creation going forward |
| `packages/agent-core-hatchery/templates/file-classes.yaml` | **Modify** — remove `"config/.mcp.json.j2"` entry from `config:` list |
| `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_probe.py` | **New** — `read_daemon_http_config`, `reload_and_probe`, and supporting helpers |
| `packages/agent-core-hatchery/tests/test_hatcher_venv_mcp.py` | **New** — unit tests for venv-build and mcp-json-gen steps in hatcher |
| `packages/agent-core-hatchery/tests/test_daemon_probe.py` | **New** — unit tests for daemon_probe.py |
| `packages/agent-core-hatchery/tests/test_hatcher_basic.py` | **Modify** — add module-level `_noop_venv_builder` and `_noop_mcp_gen` stubs; inject both into all 8 bare `Hatcher(cfg)` calls with `init_missing=False` (lines 19, 48, 52, 62, 74, 97, 114, 139) |
| `packages/agent-core-hatchery/tests/test_hatcher_config.py` | **Modify** — (a) update `hatched` fixture to inject stubs for `_venv_builder` / `_mcp_json_gen`; (b) inject stubs into the two standalone first-hatch calls in `test_init_missing_preserves_user_edits_to_config` (line 107) and `test_init_missing_restores_deleted_config` (line 131); (c) update `test_mcp_json_rendered_at_vault_root` to remove uvx assertion |
| `packages/agent-core-hatchery/tests/test_validators.py` | **Modify** — inject no-op stubs into the `_hatched_cfg` helper's `Hatcher(cfg)` call (line 21) |

### Exact content: `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_probe.py`

```python
"""Daemon reload and endpoint health-probe for hatch→run handoff (Cβ-3, issue #327).

SRP: this module does two things only — restart the daemon process and HTTP-probe the
new being's endpoint. It imports nothing from agent_core_hatchery to avoid cycles.
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.report import DaemonCheckStatus


def read_daemon_http_config(daemon_config_dir: Path) -> tuple[str, int]:
    """Parse agent_core.yaml for the bus HTTP bind host and port.

    Returns (bind_host, bind_port). Falls back to ("127.0.0.1", 8789) if
    the file does not exist or the keys are absent.
    """
    config_path = daemon_config_dir / "agent_core.yaml"
    if not config_path.is_file():
        return ("127.0.0.1", 8789)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ("127.0.0.1", 8789)
    http = raw.get("http", {}) if isinstance(raw, dict) else {}
    host = str(http.get("bind_host", "127.0.0.1"))
    port = int(http.get("bind_port", 8789))
    return (host, port)


def _stop_daemon(runner=subprocess.run) -> None:
    """Best-effort daemon stop. Never raises; failures are absorbed."""
    try:
        runner(
            ["agent-core", "daemon", "stop"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass  # agent-core not on PATH or daemon already stopped — probe decides outcome


def _start_daemon(runner=subprocess.run) -> None:
    """Best-effort daemon start. Never raises; failures are absorbed."""
    try:
        runner(
            ["agent-core", "daemon", "start"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass


def _probe_endpoint(
    host: str,
    port: int,
    endpoint_name: str,
    *,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
) -> DaemonCheckStatus:
    """Poll http://{host}:{port}/mcp/{endpoint_name}/ until a response or timeout.

    Returns:
      "reachable_and_registered" — any non-404 HTTP response (endpoint is mounted).
      "reachable_but_missing"    — HTTP 404 (daemon up; endpoint not registered).
      "unreachable"              — polling timed out or connection always refused.
    """
    url = f"http://{host}:{port}/mcp/{endpoint_name}/"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as _resp:
                # Any 2xx or redirect means the endpoint is mounted.
                return "reachable_and_registered"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "reachable_but_missing"
            # Other HTTP error (405, 500, etc.) — server is up, endpoint mounted.
            return "reachable_and_registered"
        except (urllib.error.URLError, OSError):
            # Connection refused / reset — daemon not yet up. Retry.
            time.sleep(poll_interval)

    return "unreachable"


def reload_and_probe(
    config: HatchConfig,
    *,
    timeout: float = 15.0,
    runner=subprocess.run,
) -> DaemonCheckStatus:
    """Stop the daemon, start it, then probe the new being's endpoint.

    Returns DaemonCheckStatus: see report.py for semantics. Called from cli.py after
    Hatcher.hatch() succeeds (fragments + venv + .mcp.json already written).
    """
    daemon_config_dir = config.resolved_daemon_config_dir()

    # Bail early if there is no daemon config to read.
    if not (daemon_config_dir / "agent_core.yaml").is_file():
        return "unreachable"

    host, port = read_daemon_http_config(daemon_config_dir)

    _stop_daemon(runner)
    _start_daemon(runner)

    return _probe_endpoint(
        host,
        port,
        config.endpoint_name,
        timeout=timeout,
        poll_interval=0.5,
    )
```

### Exact content: `packages/agent-core-hatchery/tests/test_hatcher_venv_mcp.py`

```python
"""Unit tests for Hatcher's venv-build and .mcp.json-generation steps (Cβ-3, #327)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher


def _cfg(tmp_path: Path, **extra) -> HatchConfig:
    return HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        **extra,
    )


def _stub_venv_builder(calls: list) -> callable:
    """Stub for _venv_builder: records calls; returns expected stable path."""
    def _build(target: str) -> Path:
        calls.append(target)
        return Path.home() / f".{target}" / ".venv"
    return _build


def _stub_mcp_gen(calls: list, vault: Path) -> callable:
    """Stub for _mcp_json_gen: records calls; writes a valid .mcp.json."""
    def _gen(**kwargs) -> Path:
        calls.append(kwargs)
        p = vault / ".testbeing" / ".mcp.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"mcpServers": {"agent-core-busproxy": {"command": "/fake/.venv/bin/python", "args": []}}}),
            encoding="utf-8",
        )
        return p
    return _gen


class TestBuildBeingVenvStep:
    def test_venv_builder_called_with_being_name_lower(self, tmp_path: Path) -> None:
        calls: list[str] = []
        cfg = _cfg(tmp_path)
        vault = cfg.resolved_vault_root()

        gen_calls: list = []
        hatcher = Hatcher(
            cfg,
            _venv_builder=_stub_venv_builder(calls),
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        )
        hatcher.hatch()

        assert "testbeing" in calls, f"expected 'testbeing' in venv_builder calls, got {calls}"

    def test_stable_venv_path_tracked_for_rollback(self, tmp_path: Path) -> None:
        calls: list[str] = []
        cfg = _cfg(tmp_path)
        gen_calls: list = []

        hatcher = Hatcher(
            cfg,
            _venv_builder=_stub_venv_builder(calls),
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        )
        hatcher.hatch()

        stable = Path.home() / ".testbeing" / ".venv"
        assert stable in hatcher._tracked_writes

    def test_venv_builder_not_called_on_init_missing(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        gen_calls: list = []
        # First hatch (normal)
        Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        calls: list[str] = []
        cfg_topup = cfg.model_copy(update={"init_missing": True})
        Hatcher(
            cfg_topup,
            _venv_builder=_stub_venv_builder(calls),
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        assert calls == [], f"venv_builder must not be called on init_missing; got {calls}"


class TestMcpJsonGenStep:
    def test_mcp_json_gen_called_after_venv_build(self, tmp_path: Path) -> None:
        venv_calls: list[str] = []
        gen_calls: list = []
        cfg = _cfg(tmp_path)

        call_order: list[str] = []

        def recording_venv(target: str) -> Path:
            call_order.append("venv")
            venv_calls.append(target)
            return Path.home() / f".{target}" / ".venv"

        def recording_gen(**kwargs) -> Path:
            call_order.append("mcp_gen")
            gen_calls.append(kwargs)
            p = cfg.resolved_vault_root() / ".mcp.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
            return p

        Hatcher(cfg, _venv_builder=recording_venv, _mcp_json_gen=recording_gen).hatch()

        assert call_order.index("venv") < call_order.index("mcp_gen"), (
            "venv must be built before .mcp.json is generated"
        )

    def test_mcp_json_path_tracked_for_rollback(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        mcp_path = cfg.resolved_vault_root() / ".mcp.json"
        gen_calls: list = []

        hatcher = Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        )
        hatcher.hatch()

        assert mcp_path in hatcher._tracked_writes

    def test_mcp_json_gen_not_called_on_init_missing(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        gen_calls: list = []

        Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        gen_calls.clear()
        cfg_topup = cfg.model_copy(update={"init_missing": True})
        Hatcher(
            cfg_topup,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        assert gen_calls == [], "mcp_json_gen must not be called on init_missing"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink rollback test")
class TestRollbackSymlink:
    def test_rollback_removes_stable_venv_symlink(self, tmp_path: Path) -> None:
        """_rollback() must unlink a symlink without following it into the venv content."""
        import os

        cfg = _cfg(tmp_path)
        # Create a fake stable symlink target (doesn't need to be a real venv)
        venv_target = tmp_path / ".fake_venv_content"
        venv_target.mkdir()
        stable = tmp_path / ".testbeing_stable_link"
        os.symlink(venv_target, stable)

        hatcher = Hatcher.__new__(Hatcher)
        hatcher._tracked_writes = [stable]

        hatcher._rollback()

        assert not stable.exists() and not stable.is_symlink(), (
            "rollback must remove the stable symlink"
        )
        assert venv_target.exists(), (
            "rollback must NOT remove the symlink target (GC is C2-3's job)"
        )
```

### Exact content: `packages/agent-core-hatchery/tests/test_daemon_probe.py`

```python
"""Unit tests for agent_core_hatchery.daemon_probe (Cβ-3, issue #327)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.daemon_probe import (
    _probe_endpoint,
    _start_daemon,
    _stop_daemon,
    read_daemon_http_config,
    reload_and_probe,
)


def _cfg(tmp_path: Path) -> HatchConfig:
    return HatchConfig(
        being_name="Wren",
        primary_human_name="Jeff",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )


class TestReadDaemonHttpConfig:
    def test_reads_host_and_port_from_yaml(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text(
            "http:\n  bind_host: 0.0.0.0\n  bind_port: 9999\n",
            encoding="utf-8",
        )
        host, port = read_daemon_http_config(cfg_dir)
        assert host == "0.0.0.0"
        assert port == 9999

    def test_defaults_when_file_missing(self, tmp_path: Path) -> None:
        host, port = read_daemon_http_config(tmp_path / ".agent-core-nonexistent")
        assert host == "127.0.0.1"
        assert port == 8789

    def test_defaults_when_keys_absent(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text("bus:\n  storage_path: :memory:\n")
        host, port = read_daemon_http_config(cfg_dir)
        assert host == "127.0.0.1"
        assert port == 8789

    def test_defaults_on_yaml_error(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text(": bad: yaml [\n")
        host, port = read_daemon_http_config(cfg_dir)
        assert host == "127.0.0.1"
        assert port == 8789


class TestStopStartDaemon:
    def test_stop_calls_agent_core_daemon_stop(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        _stop_daemon(runner=fake_runner)
        assert calls == [["agent-core", "daemon", "stop"]]

    def test_start_calls_agent_core_daemon_start(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        _start_daemon(runner=fake_runner)
        assert calls == [["agent-core", "daemon", "start"]]

    def test_stop_swallows_file_not_found(self) -> None:
        def raising_runner(cmd, **kw):
            raise FileNotFoundError("agent-core not found")

        _stop_daemon(runner=raising_runner)  # must not raise

    def test_start_swallows_timeout(self) -> None:
        import subprocess

        def timeout_runner(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 15)

        _start_daemon(runner=timeout_runner)  # must not raise


class TestProbeEndpoint:
    def _make_http_response(self, status: int):
        class _FakeResp:
            def __init__(self):
                self.status = status
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
        return _FakeResp()

    def test_returns_registered_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen",
            lambda url, timeout: self._make_http_response(200),
        )
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=1.0)
        assert result == "reachable_and_registered"

    def test_returns_missing_on_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_404(url, timeout):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen",
            _raise_404,
        )
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=1.0)
        assert result == "reachable_but_missing"

    def test_returns_registered_on_non_404_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_405(url, timeout):
            raise urllib.error.HTTPError(url, 405, "Method Not Allowed", {}, None)

        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen",
            _raise_405,
        )
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=1.0)
        assert result == "reachable_and_registered"

    def test_returns_unreachable_on_connection_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        call_count = 0

        def _refuse(url, timeout):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen", _refuse
        )
        monkeypatch.setattr("agent_core_hatchery.daemon_probe.time.sleep", lambda s: None)

        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=0.1, poll_interval=0.0)
        assert result == "unreachable"
        assert call_count >= 1


class TestReloadAndProbe:
    def test_stops_then_starts_daemon(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        cfg_dir = cfg.resolved_daemon_config_dir()
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text(
            "http:\n  bind_host: 127.0.0.1\n  bind_port: 8789\n", encoding="utf-8"
        )

        call_log: list[str] = []

        def fake_runner(cmd, **kw):
            call_log.append(" ".join(cmd))
            class _R:
                returncode = 0
            return _R()

        # Stub the probe to return immediately
        with patch(
            "agent_core_hatchery.daemon_probe._probe_endpoint",
            return_value="reachable_and_registered",
        ):
            reload_and_probe(cfg, runner=fake_runner)

        assert any("stop" in c for c in call_log), f"stop not called; log={call_log}"
        assert any("start" in c for c in call_log), f"start not called; log={call_log}"
        stop_idx = next(i for i, c in enumerate(call_log) if "stop" in c)
        start_idx = next(i for i, c in enumerate(call_log) if "start" in c)
        assert stop_idx < start_idx, "stop must precede start"

    def test_returns_unreachable_when_no_config_file(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        # daemon_config_dir exists but has no agent_core.yaml
        cfg.resolved_daemon_config_dir().mkdir(parents=True)

        result = reload_and_probe(cfg)
        assert result == "unreachable"
```

### Modifications to `packages/agent-core-hatchery/tests/test_hatcher_config.py`

**Update the `hatched` fixture** to inject no-op stubs so `hatch()` does not attempt real subprocess calls:

```python
import json as _json

def _noop_venv_builder(target: str) -> Path:
    return Path.home() / f".{target}" / ".venv"


def _stub_mcp_gen_for_fixture(vault_root: Path):
    """Returns a generator that writes a minimal valid .mcp.json."""
    def _gen(**kwargs) -> Path:
        p = vault_root / ".mcp.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            _json.dumps({
                "mcpServers": {
                    "agent-core-busproxy": {
                        "command": "/fake/.venv/bin/python",
                        "args": ["-m", "agent_core_busproxy", "--agent", "testbeing"],
                    },
                    "agent-core-channel": {
                        "command": "/fake/.venv/bin/python",
                        "args": ["-m", "agent_core_channel", "--agent", "testbeing"],
                    },
                    "agent-core-notify": {
                        "command": "/fake/.venv/bin/python",
                        "args": ["-m", "agent_core_notify", "--agent", "testbeing"],
                    },
                }
            }),
            encoding="utf-8",
        )
        return p
    return _gen


@pytest.fixture
def hatched(tmp_path: Path) -> tuple[HatchConfig, Path]:
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    vault = cfg.resolved_vault_root()
    Hatcher(
        cfg,
        _venv_builder=_noop_venv_builder,
        _mcp_json_gen=_stub_mcp_gen_for_fixture(vault),
    ).hatch()
    return cfg, vault
```

**Update `test_mcp_json_rendered_at_vault_root`** — remove `command == "uvx"` assertion; confirm stub output:

```python
def test_mcp_json_rendered_at_vault_root(hatched):
    cfg, vault = hatched
    path = vault / ".mcp.json"
    assert path.is_file(), ".mcp.json was not written into the vault root"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    servers = data["mcpServers"]
    # C2-2 generator writes 3 sidecars (stub writes them for the fixture)
    assert "agent-core-busproxy" in servers
    assert "agent-core-channel" in servers
    assert "agent-core-notify" in servers
    # Stable venv interpreter — not uvx
    assert servers["agent-core-busproxy"]["command"] != "uvx"
```

## Alternatives considered

1. **Keep `.mcp.json.j2` and update it to reference a new Jinja variable `{{ stable_venv_python }}`**: Avoids deleting the template file. Ruled out: the stable interpreter path is OS-specific (`.venv/bin/python` vs `.venv/Scripts/python.exe`) and depends on C2-1's `python_in_venv()` function, not a string substitution. A Python function handles this cleanly; a Jinja variable would require the Renderer to know the venv layout. C2-2's generator is purpose-built for exactly this; using it is what the design doc prescribes.

2. **Import daemon.supervisor functions directly instead of subprocess calls for reload**: Hatchery already depends on agent-core, so importing `kill_tree`, `read_pid`, `write_pid` from `agent_core.daemon.supervisor` is technically valid. Ruled out: subprocess calls via the `agent-core daemon stop/start` CLI are a more robust decoupling — if the daemon's stop/start logic evolves (signals, Windows service, etc.), the CLI absorbs the change. Direct supervisor imports would couple Cβ-3 to daemon internals.

3. **Use `httpx` for the HTTP probe instead of `urllib.request`**: httpx is not a current dependency of agent-core-hatchery. Ruled out: stdlib `urllib.request` is sufficient for a simple GET probe with timeout. Adding a dep for a feature that amounts to three urllib calls is YAGNI.

4. **Exit 1 for `unreachable` as well as `reachable_but_missing`**: The design says "fails loudly if the being is not live." Technically `unreachable` means the being is not live. Ruled out: if the operator has the daemon intentionally stopped, a hatch that exits 1 is surprising and blocks scripted use. `unreachable` already produces a prominent `⚠` in the hatching report and prompts the operator to start the daemon. `reachable_but_missing` is the unambiguous defect case (daemon up, endpoint invisible after reload) that warrants exit 1.

## Open questions

1. **C2-2 generator module path and signature**: This spec cannot know the exact module path and function signature until #316 is merged. The Worker must locate C2-2's implementation (look for a `generate_mcp_json` function or similar in the merged #316 branch) and adapt the lazy-import in `Hatcher._generate_mcp_json()` accordingly. The expected contract: `generate_mcp_json(target: str, *, vault_root: Path, ...) -> Path` — takes the being's lowercase name and vault root, writes `<vault_root>/.mcp.json` with stable venv path, returns the path. If the actual signature differs, adjust both the call site and the stub in the test fixtures.

## Out of scope

- Secrets / `set_owner_only` hardening (Cβ-1) — separate ticket, no dependency.
- Schema validation of daemon fragments against Cα-1's Pydantic schema (Cβ-2) — blocked by #319.
- Venv GC / `daemon doctor` extension for versioned venv pruning (C2-3, #317) — separate ticket.
- Auto-spawning a Claude Code session — explicitly deferred per D4; the being is woken via the wake channel.
- Wren/Pepper `.mcp.json` migration (regenerating existing beings' hand-crafted files) — C2-2 owns the migration; Cβ-3 only calls the generator for newly hatched beings.
- Adding `agent-core-notify` to the existing `uvx`-based vault template (the template is being retired, so no point patching it).
