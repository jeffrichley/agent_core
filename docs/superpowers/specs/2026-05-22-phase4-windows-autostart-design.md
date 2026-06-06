# Phase 4 — Daemon Auto-Start at Boot (Windows): Design

**Date:** 2026-05-22
**Status:** brainstormed; pending implementation plan
**Relationship to the maturity spec:** Implements the `Phase 4 — Daemon
auto-start at boot` section of
`docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md`. Where
they differ, **this document wins**. Builds on Phase 3 (the
`--instance {prod|dev}` daemon surface, merged in PR #108).

---

## 1. Goal

The prod daemon should come back automatically after a machine reboot, so
Pepper and Wren are available without Jeff manually running
`agent-core daemon start`. Today a reboot leaves the daemon down until
Jeff notices and restarts it by hand.

Phase 4 adds two commands that register / remove a Windows Task Scheduler
task. The task starts the **prod** daemon at logon.

Out of scope (per umbrella spec): Pepper-session auto-launch — its
blocker (the Claude Code onboarding/preview prompt on version drift) is
unsolved. Phase 4 ensures only the *daemon* returns after a reboot. See
§7 for the pointer to the always-on-Pepper goal.

## 2. Commands

Two new `agent-core daemon` subcommands. Both are **prod-only**,
localhost-only, no-admin.

### 2.1 `agent-core daemon install-autostart`

1. Resolve the instance. **Prod only** — `--instance dev` errors,
   consistent with Phase 3's `install --instance dev` rejection.
2. Verify the prod daemon exe exists at
   `~/.agent-core/.venv/Scripts/agent-core.exe`. If missing, error:
   *"prod daemon is not installed — run `agent-core daemon install`
   first"*. Auto-start cannot point at an exe that does not exist.
3. Build the Task Scheduler XML (pure) and register it via
   `schtasks /create … /f` (idempotent — `/f` replaces an existing
   task of the same name).
4. **Prompt:** `Start the prod daemon now? [y/N]`. On yes, call
   `start(instance="prod")`. A `--start/--no-start` flag overrides the
   prompt for non-interactive use (scripts, tests); when neither is
   passed and stdin is interactive, the prompt is shown.

### 2.2 `agent-core daemon uninstall-autostart`

1. Prod-only guard (same as above).
2. Delete the Task Scheduler task via `schtasks /delete … /f`.
   Idempotent: if the task does not exist, report that and exit 0 — not
   an error.

## 3. The registered Task Scheduler task

### 3.1 Why XML import (not `schtasks` flags, not PowerShell)

`schtasks /create` command-line flags cannot express the full settings
set this phase needs — restart-on-failure count, "start when available,"
and the multiple-instances policy live only in the Task Scheduler **XML
schema**. So the mechanism is: build the XML, write it to a temp file,
`schtasks /create /tn <name> /xml <file> /f`. PowerShell
`Register-ScheduledTask` could also do it, but XML-as-data is a cleaner
return value for a pure function than a PowerShell script string.

### 3.2 Task definition

- **Name:** `agent-core-daemon-prod`
- **Trigger:** `LogonTrigger` scoped to the current user account.
- **Action:** `Exec` —
  command `~/.agent-core/.venv/Scripts/agent-core.exe`,
  arguments `daemon start --instance prod`.
- **Settings:**
  - `RestartOnFailure` — interval `PT1M`, count `3` (small retry).
  - `StartWhenAvailable` — `true` (run a missed start as soon as
    possible).
  - `MultipleInstancesPolicy` — `IgnoreNew` (never start a second
    daemon; `daemon start` also self-guards via the PID file).
  - `ExecutionTimeLimit` — `PT0S` (no time limit — the daemon is
    long-running).
- **Principal:** runs as the current user, `LeastPrivilege` run level
  (no admin; the daemon binds 127.0.0.1 only).

## 4. Code structure

Pure/impure split per project convention (mirrors
`build_uv_sync_command` pure / `run_install` impure).

### 4.1 New — `packages/core/src/agent_core/daemon/autostart.py`

- `TASK_NAME = "agent-core-daemon-prod"` — module constant.
- `build_autostart_task(*, agent_core_exe: Path, account: str) -> str`
  — **pure**: returns the Task Scheduler XML as a string. Deterministic,
  no I/O. The single source of the task definition; fully
  unit-testable.
- `install_autostart(xml: str) -> None` — **impure** thin shell: writes
  `xml` to a temp file, runs
  `schtasks /create /tn <TASK_NAME> /xml <file> /f`, raises
  `subprocess.CalledProcessError` on a non-zero exit.
- `uninstall_autostart() -> bool` — **impure**: runs
  `schtasks /delete /tn <TASK_NAME> /f`; returns `True` if a task was
  deleted, `False` if `schtasks` reported the task did not exist.

### 4.2 Modified — `packages/core/src/agent_core/daemon/cli.py`

Adds the two `@app.command()` functions `install_autostart` /
`uninstall_autostart` (Typer maps the underscores to
`install-autostart` / `uninstall-autostart`). They:
- Reuse the Phase 3 `_resolve` + the prod-only guard.
- Resolve `agent_core_exe` from the prod home
  (`home / ".venv" / "Scripts" / "agent-core.exe"`) and the account
  from `os.getlogin()` (or the `USERNAME` env var).
- Call into `autostart.py`.
- `install-autostart` runs the start-now prompt / flag.

## 5. Error handling

- **Non-Windows platform:** `schtasks` does not exist. Both commands
  check `sys.platform == "win32"` and error cleanly
  (*"autostart is Windows-only"*) on other platforms. The pure
  `build_autostart_task` is platform-agnostic string-building — its
  tests run on any CI OS.
- **Prod exe missing:** `install-autostart` errors before touching
  `schtasks` (see §2.1 step 2).
- **`schtasks` non-zero exit on create:** surfaces the returncode +
  stderr; the command exits non-zero.
- **Re-running `install-autostart`:** `/f` replaces the existing task —
  idempotent.
- **`uninstall-autostart` with no task:** not an error (exit 0).

## 6. Testing strategy

All fast (the Phase 1 `check` gate) except one slow integration test.

- **`build_autostart_task` (pure)** — the bulk of coverage, no I/O:
  the returned XML parses (via `xml.etree.ElementTree`); contains the
  exact action command path and `daemon start --instance prod`
  arguments; has a `LogonTrigger`; has `RestartOnFailure` (interval +
  count), `StartWhenAvailable`, `IgnoreNew`, and no execution time
  limit; the principal is `LeastPrivilege` for the supplied account.
- **`install-autostart` command** — `schtasks` faked via a monkeypatched
  subprocess runner: errors when the prod exe is missing; errors on
  `--instance dev`; `--no-start` skips the daemon start; `--start`
  calls `start`.
- **`uninstall_autostart`** — faked subprocess: returns `True` on a
  successful delete, `False` when `schtasks` reports the task is
  absent.
- **Non-Windows guard** — on a non-`win32` platform the commands error
  cleanly; `build_autostart_task` still works.
- **One `slow`-marked integration test** — registers a task under a
  **throwaway test-only name** (never `agent-core-daemon-prod`), queries
  it back via `schtasks /query`, then deletes it. Proves the XML the
  pure function emits is actually accepted by the real `schtasks`.
  Skipped on non-Windows.

## 7. Rollout

1. Phase 4 PR (`autostart.py` + the two cli commands + tests + a
   `docs/setup/daemon.md` section on auto-start) → passes
   `phase1-main-gate` → squash-merged via the gate.
2. After merge: on the box, once the prod daemon is installed,
   `agent-core daemon install-autostart` — registers the task, answer
   the start-now prompt. The daemon now returns on every logon.
3. **Always-on-Pepper:** Phase 4 closes the *daemon* half of the 24/7
   goal. The remaining half — auto-launching Pepper's own Claude Code
   session — stays blocked on the onboarding/preview prompt and is
   tracked separately, not in this phase.

## 8. The maturity initiative after Phase 4

Phases 0, 1, 1.5, 2, 2.5, 3, 4 complete the umbrella maturity spec.
Known remaining follow-up outside the phase sequence: **issue #107** —
the release-please ↔ CI automation gap (PAT / GitHub App), independent
of Phase 4.
