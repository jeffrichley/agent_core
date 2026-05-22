# Phase 4 — Windows Daemon Auto-Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agent-core daemon install-autostart` / `uninstall-autostart` so the prod daemon comes back automatically at logon after a reboot (Windows Task Scheduler).

**Architecture:** A new pure/impure module `daemon/autostart.py` — `build_autostart_task` (pure) returns the Task Scheduler XML; `install_autostart` / `uninstall_autostart` (impure) shell out to `schtasks`. Two new `daemon/cli.py` commands wire them up, prod-only, with a Windows guard and an interactive "start now?" prompt.

**Tech Stack:** Python 3.12, Typer, `schtasks.exe` (Windows), pytest.

**Spec:** `docs/superpowers/specs/2026-05-22-phase4-windows-autostart-design.md`

**Worktree:** already created at `.worktrees/phase4-windows-autostart` on branch `feat/phase4-windows-autostart` (off `origin/main`, has Phase 3). Run all commands from inside that worktree. First, sync its venv + hooks:

```bash
cd .worktrees/phase4-windows-autostart
uv sync
just install-hooks
```

---

## File structure

### New files
- `packages/core/src/agent_core/daemon/autostart.py` — `TASK_NAME`, `build_autostart_task` (pure), `install_autostart` / `uninstall_autostart` (impure).
- `packages/core/tests/test_daemon_autostart.py` — unit tests for autostart.py (pure builder + faked-subprocess wrappers).

### Modified files
- `packages/core/src/agent_core/daemon/cli.py` — add `install-autostart` + `uninstall-autostart` commands.
- `packages/core/tests/test_daemon_cli.py` — add command tests (faked subprocess) + one `slow` real-`schtasks` integration test.
- `docs/setup/daemon.md` — add an "Auto-start at boot" section.

---

## Task 1: `daemon/autostart.py` — the module (TDD)

**Files:**
- Create: `packages/core/src/agent_core/daemon/autostart.py`
- Test: `packages/core/tests/test_daemon_autostart.py`

- [ ] **Step 1: Write the failing tests**

Write to `packages/core/tests/test_daemon_autostart.py`:
```python
"""Unit tests for daemon/autostart.py — Task Scheduler registration."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agent_core.daemon.autostart import (
    TASK_NAME,
    build_autostart_task,
    install_autostart,
    uninstall_autostart,
)

# Task Scheduler XML namespace.
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


# ---- build_autostart_task (pure) -------------------------------------------

def test_task_name_constant() -> None:
    assert TASK_NAME == "agent-core-daemon-prod"


def test_build_autostart_task_is_well_formed_xml() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\Users\x\.agent-core\.venv\Scripts\agent-core.exe"),
        account="x",
    )
    # Must parse without error.
    ET.fromstring(xml)


def test_build_autostart_task_action_command_and_args() -> None:
    exe = Path(r"C:\Users\x\.agent-core\.venv\Scripts\agent-core.exe")
    xml = build_autostart_task(agent_core_exe=exe, account="x")
    root = ET.fromstring(xml)
    command = root.find(".//t:Actions/t:Exec/t:Command", _NS)
    arguments = root.find(".//t:Actions/t:Exec/t:Arguments", _NS)
    assert command is not None and command.text == str(exe)
    assert arguments is not None
    assert arguments.text == "daemon start --instance prod"


def test_build_autostart_task_has_logon_trigger_for_account() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\x\agent-core.exe"), account="jeffr"
    )
    root = ET.fromstring(xml)
    trigger = root.find(".//t:Triggers/t:LogonTrigger", _NS)
    assert trigger is not None
    user = trigger.find("t:UserId", _NS)
    assert user is not None and user.text == "jeffr"


def test_build_autostart_task_settings() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\x\agent-core.exe"), account="x"
    )
    root = ET.fromstring(xml)
    settings = root.find("t:Settings", _NS)
    assert settings is not None
    assert settings.findtext("t:MultipleInstancesPolicy", namespaces=_NS) == "IgnoreNew"
    assert settings.findtext("t:StartWhenAvailable", namespaces=_NS) == "true"
    assert settings.findtext("t:ExecutionTimeLimit", namespaces=_NS) == "PT0S"
    restart = settings.find("t:RestartOnFailure", _NS)
    assert restart is not None
    assert restart.findtext("t:Interval", namespaces=_NS) == "PT1M"
    assert restart.findtext("t:Count", namespaces=_NS) == "3"


def test_build_autostart_task_principal_is_least_privilege() -> None:
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\x\agent-core.exe"), account="jeffr"
    )
    root = ET.fromstring(xml)
    principal = root.find(".//t:Principals/t:Principal", _NS)
    assert principal is not None
    assert principal.findtext("t:RunLevel", namespaces=_NS) == "LeastPrivilege"
    assert principal.findtext("t:UserId", namespaces=_NS) == "jeffr"


# ---- install_autostart / uninstall_autostart (impure, faked subprocess) ----

def test_install_autostart_invokes_schtasks_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)

    install_autostart("<Task></Task>")

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "schtasks"
    assert "/create" in cmd
    assert "/tn" in cmd and TASK_NAME in cmd
    assert "/xml" in cmd
    assert "/f" in cmd


def test_install_autostart_raises_on_schtasks_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class _R:
            returncode = 1
            stdout = ""
            stderr = "ERROR: Access is denied."
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        install_autostart("<Task></Task>")


def test_uninstall_autostart_returns_true_on_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class _R:
            returncode = 0
            stdout = "SUCCESS"
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)
    assert uninstall_autostart() is True


def test_uninstall_autostart_returns_false_when_task_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class _R:
            returncode = 1
            stdout = ""
            stderr = "ERROR: The system cannot find the file specified."
        return _R()

    monkeypatch.setattr("agent_core.daemon.autostart.subprocess.run", fake_run)
    assert uninstall_autostart() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_autostart.py -v`
Expected: `ModuleNotFoundError: No module named 'agent_core.daemon.autostart'`

- [ ] **Step 3: Implement `daemon/autostart.py`**

Write to `packages/core/src/agent_core/daemon/autostart.py`:
```python
"""Windows daemon auto-start — register the prod daemon as a Task Scheduler task.

Pure/impure split (project convention, mirrors build_uv_sync_command /
run_install): `build_autostart_task` is pure (returns the Task Scheduler XML),
`install_autostart` / `uninstall_autostart` are thin impure shells that run
`schtasks`.

Windows-only at the `schtasks` boundary. The pure builder is plain
string-building and works on any platform, so its tests run on any CI OS.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

TASK_NAME = "agent-core-daemon-prod"


def build_autostart_task(*, agent_core_exe: Path, account: str) -> str:
    """Return the Task Scheduler XML registering the prod daemon at logon.

    The task runs `<agent_core_exe> daemon start --instance prod` at
    `account`'s logon, with restart-on-failure (PT1M x3), start-when-
    available, ignore-new-instance, no execution time limit, least
    privilege.
    """
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>agent-core prod daemon auto-start at logon."
        "</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"      <UserId>{account}</UserId>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{account}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>3</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{agent_core_exe}</Command>\n"
        "      <Arguments>daemon start --instance prod</Arguments>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def install_autostart(xml: str) -> None:
    """Register (or replace) the autostart task from `xml`.

    Writes the XML to a temp file (UTF-16 — the Task Scheduler XML prolog
    declares UTF-16) and runs `schtasks /create /tn <TASK_NAME> /xml
    <file> /f`. Raises subprocess.CalledProcessError on a non-zero exit.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-16", suffix=".xml", delete=False
    ) as fh:
        fh.write(xml)
        xml_path = fh.name
    try:
        result = subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", xml_path, "/f"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(xml_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", "...", "/f"],
            output=result.stdout,
            stderr=result.stderr,
        )


def uninstall_autostart() -> bool:
    """Delete the autostart task. Returns True if a task was deleted,
    False if no such task existed. Idempotent — never raises for a
    missing task."""
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_autostart.py -v`
Expected: 10 tests pass.

- [ ] **Step 5: Lint + typecheck**

Run: `uv run --no-sync ruff check --fix packages/core/src/agent_core/daemon/autostart.py packages/core/tests/test_daemon_autostart.py && uv run --no-sync ruff check packages/core/src/agent_core/daemon/autostart.py packages/core/tests/test_daemon_autostart.py && uv run --no-sync mypy packages/core/src/agent_core/daemon/autostart.py`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/autostart.py packages/core/tests/test_daemon_autostart.py
git commit -m "feat(daemon): autostart.py — Task Scheduler XML builder + schtasks wrappers"
```

---

## Task 2: `install-autostart` / `uninstall-autostart` CLI commands

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py`
- Test: `packages/core/tests/test_daemon_cli.py`

- [ ] **Step 1: Add the import**

In `packages/core/src/agent_core/daemon/cli.py`, add to the import block
(after the other `agent_core.daemon.*` imports):
```python
from agent_core.daemon import autostart
```

Importing the module (not the names) avoids any collision between the
command function `install_autostart` and the module function
`autostart.install_autostart`.

- [ ] **Step 2: Add the two commands**

Append to `packages/core/src/agent_core/daemon/cli.py` (after the
`refresh` command, before the trailing `_git_sha_of_tag` helper — or at
end of file, ordering does not matter to Typer):
```python
@app.command()
def install_autostart(
    instance: str | None = _INSTANCE_OPTION,
    start: bool | None = typer.Option(
        None,
        "--start/--no-start",
        help="Start the daemon now without prompting (for non-interactive use).",
    ),
) -> None:
    """Register the prod daemon to auto-start at logon (Windows Task Scheduler)."""
    inst = _resolve(instance)
    if inst is not Instance.PROD:
        console.print(
            "[red]autostart is prod-only[/red] — the dev instance is started "
            "by hand from the workspace."
        )
        raise typer.Exit(code=1)
    if sys.platform != "win32":
        console.print("[red]autostart is Windows-only.[/red]")
        raise typer.Exit(code=1)

    home = home_for(inst)
    exe = home / ".venv" / "Scripts" / "agent-core.exe"
    if not exe.exists():
        console.print(
            f"[red]prod daemon is not installed ({exe} missing).[/red]\n"
            "   Run [bold]agent-core daemon install[/bold] first."
        )
        raise typer.Exit(code=1)

    # USERNAME is always set on Windows; os.getlogin() is the fallback.
    account = os.environ.get("USERNAME") or os.getlogin()
    xml = autostart.build_autostart_task(agent_core_exe=exe, account=account)
    try:
        autostart.install_autostart(xml)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]schtasks failed (exit {exc.returncode}).[/red]")
        if exc.stderr:
            console.print(exc.stderr.rstrip())
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]registered autostart task '{autostart.TASK_NAME}'[/green] — "
        "the prod daemon will start at every logon."
    )

    should_start = start
    if should_start is None:
        should_start = typer.confirm("Start the prod daemon now?", default=False)
    if should_start:
        start_daemon(instance="prod")


@app.command()
def uninstall_autostart(instance: str | None = _INSTANCE_OPTION) -> None:
    """Remove the prod daemon auto-start task (Windows Task Scheduler)."""
    inst = _resolve(instance)
    if inst is not Instance.PROD:
        console.print("[red]autostart is prod-only.[/red]")
        raise typer.Exit(code=1)
    if sys.platform != "win32":
        console.print("[red]autostart is Windows-only.[/red]")
        raise typer.Exit(code=1)

    if autostart.uninstall_autostart():
        console.print(
            f"[green]removed autostart task '{autostart.TASK_NAME}'[/green]"
        )
    else:
        console.print(
            f"[yellow]no autostart task '{autostart.TASK_NAME}' to remove[/yellow]"
        )
```

- [ ] **Step 3: Rename the `start` command to avoid the call-name collision**

The `install_autostart` command above calls `start_daemon(instance="prod")`.
The existing `start` command function must be reachable under that name.
**Do not rename the `start` command** — instead, add a module-level
alias right after the `start` command definition so `install_autostart`
can call it without shadowing issues:

Find the end of the `start` command (the line
`console.print(f"[green]{inst} daemon started (PID: {proc.pid})[/green]")`)
and immediately after the function add:
```python


# Alias so other commands can call `start` without ambiguity.
start_daemon = start
```

(`start` remains the Typer command; `start_daemon` is the same function
object, used for internal calls.)

- [ ] **Step 4: Write the command tests**

Append to `packages/core/tests/test_daemon_cli.py`:
```python
def test_install_autostart_dev_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["install-autostart", "--instance", "dev"])
    assert result.exit_code == 1
    assert "prod-only" in result.stdout.lower()


def test_install_autostart_errors_when_prod_exe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-autostart must refuse if the prod agent-core.exe is absent."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 1
    assert "not installed" in result.stdout.lower()


def test_install_autostart_registers_and_skips_start_with_no_start_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    # Create the prod exe so the existence check passes.
    exe = tmp_path / ".venv" / "Scripts" / "agent-core.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")

    registered: list[str] = []

    def fake_install(xml: str) -> None:
        registered.append(xml)

    started: list[str] = []

    def fake_start(instance: str | None = None) -> None:
        started.append("start")

    monkeypatch.setattr("agent_core.daemon.autostart.install_autostart", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start_daemon", fake_start)

    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 0, result.stdout
    assert len(registered) == 1  # task registered
    assert started == []  # --no-start skipped the daemon start


def test_install_autostart_starts_with_start_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    exe = tmp_path / ".venv" / "Scripts" / "agent-core.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")

    monkeypatch.setattr(
        "agent_core.daemon.autostart.install_autostart", lambda xml: None
    )
    started: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.cli.start_daemon",
        lambda instance=None: started.append("start"),
    )

    result = runner.invoke(daemon_app, ["install-autostart", "--start"])
    assert result.exit_code == 0, result.stdout
    assert started == ["start"]


def test_uninstall_autostart_reports_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "agent_core.daemon.autostart.uninstall_autostart", lambda: True
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0
    assert "removed" in result.stdout.lower()


def test_uninstall_autostart_idempotent_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "agent_core.daemon.autostart.uninstall_autostart", lambda: False
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0  # not an error
    assert "no autostart task" in result.stdout.lower()
```

- [ ] **Step 5: Run the daemon test suite**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_cli.py packages/core/tests/test_daemon_autostart.py -v`
Expected: all pass. If a test fails because `monkeypatch.setattr(sys, "platform", ...)` does not take effect, confirm the command reads `sys.platform` at call time (it does — the code references `sys.platform` directly inside the command body).

- [ ] **Step 6: Lint + typecheck**

Run: `uv run --no-sync ruff check --fix packages/core && uv run --no-sync ruff check packages/core && uv run --no-sync mypy packages/core/src/agent_core/daemon/cli.py`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): install-autostart + uninstall-autostart commands"
```

---

## Task 3: Slow integration test + docs

**Files:**
- Modify: `packages/core/tests/test_daemon_autostart.py` (add one `slow` test)
- Modify: `docs/setup/daemon.md`

- [ ] **Step 1: Add the slow real-`schtasks` integration test**

Append to `packages/core/tests/test_daemon_autostart.py`:
```python
import shutil
import sys


@pytest.mark.slow
@pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("schtasks") is None,
    reason="schtasks is Windows-only",
)
def test_build_xml_is_accepted_by_real_schtasks() -> None:
    """The XML build_autostart_task emits must be accepted by the real
    `schtasks`. Registers under a throwaway name, queries it back, deletes
    it — never touches the real 'agent-core-daemon-prod' task.
    """
    test_task = "agent-core-daemon-phase4-itest"
    xml = build_autostart_task(
        agent_core_exe=Path(r"C:\Windows\System32\cmd.exe"),
        account="itest",
    )
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-16", suffix=".xml", delete=False
    ) as fh:
        fh.write(xml)
        xml_path = fh.name
    try:
        create = subprocess.run(
            ["schtasks", "/create", "/tn", test_task, "/xml", xml_path, "/f"],
            capture_output=True, text=True, check=False,
        )
        assert create.returncode == 0, (
            f"schtasks rejected the XML: {create.stdout} {create.stderr}"
        )
        query = subprocess.run(
            ["schtasks", "/query", "/tn", test_task],
            capture_output=True, text=True, check=False,
        )
        assert query.returncode == 0
        assert test_task in query.stdout
    finally:
        Path(xml_path).unlink(missing_ok=True)
        subprocess.run(
            ["schtasks", "/delete", "/tn", test_task, "/f"],
            capture_output=True, text=True, check=False,
        )
```

Note: the `account="itest"` in this test is a non-existent user. `schtasks
/create` validates the XML structure but for a `LogonTrigger`/`Principal`
with a bogus account it may warn or fail. If `schtasks` rejects the bogus
account, change the test to use the real current account:
`os.getlogin()` (add `import os`). The real account always exists on the
runner. Prefer `os.getlogin()` for robustness.

- [ ] **Step 2: Run the slow test**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_autostart.py -v -m slow`
Expected: PASS on Windows. If `schtasks /create` fails on the account,
apply the `os.getlogin()` fix from Step 1's note and re-run.

- [ ] **Step 3: Add the daemon.md autostart section**

Append to `docs/setup/daemon.md`:
```markdown
## Auto-start at boot (Windows)

The prod daemon can register itself with Windows Task Scheduler so it
returns automatically after a reboot — no manual `daemon start`.

```bash
# Register the autostart task (prod only). Prompts whether to start now.
agent-core daemon install-autostart

# Remove it.
agent-core daemon uninstall-autostart
```

`install-autostart` registers a Task Scheduler task named
`agent-core-daemon-prod` that runs `agent-core daemon start --instance
prod` at your logon, with restart-on-failure and start-when-available.
It runs as your own user with least privilege (the daemon binds
127.0.0.1 only — no admin needed).

`install-autostart` requires the prod daemon to be installed first
(`agent-core daemon install`) — it points the task at
`~/.agent-core/.venv/Scripts/agent-core.exe`. Re-running it replaces the
existing task (idempotent). Pass `--no-start` / `--start` to skip the
"start now?" prompt in non-interactive use.

Auto-start is prod-only and Windows-only. It brings the **daemon** back
after a reboot; auto-launching Pepper's own Claude Code session is a
separate, still-open problem.
```

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/test_daemon_autostart.py docs/setup/daemon.md
git commit -m "test(daemon): slow real-schtasks integration test; docs(daemon): autostart section"
```

---

## Task 4: Final validation + PR

**Files:** none — verification only.

- [ ] **Step 1: Full `just check`**

Run: `just check`
Expected: PASS — lint, typecheck, contracts, all fast tests green.

- [ ] **Step 2: Review the commit history**

Run: `git log origin/main..HEAD --oneline`
Expected: spec + plan + Tasks 1-3 commits, all conventional-commit messages.

- [ ] **Step 3: Push + open the PR**

```bash
git push -u origin feat/phase4-windows-autostart
```

Then (PR title MUST be lowercase after the `type(scope):` prefix — the
pr-title-lint `subjectPattern` rejects an uppercase subject):
```bash
gh pr create --title "feat(daemon): windows daemon auto-start (Phase 4)" --body-file - <<'EOF'
## Summary
- New `agent-core daemon install-autostart` / `uninstall-autostart` commands
- Registers a Windows Task Scheduler task that starts the prod daemon at logon
- New pure/impure module `daemon/autostart.py` (`build_autostart_task` pure → Task Scheduler XML; `install_autostart`/`uninstall_autostart` impure → `schtasks`)
- Prod-only, Windows-only, no admin; idempotent; `--start/--no-start` flag else interactive prompt

Spec: `docs/superpowers/specs/2026-05-22-phase4-windows-autostart-design.md`
Plan: `docs/superpowers/plans/2026-05-22-phase4-windows-autostart.md`

## Test plan
- [ ] All phase1-main-gate checks green (ubuntu + windows + integration + PR title)
- [ ] After merge: `agent-core daemon install-autostart` on the box, then reboot to confirm the daemon returns
EOF
```

- [ ] **Step 4: Drive CI green**

Wait for all four checks (`check (ubuntu-latest)`, `check (windows-latest)`,
`integration`, `Validate PR title`). The PR title above is already
lowercase-subject compliant. Do NOT bypass the gate. Report the PR URL
and green status; squash-merge is the human step.

---

## Self-review check

Spec coverage:
- §2.1 `install-autostart` (prod guard, exe check, register, start prompt/flag) — Task 2
- §2.2 `uninstall-autostart` (prod guard, idempotent delete) — Task 2
- §3.1 XML import mechanism — Task 1 (`install_autostart` writes UTF-16 temp file + `schtasks /create /xml`)
- §3.2 task definition (name, LogonTrigger, Exec action, settings, LeastPrivilege principal) — Task 1 (`build_autostart_task`) + its tests
- §4.1 `autostart.py` pure/impure split — Task 1
- §4.2 cli.py commands — Task 2
- §5 error handling (non-Windows guard, missing exe, schtasks failure, idempotency) — Task 2 (guards) + Task 1 (`uninstall_autostart` returns False not raise)
- §6 testing (pure builder heavy, faked-subprocess command tests, one slow integration test, non-Windows guard) — Tasks 1, 2, 3
- §7 rollout — Task 4 (PR) + Task 3 (docs)

No spec section uncovered. No placeholders. Type/name consistency
checked: `TASK_NAME`, `build_autostart_task(*, agent_core_exe, account)`,
`install_autostart(xml)`, `uninstall_autostart() -> bool`, the
`autostart` module import, the `start_daemon` alias, and the command
function names `install_autostart` / `uninstall_autostart` (Typer →
`install-autostart` / `uninstall-autostart`) are used consistently
across all tasks.
