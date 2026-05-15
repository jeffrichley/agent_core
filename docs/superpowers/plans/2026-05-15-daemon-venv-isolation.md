# Daemon ↔ Workspace Venv Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate the bus daemon's Python runtime from the workspace's `.venv/` so `uv sync` in the workspace cannot disrupt a running Pepper or testbot.

**Architecture:** Daemon runs from `~/.agent-core/.venv/`, a non-editable, lockfile-frozen venv populated by `agent-core daemon install`. The supervisor prefers that interpreter and falls back to `sys.executable` when it's absent (today's behavior). A new `daemon refresh` bundles stop/install/start for daily code-pickup.

**Tech Stack:** Python 3.12, Typer (CLI), `uv` (venv + dependency manager), `subprocess` (uv invocation), `psutil` (existing PID-liveness via `daemon.supervisor`), pytest + `typer.testing.CliRunner`.

**Spec:** `docs/superpowers/specs/2026-05-15-daemon-venv-isolation-design.md`
**Issue:** [#79](https://github.com/jeffrichley/agent_core/issues/79)
**Branch:** `feat/issue-79-daemon-venv-spec` (spec already committed; implementation lands on this same branch).

---

## Task 1: Add `_daemon_python()` helper, thread through `daemon start`

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py`
- Test:   `packages/core/tests/test_daemon_cli.py`

This task introduces the interpreter-resolution helper and uses it in `start()`. Without a daemon venv on disk, behavior is identical to today (fallback path). All later tasks depend on this helper existing.

- [ ] **Step 1: Write the failing tests**

Add to the end of `packages/core/tests/test_daemon_cli.py`:

```python
from agent_core.daemon.cli import _daemon_python


def test_daemon_python_returns_sys_executable_when_venv_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # No ~/.agent-core/.venv/ exists in tmp_path.
    import sys

    assert _daemon_python() == sys.executable


def test_daemon_python_returns_daemon_venv_python_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    import sys

    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    assert _daemon_python() == str(venv_python)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_cli.py::test_daemon_python_returns_sys_executable_when_venv_missing packages/core/tests/test_daemon_cli.py::test_daemon_python_returns_daemon_venv_python_when_present -v
```

Expected: FAIL with `ImportError` (cannot import name `_daemon_python` from `agent_core.daemon.cli`).

- [ ] **Step 3: Add the helper to `cli.py`**

In `packages/core/src/agent_core/daemon/cli.py`, after the existing `_log_path()` function, add:

```python
def _daemon_python() -> str:
    """Return the daemon's preferred interpreter, with fallback.

    Prefers `~/.agent-core/.venv/Scripts/python.exe` (Windows) or
    `~/.agent-core/.venv/bin/python` (POSIX) when present; falls back
    to `sys.executable` (today's behavior) when the daemon venv is
    missing. This keeps the supervisor working unchanged on machines
    that haven't run `agent-core daemon install` yet.
    """
    if sys.platform == "win32":
        candidate = _home() / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = _home() / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable
```

Then in `start()` (around line 73), change:

```python
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_core.cli", "bus", "run", "--config", str(cfg)],
```

to:

```python
    proc = subprocess.Popen(
        [_daemon_python(), "-m", "agent_core.cli", "bus", "run", "--config", str(cfg)],
```

- [ ] **Step 4: Run the two new tests + existing daemon CLI tests**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: PASS for the two new tests AND all 5 existing tests (including the e2e `test_start_writes_pid_file_and_stop_kills`, which still works because `_daemon_python()` falls back to `sys.executable` in the tmp_path that has no daemon venv).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): _daemon_python() helper with sys.executable fallback (#79)"
```

---

## Task 2: Workspace root discovery in `install.py`

**Files:**
- Create: `packages/core/src/agent_core/daemon/install.py`
- Create: `packages/core/tests/test_daemon_install.py`

Pure function that locates the workspace root by ascending from a starting path until it finds a `pyproject.toml` containing `[tool.uv.workspace]`.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_daemon_install.py`:

```python
"""Unit tests for `agent_core.daemon.install`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.daemon.install import WorkspaceNotFoundError, find_workspace_root


def test_find_workspace_root_walks_up_to_pyproject(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"packages/*\"]\n",
        encoding="utf-8",
    )
    deep = workspace / "packages" / "core" / "src" / "agent_core"
    deep.mkdir(parents=True)

    assert find_workspace_root(deep) == workspace


def test_find_workspace_root_ignores_non_workspace_pyproject(tmp_path: Path) -> None:
    """A pyproject.toml without [tool.uv.workspace] is not a workspace root."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("[project]\nname = \"x\"\n", encoding="utf-8")

    outer = tmp_path
    (outer / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"inner\"]\n",
        encoding="utf-8",
    )

    assert find_workspace_root(inner) == outer


def test_find_workspace_root_raises_when_absent(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError, match="workspace"):
        find_workspace_root(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_core.daemon.install'`.

- [ ] **Step 3: Create `install.py` with the helper**

Create `packages/core/src/agent_core/daemon/install.py`:

```python
"""Daemon venv install logic — pure functions, no I/O beyond filesystem.

`cli.py` wires these into the `agent-core daemon install` and
`agent-core daemon refresh` typer commands. Keeping the install logic
out of the CLI module makes it directly unit-testable.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


class WorkspaceNotFoundError(RuntimeError):
    """Raised when find_workspace_root cannot locate a workspace pyproject.toml."""


def find_workspace_root(start: Path) -> Path:
    """Ascend from `start` until a `pyproject.toml` with `[tool.uv.workspace]` is found.

    The agent-core repo's root `pyproject.toml` declares the workspace; we
    use that as the install target.
    """
    candidate = start.resolve()
    while True:
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                data = {}
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return candidate
        parent = candidate.parent
        if parent == candidate:
            raise WorkspaceNotFoundError(
                f"couldn't find workspace root above {start} "
                "(no pyproject.toml with [tool.uv.workspace] found). "
                "Run `agent-core daemon install` from within the agent-core repo."
            )
        candidate = parent
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: PASS (all 3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/install.py packages/core/tests/test_daemon_install.py
git commit -m "feat(daemon): workspace root discovery for install command (#79)"
```

---

## Task 3: Install stamp file I/O

**Files:**
- Modify: `packages/core/src/agent_core/daemon/install.py`
- Modify: `packages/core/tests/test_daemon_install.py`

The stamp file at `~/.agent-core/.daemon-install-stamp.json` records install metadata: timestamp, git sha, python version, extra, lockfile hash.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_daemon_install.py`:

```python
from agent_core.daemon.install import (
    STAMP_FILENAME,
    InstallStamp,
    read_stamp,
    write_stamp,
)


def test_write_then_read_stamp_round_trips(tmp_path: Path) -> None:
    stamp = InstallStamp(
        installed_at="2026-05-15T19:31:04Z",
        installed_sha="42713d7",
        python_version="3.12.5",
        extra="cu130",
        uv_lock_hash="sha256:abc",
    )
    write_stamp(tmp_path, stamp)

    assert (tmp_path / STAMP_FILENAME).exists()
    assert read_stamp(tmp_path) == stamp


def test_read_stamp_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_stamp(tmp_path) is None


def test_read_stamp_returns_none_when_corrupt(tmp_path: Path) -> None:
    (tmp_path / STAMP_FILENAME).write_text("not json", encoding="utf-8")
    assert read_stamp(tmp_path) is None


def test_read_stamp_returns_none_when_missing_fields(tmp_path: Path) -> None:
    (tmp_path / STAMP_FILENAME).write_text(
        '{"installed_at": "2026-05-15T19:31:04Z"}', encoding="utf-8"
    )
    assert read_stamp(tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: FAIL with `ImportError` on `STAMP_FILENAME`/`InstallStamp`/`read_stamp`/`write_stamp`.

- [ ] **Step 3: Add stamp logic to `install.py`**

Append to `packages/core/src/agent_core/daemon/install.py`:

```python
import json
from dataclasses import asdict, dataclass

STAMP_FILENAME = ".daemon-install-stamp.json"


@dataclass(frozen=True)
class InstallStamp:
    """Captures what was installed into the daemon venv, when, and from where."""

    installed_at: str  # ISO 8601 UTC
    installed_sha: str  # git rev-parse HEAD at install time
    python_version: str  # e.g., "3.12.5"
    extra: str | None  # uv extra name, or None
    uv_lock_hash: str  # sha256 of uv.lock at install time


def write_stamp(home: Path, stamp: InstallStamp) -> None:
    """Write the install stamp to <home>/.daemon-install-stamp.json."""
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP_FILENAME).write_text(
        json.dumps(asdict(stamp), indent=2) + "\n",
        encoding="utf-8",
    )


def read_stamp(home: Path) -> InstallStamp | None:
    """Read the install stamp. None on missing, corrupt, or schema-incomplete."""
    path = home / STAMP_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return InstallStamp(
            installed_at=data["installed_at"],
            installed_sha=data["installed_sha"],
            python_version=data["python_version"],
            extra=data.get("extra"),
            uv_lock_hash=data["uv_lock_hash"],
        )
    except (KeyError, TypeError):
        return None
```

Move the `import json` to the top of the file with the other imports.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: PASS (7 tests total).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/install.py packages/core/tests/test_daemon_install.py
git commit -m "feat(daemon): install stamp file read/write (#79)"
```

---

## Task 4: uv-command builder

**Files:**
- Modify: `packages/core/src/agent_core/daemon/install.py`
- Modify: `packages/core/tests/test_daemon_install.py`

Pure function that returns the `uv sync` command and environment dict. Testable without running uv.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_daemon_install.py`:

```python
from agent_core.daemon.install import build_uv_sync_command


def test_build_uv_sync_command_basic(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    cmd, env_overrides = build_uv_sync_command(venv=venv, extra=None)
    assert cmd == ["uv", "sync", "--frozen", "--no-editable", "--no-dev"]
    assert env_overrides == {"UV_PROJECT_ENVIRONMENT": str(venv)}


def test_build_uv_sync_command_with_extra(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    cmd, env_overrides = build_uv_sync_command(venv=venv, extra="cu130")
    assert cmd == [
        "uv",
        "sync",
        "--frozen",
        "--no-editable",
        "--no-dev",
        "--extra",
        "cu130",
    ]
    assert env_overrides == {"UV_PROJECT_ENVIRONMENT": str(venv)}
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_install.py::test_build_uv_sync_command_basic packages/core/tests/test_daemon_install.py::test_build_uv_sync_command_with_extra -v
```

Expected: FAIL with `ImportError: cannot import name 'build_uv_sync_command'`.

- [ ] **Step 3: Add the builder to `install.py`**

Append to `packages/core/src/agent_core/daemon/install.py`:

```python
def build_uv_sync_command(
    *, venv: Path, extra: str | None
) -> tuple[list[str], dict[str, str]]:
    """Build the `uv sync` command list and env overrides for daemon install.

    --frozen        → install against the workspace's uv.lock verbatim.
    --no-editable   → workspace members go in as wheels; no .pth shims.
                      This is what makes the daemon venv immune to
                      `uv sync` in the workspace tree.
    --no-dev        → skip the workspace's dev dependency group.
    --extra <x>     → optional uv extra (e.g., cu130, cpu).

    UV_PROJECT_ENVIRONMENT redirects uv's install target to the daemon venv
    instead of the workspace's `.venv/`.
    """
    cmd: list[str] = ["uv", "sync", "--frozen", "--no-editable", "--no-dev"]
    if extra is not None:
        cmd += ["--extra", extra]
    env_overrides = {"UV_PROJECT_ENVIRONMENT": str(venv)}
    return cmd, env_overrides
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/install.py packages/core/tests/test_daemon_install.py
git commit -m "feat(daemon): build_uv_sync_command pure builder (#79)"
```

---

## Task 5: `run_install()` orchestrator

**Files:**
- Modify: `packages/core/src/agent_core/daemon/install.py`
- Modify: `packages/core/tests/test_daemon_install.py`

Composes Tasks 2–4 into the actual install: runs `uv venv`, runs `uv sync`, writes the stamp file. Subprocess mocked in tests.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_daemon_install.py`:

```python
import hashlib
import subprocess
import sys
from unittest.mock import MagicMock

from agent_core.daemon.install import UvNotFoundError, run_install


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"packages/*\"]\n", encoding="utf-8"
    )
    (workspace / "uv.lock").write_text("# fake lock\n", encoding="utf-8")
    return workspace


def test_run_install_invokes_uv_venv_then_uv_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_core.daemon.install._git_head_sha", lambda _ws: "abc1234"
    )

    run_install(home=home, workspace=workspace, extra="cu130", python_version="3.12")

    # First call: uv venv ... --python 3.12
    assert calls[0][:2] == ["uv", "venv"]
    assert "--python" in calls[0]
    assert "3.12" in calls[0]
    # Second call: uv sync --frozen --no-editable --no-dev --extra cu130
    assert calls[1] == [
        "uv",
        "sync",
        "--frozen",
        "--no-editable",
        "--no-dev",
        "--extra",
        "cu130",
    ]


def test_run_install_writes_stamp_with_lock_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(
        "agent_core.daemon.install.subprocess.run",
        lambda cmd, **kwargs: MagicMock(returncode=0),
    )
    monkeypatch.setattr(
        "agent_core.daemon.install._git_head_sha", lambda _ws: "abc1234"
    )

    run_install(home=home, workspace=workspace, extra=None, python_version="3.12")

    stamp = read_stamp(home)
    assert stamp is not None
    assert stamp.installed_sha == "abc1234"
    assert stamp.extra is None
    # uv_lock_hash matches sha256 of the lock file content
    expected_hash = (
        "sha256:"
        + hashlib.sha256((workspace / "uv.lock").read_bytes()).hexdigest()
    )
    assert stamp.uv_lock_hash == expected_hash


def test_run_install_raises_uv_not_found_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "uv")

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)

    with pytest.raises(UvNotFoundError, match="uv not found on PATH"):
        run_install(home=home, workspace=workspace, extra=None, python_version="3.12")


def test_run_install_raises_on_uv_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    def fake_run(cmd, **kwargs):
        # `uv venv` succeeds, `uv sync` fails.
        if cmd[:2] == ["uv", "sync"]:
            return MagicMock(returncode=1, stderr="resolution error")
        return MagicMock(returncode=0)

    monkeypatch.setattr("agent_core.daemon.install.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_core.daemon.install._git_head_sha", lambda _ws: "abc1234"
    )

    with pytest.raises(subprocess.CalledProcessError):
        run_install(home=home, workspace=workspace, extra=None, python_version="3.12")
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: FAIL with `ImportError: cannot import name 'run_install'` / `UvNotFoundError`.

- [ ] **Step 3: Add `run_install()` and helpers**

Append to `packages/core/src/agent_core/daemon/install.py`:

```python
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
import os


class UvNotFoundError(RuntimeError):
    """Raised when `uv` is not on PATH."""


def _git_head_sha(workspace: Path) -> str:
    """Return the short HEAD sha of the workspace repo, or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def compute_lock_hash(workspace: Path) -> str:
    """SHA-256 of the workspace's uv.lock, prefixed with `sha256:`. Public:
    `cli.py`'s `daemon status` reuses this for the lock-drift check.
    """
    lock = workspace / "uv.lock"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def run_install(
    *,
    home: Path,
    workspace: Path,
    extra: str | None,
    python_version: str,
) -> InstallStamp:
    """Populate `<home>/.venv/` and write the install stamp. Idempotent."""
    venv = home / ".venv"

    # Step 1: create / refresh the venv with the pinned Python.
    venv_cmd = ["uv", "venv", str(venv), "--python", python_version]
    try:
        result = subprocess.run(venv_cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise UvNotFoundError(
            "uv not found on PATH — install uv first: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        ) from exc
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, venv_cmd, output=result.stdout, stderr=result.stderr
        )

    # Step 2: uv sync into that venv.
    sync_cmd, env_overrides = build_uv_sync_command(venv=venv, extra=extra)
    env = {**os.environ, **env_overrides}
    sync_result = subprocess.run(
        sync_cmd, cwd=workspace, env=env, capture_output=True, text=True, check=False
    )
    if sync_result.returncode != 0:
        raise subprocess.CalledProcessError(
            sync_result.returncode,
            sync_cmd,
            output=sync_result.stdout,
            stderr=sync_result.stderr,
        )

    # Step 3: stamp it.
    stamp = InstallStamp(
        installed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        installed_sha=_git_head_sha(workspace),
        python_version=python_version,
        extra=extra,
        uv_lock_hash=compute_lock_hash(workspace),
    )
    write_stamp(home, stamp)
    return stamp
```

Make sure the file's top-level imports block contains everything used so far. The final import block in `install.py` after this task should be:

```python
import hashlib
import json
import os
import subprocess
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_install.py -v
```

Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/install.py packages/core/tests/test_daemon_install.py
git commit -m "feat(daemon): run_install orchestrator (uv venv + uv sync + stamp) (#79)"
```

---

## Task 6: `daemon install` CLI command

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py`
- Modify: `packages/core/tests/test_daemon_cli.py`

Typer command that parses args, refuses while the daemon is running, discovers the workspace, and calls `run_install()`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_daemon_cli.py`:

```python
from unittest.mock import MagicMock


def test_install_refuses_when_daemon_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))  # current process is alive
    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "currently running" in result.stdout.lower()
    assert "refresh" in result.stdout.lower()


def test_install_invokes_run_install_with_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    captured: dict = {}

    fake_stamp = MagicMock(
        installed_sha="abc1234",
        extra="cu130",
        python_version="3.12",
        installed_at="2026-05-15T19:31:04Z",
    )

    def fake_run_install(*, home, workspace, extra, python_version):
        captured["home"] = home
        captured["workspace"] = workspace
        captured["extra"] = extra
        captured["python_version"] = python_version
        return fake_stamp

    monkeypatch.setattr("agent_core.daemon.cli.run_install", fake_run_install)
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root",
        lambda _start: tmp_path / "fake-workspace",
    )

    result = runner.invoke(daemon_app, ["install", "--extra", "cu130"])
    assert result.exit_code == 0, result.stdout
    assert captured["home"] == tmp_path
    assert captured["workspace"] == tmp_path / "fake-workspace"
    assert captured["extra"] == "cu130"
    assert captured["python_version"] == "3.12"
    assert "abc1234" in result.stdout  # stamp sha echoed


def test_install_reports_workspace_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    def raise_not_found(_start):
        from agent_core.daemon.install import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("can't find workspace")

    monkeypatch.setattr("agent_core.daemon.cli.find_workspace_root", raise_not_found)

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "workspace" in result.stdout.lower()


def test_install_reports_uv_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    def fake_run_install(*, home, workspace, extra, python_version):
        from agent_core.daemon.install import UvNotFoundError

        raise UvNotFoundError("uv not found on PATH")

    monkeypatch.setattr("agent_core.daemon.cli.run_install", fake_run_install)
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root",
        lambda _start: tmp_path / "fake-workspace",
    )

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "uv not found" in result.stdout.lower()
```

Add `import os` at the top of the test file if not already imported.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: FAIL — `install` subcommand doesn't exist yet.

- [ ] **Step 3: Add the `install` command to `cli.py`**

In `packages/core/src/agent_core/daemon/cli.py`, add at the top of the file (with existing imports):

```python
from agent_core.daemon.install import (
    UvNotFoundError,
    WorkspaceNotFoundError,
    find_workspace_root,
    run_install,
)
```

Then add this command after the existing `status()` command:

```python
@app.command()
def install(
    extra: str | None = typer.Option(
        None, "--extra", help="uv extra to install (e.g., cu130, cpu)."
    ),
    python_version: str = typer.Option(
        "3.12", "--python", help="Python version to pin the daemon venv to."
    ),
) -> None:
    """Populate ~/.agent-core/.venv/ from the workspace (non-editable, frozen)."""
    pid_file = _pid_path()
    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(
            f"[red]daemon is currently running (PID {existing}).[/red]\n"
            "   • Run [bold]agent-core daemon stop[/bold] and re-run install, or\n"
            "   • Run [bold]agent-core daemon refresh[/bold] to stop/install/start in one step."
        )
        raise typer.Exit(code=1)

    try:
        workspace = find_workspace_root(Path(__file__).parent)
    except WorkspaceNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    home = _home()
    home.mkdir(parents=True, exist_ok=True)

    try:
        stamp = run_install(
            home=home,
            workspace=workspace,
            extra=extra,
            python_version=python_version,
        )
    except UvNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]daemon venv installed[/green] "
        f"(sha {stamp.installed_sha}, python {stamp.python_version}"
        + (f", extra {stamp.extra}" if stamp.extra else "")
        + f", at {stamp.installed_at})"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: PASS (4 new tests + 7 existing tests = 11 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): daemon install CLI command (#79)"
```

---

## Task 7: `daemon refresh` CLI command

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py`
- Modify: `packages/core/tests/test_daemon_cli.py`

Bundles stop → install → start. If install fails, start is not called.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_daemon_cli.py`:

```python
def test_refresh_calls_stop_install_start_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop() -> None:
        order.append("stop")

    def fake_install(extra: str | None = None, python_version: str = "3.12") -> None:
        order.append(f"install:extra={extra}")

    def fake_start() -> None:
        order.append("start")

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh", "--extra", "cu130"])
    assert result.exit_code == 0, result.stdout
    assert order == ["stop", "install:extra=cu130", "start"]


def test_refresh_aborts_start_when_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop() -> None:
        order.append("stop")

    def fake_install(extra: str | None = None, python_version: str = "3.12") -> None:
        order.append("install")
        raise typer.Exit(code=1)

    def fake_start() -> None:
        order.append("start")  # must not be called

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh"])
    assert result.exit_code != 0
    assert "start" not in order
    assert order == ["stop", "install"]


def test_refresh_reuses_stamped_extra_when_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --extra is omitted, refresh passes the stamped extra to install."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Write a stamp with extra='cu130'
    from agent_core.daemon.install import InstallStamp, write_stamp

    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            python_version="3.12",
            extra="cu130",
            uv_lock_hash="sha256:abc",
        ),
    )

    captured: dict = {}

    def fake_install(extra: str | None = None, python_version: str = "3.12") -> None:
        captured["extra"] = extra

    monkeypatch.setattr("agent_core.daemon.cli.stop", lambda: None)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", lambda: None)

    result = runner.invoke(daemon_app, ["refresh"])
    assert result.exit_code == 0
    assert captured["extra"] == "cu130"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: FAIL — `refresh` subcommand doesn't exist yet.

- [ ] **Step 3: Add `refresh` to `cli.py`**

Add the import near the top with the other install imports:

```python
from agent_core.daemon.install import (
    UvNotFoundError,
    WorkspaceNotFoundError,
    find_workspace_root,
    read_stamp,         # <-- new
    run_install,
)
```

Add this command at the end of `cli.py`:

```python
@app.command()
def refresh(
    extra: str | None = typer.Option(
        None,
        "--extra",
        help="uv extra to install. Defaults to the stamped extra from the last install.",
    ),
    python_version: str = typer.Option(
        "3.12", "--python", help="Python version to pin the daemon venv to."
    ),
) -> None:
    """Stop daemon → reinstall daemon venv → start daemon. Bundled lifecycle."""
    if extra is None:
        stamp = read_stamp(_home())
        if stamp is not None:
            extra = stamp.extra

    stop()
    install(extra=extra, python_version=python_version)
    start()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: PASS (3 new + 11 existing = 14 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): daemon refresh CLI command (#79)"
```

---

## Task 8: `daemon status` diagnostics

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py`
- Modify: `packages/core/tests/test_daemon_cli.py`

Adds three lines to `daemon status`: running-from interpreter, stamp metadata, lock-drift warning.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_daemon_cli.py`:

```python
def test_status_shows_fallback_warning_when_no_daemon_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _daemon_python falls back to sys.executable, status warns."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Daemon is "running" — write a PID for the current process so is_alive=True
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))

    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "fallback" in result.stdout.lower()
    assert "vulnerable to uv sync" in result.stdout.lower()


def test_status_no_warning_when_daemon_venv_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))

    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "fallback" not in result.stdout.lower()


def test_status_shows_stamp_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_core.daemon.install import InstallStamp, write_stamp

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            python_version="3.12",
            extra="cu130",
            uv_lock_hash="sha256:deadbeef",
        ),
    )

    result = runner.invoke(daemon_app, ["status"])
    assert "abc1234" in result.stdout
    assert "2026-05-15" in result.stdout


def test_status_flags_lock_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_core.daemon.install import InstallStamp, write_stamp

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    # Stamp says the lock was hash "stale-hash"; workspace lock has different content.
    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            python_version="3.12",
            extra=None,
            uv_lock_hash="sha256:STALE",
        ),
    )

    # Fake workspace with a uv.lock whose hash is different.
    fake_ws = tmp_path / "fake-workspace"
    fake_ws.mkdir()
    (fake_ws / "uv.lock").write_text("# new content\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_core.daemon.cli.find_workspace_root", lambda _start: fake_ws
    )

    result = runner.invoke(daemon_app, ["status"])
    assert "stale" in result.stdout.lower() or "refresh" in result.stdout.lower()


def test_status_handles_missing_workspace_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status must not crash if workspace discovery fails — drift check is optional."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))

    def raise_not_found(_start):
        from agent_core.daemon.install import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("no workspace")

    monkeypatch.setattr("agent_core.daemon.cli.find_workspace_root", raise_not_found)

    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0  # still succeeds
```

Make sure `import sys` and `import os` are at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: FAIL — status doesn't have the new diagnostic lines yet.

- [ ] **Step 3: Extend `status()` in `cli.py`**

Replace the existing `status()` command with this version:

```python
@app.command()
def status() -> None:
    """Report daemon liveness and tail the log."""
    pid_file = _pid_path()
    log_file = _log_path()
    pid = read_pid(pid_file)

    if pid is None:
        console.print("[yellow]daemon is not running[/yellow]")
        return
    if not is_alive(pid):
        console.print("[yellow]daemon is not running (stale PID file removed)[/yellow]")
        remove_pid(pid_file)
        return

    console.print(f"[green]daemon is running (PID: {pid})[/green]")

    # Diagnostic: which interpreter the supervisor would use today.
    daemon_py = _daemon_python()
    suffix = ""
    if daemon_py == sys.executable:
        suffix = (
            " [dim red](fallback — vulnerable to uv sync; "
            "run `agent-core daemon install`)[/dim red]"
        )
    console.print(f"running from: {daemon_py}{suffix}")

    # Diagnostic: stamp metadata, if present.
    stamp = read_stamp(_home())
    if stamp is not None:
        console.print(f"installed at: {stamp.installed_at}")
        console.print(f"installed sha: {stamp.installed_sha}")

        # Lock-drift check (best-effort; skipped silently if workspace not findable).
        try:
            workspace = find_workspace_root(Path(__file__).parent)
            current_hash = compute_lock_hash(workspace)
            if current_hash != stamp.uv_lock_hash:
                console.print(
                    "[yellow]daemon venv may be stale — "
                    "run `agent-core daemon refresh`[/yellow]"
                )
        except (WorkspaceNotFoundError, FileNotFoundError):
            pass  # Lock-drift is a nice-to-have; don't fail status on workspace issues.

    if log_file.exists():
        console.print("\n[dim]--- last 20 lines of daemon.log ---[/dim]")
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-20:]:
            console.print(line)
```

Add `compute_lock_hash` to the install import at the top:

```python
from agent_core.daemon.install import (
    UvNotFoundError,
    WorkspaceNotFoundError,
    compute_lock_hash,
    find_workspace_root,
    read_stamp,
    run_install,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: PASS (5 new + 14 existing = 19 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): status diagnostics — fallback warning, stamp display, lock drift (#79)"
```

---

## Task 9: Integration test against a minimal tmp workspace

**Files:**
- Create: `packages/core/tests/test_daemon_install_integration.py`
- Modify: `pyproject.toml` (register `slow` marker)

End-to-end install against a scaled-down tmp workspace. Validates orchestration with a real `uv` invocation but without pulling torch.

- [ ] **Step 1: Register the `slow` pytest marker**

In `pyproject.toml`, find the existing markers list:

```toml
markers = [
    "looptime: virtual asyncio clock compaction (looptime plugin; see test docs)",
]
```

Change to:

```toml
markers = [
    "looptime: virtual asyncio clock compaction (looptime plugin; see test docs)",
    "slow: tests that hit network or take >5s; skipped by default in CI",
]
```

- [ ] **Step 2: Write the integration test**

Create `packages/core/tests/test_daemon_install_integration.py`:

```python
"""End-to-end install against a minimal tmp workspace. Real uv subprocess.

Skipped by default (slow); run locally with:
    pytest packages/core/tests/test_daemon_install_integration.py -v -m slow
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_core.daemon.install import read_stamp, run_install


pytestmark = pytest.mark.slow


def _uv_available() -> bool:
    return shutil.which("uv") is not None


@pytest.mark.skipif(not _uv_available(), reason="uv not on PATH")
def test_run_install_creates_working_venv_against_minimal_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Minimal workspace pyproject — no torch, no agent-core. Just `iniconfig`
    # so uv has something real to install.
    (workspace / "pyproject.toml").write_text(
        """
[project]
name = "fake-ws-root"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["iniconfig"]

[tool.uv.workspace]
members = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""",
        encoding="utf-8",
    )
    # Generate a lockfile so --frozen has something to install against.
    lock_result = subprocess.run(
        ["uv", "lock"], cwd=workspace, capture_output=True, text=True, check=False
    )
    assert lock_result.returncode == 0, lock_result.stderr

    home = tmp_path / "agent-core-home"
    home.mkdir()

    stamp = run_install(
        home=home,
        workspace=workspace,
        extra=None,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )

    # Venv exists and has a Python interpreter.
    if sys.platform == "win32":
        py = home / ".venv" / "Scripts" / "python.exe"
    else:
        py = home / ".venv" / "bin" / "python"
    assert py.exists(), f"expected interpreter at {py}"

    # iniconfig was installed.
    show = subprocess.run(
        [str(py), "-c", "import iniconfig; print(iniconfig.__name__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0, show.stderr
    assert show.stdout.strip() == "iniconfig"

    # Stamp file exists and matches what run_install returned.
    on_disk = read_stamp(home)
    assert on_disk == stamp

    # Re-running run_install is idempotent (uv sync no-op).
    stamp2 = run_install(
        home=home,
        workspace=workspace,
        extra=None,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    # Stamp's installed_at will differ (re-stamped); installed_sha/lock_hash
    # should match the first call.
    assert stamp2.uv_lock_hash == stamp.uv_lock_hash
```

- [ ] **Step 3: Run the test locally**

```
pytest packages/core/tests/test_daemon_install_integration.py -v -m slow
```

Expected: PASS in roughly 10–30 seconds (uv installs `iniconfig` into the tmp venv).

- [ ] **Step 4: Verify the test is skipped by default**

```
pytest packages/core/tests/test_daemon_install_integration.py -v
```

Expected: 1 test skipped (slow marker, no `-m slow` requested).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml packages/core/tests/test_daemon_install_integration.py
git commit -m "test(daemon): integration test against minimal tmp workspace (#79)"
```

---

## Task 10: Operator docs

**Files:**
- Create: `docs/setup/daemon.md`
- Modify: `README.md` (one-line pointer)

- [ ] **Step 1: Write `docs/setup/daemon.md`**

Create `docs/setup/daemon.md`:

```markdown
# Daemon setup and refresh workflow

The agent-core bus daemon supervises every endpoint (bus, Discord adapters,
scheduler, briefs, webcam, voice). To keep it stable while you iterate on
workspace code, the daemon runs from its **own** venv at
`~/.agent-core/.venv/` — not from the workspace's `.venv/`. This isolates
the daemon process from `uv sync` activity in the workspace.

## One-time setup

```bash
# Stop any running daemon first.
agent-core daemon stop

# Populate ~/.agent-core/.venv/ from the workspace.
# --extra cu130 picks the CUDA torch wheels for GPU voice synthesis.
# Use --extra cpu on machines without a GPU, or omit on machines that
# don't run the voice endpoint.
agent-core daemon install --extra cu130

# Start the daemon — it now runs from ~/.agent-core/.venv/.
agent-core daemon start

# Verify.
agent-core daemon status
```

`daemon status` should show:

```
daemon is running (PID: NNNNN)
running from: ~/.agent-core/.venv/Scripts/python.exe   (or bin/python on POSIX)
installed at: <timestamp>
installed sha: <git short hash>
```

If you see `(fallback — vulnerable to uv sync; run \`agent-core daemon install\`)`
next to `running from`, the daemon venv is missing and the supervisor fell
back to the workspace venv. Run `daemon install` and `daemon refresh`.

## Daily flow: picking up new code

When you've made changes to agent-core (or pulled new code) and want the
daemon to run them:

```bash
agent-core daemon refresh
```

This does three things in order:
1. `daemon stop` — kills the running daemon.
2. `daemon install` — re-runs `uv sync --frozen --no-editable --no-dev` against
   the workspace. Uses the extra you specified at install time (stamped in
   `~/.agent-core/.daemon-install-stamp.json`).
3. `daemon start` — relaunches the daemon from the refreshed venv.

If `daemon install` fails (uv error, missing workspace, etc.), the daemon
stays stopped and the error surfaces. Fix the underlying issue and re-run
`daemon refresh`.

## Why this exists

Before this change, the daemon ran from `<workspace>/.venv/Scripts/python.exe`
(Windows) or `<workspace>/.venv/bin/python` (POSIX). On Windows, `uv sync`
uses unlink-then-relink semantics that disrupt running processes holding open
files in `.venv/`. Adding a workspace member re-resolved the lockfile and
rewrote every package's editable `.pth` file, silently killing the running
daemon. Pepper went offline mid-session on 2026-05-10 from exactly this.

The fix: install the daemon non-editable, into a venv outside the workspace
tree. `uv sync` cannot reach it because there are no `.pth` files pointing
at workspace source.

## Disk cost

The full workspace install with `--extra cu130` is ~6–7 GB (mostly torch
CUDA wheels). One-time. `daemon refresh` is a delta operation on the lockfile
diff; usually fast.

To reclaim the space, `rm -rf ~/.agent-core/.venv` then `daemon install`
fresh.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `daemon is currently running` on install | Daemon supervises something | `daemon refresh` |
| `couldn't find workspace root` | Running `install` outside the repo | `cd` into the agent-core repo |
| `uv not found on PATH` | uv not installed | https://docs.astral.sh/uv/getting-started/installation/ |
| `daemon venv may be stale` | `uv.lock` moved since last install | `daemon refresh` |
| `fallback — vulnerable to uv sync` | `~/.agent-core/.venv/` not present | `daemon install` |

## Related

- [#79](https://github.com/jeffrichley/agent_core/issues/79) — the issue that motivated this.
- `docs/superpowers/specs/2026-05-15-daemon-venv-isolation-design.md` — the design.
- `agent_core.daemon.cli` — supervisor code.
- `agent_core.daemon.install` — install orchestration.
```

- [ ] **Step 2: Add pointer to `README.md`**

Read the existing `README.md`:

```
cat README.md
```

Find the section that lists docs / setup links (or a "Getting started" / "Development" section). Add this line in the most appropriate place — typically under a "Running the daemon" or "Development" header:

```markdown
- **Running the bus daemon:** see [docs/setup/daemon.md](docs/setup/daemon.md) for the one-time setup and the `daemon refresh` daily flow.
```

If there's no obviously right section, add a "Setup" section near the top of the README:

```markdown
## Setup

- **Bus daemon:** see [docs/setup/daemon.md](docs/setup/daemon.md) for the one-time setup and the `daemon refresh` daily flow.
```

- [ ] **Step 3: Verify the docs render**

```
cat docs/setup/daemon.md | head -20
```

Expected: clean markdown, no rendering artifacts.

- [ ] **Step 4: Commit**

```bash
git add docs/setup/daemon.md README.md
git commit -m "docs(daemon): operator guide for daemon install + refresh workflow (#79)"
```

---

## Task 11: Manual verification on Jeff's box (runbook only — no commit)

This task is a runbook, not a code change. It is the final acceptance step
before opening the PR.

- [ ] **Step 1: Stop the currently-running daemon**

```
agent-core daemon stop
```

Expected: `daemon stopped (PID: NNNN)`.

- [ ] **Step 2: Run the one-time install**

From the agent-core repo root:

```
agent-core daemon install --extra cu130
```

Expected: takes 1–5 minutes, ends with `daemon venv installed (sha ..., python 3.12, extra cu130, at ...)`.

- [ ] **Step 3: Verify the daemon venv contains the right packages**

```
ls ~/.agent-core/.venv/Scripts/      # Windows
ls ~/.agent-core/.venv/Lib/site-packages/ | grep agent_core
```

Expected: `agent_core/`, `agent_core_voice/`, `agent_core_webcam/`, etc. — all workspace members installed as wheels.

- [ ] **Step 4: Start the daemon and verify the new interpreter is in use**

```
agent-core daemon start
agent-core daemon status
```

Expected: `running from: C:\Users\jeffr\.agent-core\.venv\Scripts\python.exe`. NO `fallback — vulnerable to uv sync` suffix.

- [ ] **Step 5: Run a smoke MCP call to confirm everything works**

In a separate terminal, exercise an endpoint — e.g., synth a short utterance via the voice MCP, or send a test message through Discord. Should round-trip cleanly.

- [ ] **Step 6: The actual regression test — `uv sync` while the daemon is up**

In the workspace:

```
uv sync
```

Expected: daemon stays alive (verify with `agent-core daemon status` — same PID, no crash). This is the failure mode #79 was filed to fix.

- [ ] **Step 7: Daily-flow smoke**

```
agent-core daemon refresh
```

Expected: stop → install (fast no-op since nothing changed) → start. Daemon comes back up with the same metadata.

- [ ] **Step 8: Document successful run in the PR description**

Capture the output of `daemon status` after step 7 and paste it into the PR description as evidence of acceptance.

---

## After all tasks: open PR

```bash
git push -u origin feat/issue-79-daemon-venv-spec

gh pr create --title "feat(daemon): venv isolation from workspace uv sync (#79)" --body "$(cat <<'EOF'
## Summary

Closes #79.

The bus daemon now runs from its own venv at `~/.agent-core/.venv/`,
populated by a new `agent-core daemon install` subcommand. The supervisor
prefers that interpreter and falls back to `sys.executable` when the
daemon venv is absent — so merging this PR is a no-op at runtime until
the operator runs `daemon install`.

## What's new

- `agent-core daemon install [--extra <extra>] [--python <version>]` —
  populates `~/.agent-core/.venv/` non-editable, frozen against `uv.lock`,
  excluding dev deps. Refuses while a daemon is running. Idempotent.
- `agent-core daemon refresh [--extra <extra>]` — stop → install → start.
  Uses the stamped extra by default.
- `agent-core daemon status` — adds three lines (running-from interpreter,
  install metadata, lock-drift warning).

## Test plan

- [ ] All unit tests pass: `pytest packages/core/tests/test_daemon_cli.py packages/core/tests/test_daemon_install.py -v`
- [ ] Integration test passes locally: `pytest packages/core/tests/test_daemon_install_integration.py -v -m slow`
- [ ] Manual verification on Jeff's box completed (steps in `docs/superpowers/plans/2026-05-15-daemon-venv-isolation.md` Task 11).
- [ ] `uv sync` in the workspace while the daemon is running does not disrupt the daemon.
- [ ] `daemon status` output after the migration is included in this PR as evidence.

## Docs

- `docs/setup/daemon.md` — operator guide for the new install + refresh workflow.
- README pointer added.

## Out of scope

- Daemon auto-start at boot (separate ticket).
- Pepper Claude Code session auto-launch (separate effort).
- Containerization (forward-compatible; future work).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
