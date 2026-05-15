# Daemon ↔ workspace venv isolation — design

**Issue:** [#79](https://github.com/jeffrichley/agent_core/issues/79) — `daemon: develop alongside a running bus daemon — uv sync silently kills Pepper`

**Date:** 2026-05-15
**Status:** Approved, awaiting implementation

## Goal

Make the bus daemon structurally immune to `uv sync` activity in the agent-core workspace, so that adding workspace members or refreshing dependencies during development never disrupts a running Pepper or testbot.

## Background

On 2026-05-10 Pepper went silently offline mid-development at 09:40 ET while implementation work was happening on `agent-core-hatchery`. The daemon process exited cleanly with no error in `daemon.log.err`. Root cause: the daemon's Python interpreter is `sys.executable` — in practice `<workspace>/.venv/Scripts/python.exe` — and `uv sync` on Windows uses unlink-then-relink semantics that disrupt processes holding open `.pth` files and memory-mapped views from `.venv/Lib/site-packages/`. Adding a workspace member triggers a full re-resolve of the lockfile, which rewrites editable `.pth` files for every workspace package. The running daemon's imports either crash or the process exits silently.

This is not a theoretical risk. It happened, it cost Pepper's availability, and it will recur every time a new workspace package is added unless the daemon is structurally isolated from `.venv/`.

## Non-goals

- **Containerization.** Right eventual goal for multi-machine deploys; out of scope here. The design is forward-compatible: a future Docker container would carry the daemon venv as its `/app/.venv` and bind-mount `~/.agent-core/` as `/data/`.
- **Daemon auto-start at boot.** Separate ticket. This design provides the substrate (a stable, workspace-independent daemon-venv install) that auto-start sits on; it does not implement the Task Scheduler / service wrapper.
- **Pepper Claude Code session auto-launch.** Separate concern with its own design questions around terminal choice, attach pattern, and Claude Code preview-feature prompt handling. Tracked outside this spec.
- **Hot-reload of daemon code on workspace change.** Wrong shape for a long-lived stateful daemon. Refresh is explicit.
- **Editable installs in the daemon venv.** The non-editable property is what makes the isolation work; making it editable defeats the design.
- **A separate dev-daemon on a different port.** Composable with this work later if scale demands it; not needed now.

## Architecture

The daemon gets its own venv at `~/.agent-core/.venv/`, populated deliberately via `agent-core daemon install`. The workspace `.venv/` becomes a dev-only environment. `uv sync` in the workspace cannot reach the daemon venv because it lives outside the workspace tree, has no `.pth` files referencing workspace paths, and is never the target of any uv operation against the workspace project.

### Three new subcommands under `agent-core daemon`

- **`daemon install [--extra cu130|cpu] [--python 3.12]`** — populate `~/.agent-core/.venv/` from the workspace, non-editable, with the named extra. Idempotent. Refuses if a daemon is currently running.
- **`daemon refresh [--extra cu130|cpu]`** — convenience: `stop` → `install` → `start`. Aborts if any step fails. Reuses the extra from the install stamp if `--extra` is omitted.
- *(No `daemon uninstall` for v1 — operator can `rm -rf ~/.agent-core/.venv/`.)*

### Supervisor change

`packages/core/src/agent_core/daemon/cli.py` gains a `_daemon_python()` helper:

```python
def _daemon_python() -> str:
    """Return the daemon's preferred interpreter, with fallback."""
    if sys.platform == "win32":
        candidate = _home() / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = _home() / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable
```

The existing `subprocess.Popen([sys.executable, ...])` becomes `Popen([_daemon_python(), ...])`. That is the entire runtime change.

### Fallback semantics

If `~/.agent-core/.venv/python` doesn't exist, the supervisor falls back silently to `sys.executable` and behavior is identical to today. Merging the PR is a no-op at runtime; migration is opt-in via `agent-core daemon install` followed by a `daemon refresh`.

This choice is deliberate: today's behavior is "works most of the time; breaks during `uv sync`." It's not broken in steady-state, so a forced migration on first restart after merge would be unnecessarily aggressive.

## Component details

### `daemon install` mechanics

The command runs two steps:

```
uv venv  ~/.agent-core/.venv  --python <pinned>
UV_PROJECT_ENVIRONMENT=~/.agent-core/.venv \
  uv sync --frozen --no-editable --no-dev [--extra <extra>]
```

The `--no-dev` flag excludes the workspace's dev dependency group (pytest, mypy, ruff, etc.). The daemon doesn't need those at runtime, and leaving them out keeps the install lean.

The combination is the heart of the isolation:

- **`--frozen`** — installs against the workspace's `uv.lock` verbatim. Reproducible: a `daemon install` today and a `daemon install` tomorrow give the same daemon if `uv.lock` hasn't moved.
- **`--no-editable`** — workspace members are installed as wheels into `~/.agent-core/.venv/Lib/site-packages/`, not as `.pth` shims pointing at the workspace source tree. **This is what structurally prevents `uv sync` in the workspace from reaching the daemon venv.** The daemon venv has no `.pth` files referencing workspace paths; uv has no reason to touch it.
- **`--no-dev`** — excludes dev-only dependencies (pytest, mypy, ruff). The daemon never runs tests; keeping these out shrinks the install and reduces the surface for version drift.
- **`UV_PROJECT_ENVIRONMENT`** — tells uv "install into this venv, not the workspace's `.venv/`." Without this, uv would either refuse or sync the wrong venv.

**Install scope:** full workspace + GPU extras. The daemon imports every endpoint type configured in `~/.agent-core/agent_core.yaml` (voice, webcam, discord, briefs, scheduler, handoff-jobs, stub, claude-code-mcp), so the daemon venv must contain all of them. With voice in the workspace this means cu130 torch + transformers + qwen-tts ≈ 6–7 GB on disk. This is one-time cost; refresh is a delta operation.

**Workspace root discovery:** the install command ascends from `agent_core.__file__` until it finds a `pyproject.toml` containing `[tool.uv.workspace]`. If absent (e.g., `daemon install` invoked from outside the repo), it errors with a clear "couldn't find workspace root — run from within the agent-core repo."

**Python version:** `--python` defaults to `3.12` (matching the workspace's `requires-python`). Pinning prevents Python version drift between dev and daemon, which would otherwise be a hidden bug surface.

**Guard against in-place rewrite:** if a daemon is currently running, `daemon install` refuses with:

```
daemon is currently running (PID 24884).
   • Run `agent-core daemon stop` and re-run install, or
   • Run `agent-core daemon refresh` to stop/install/start in one step.
```

**Failure semantics:** install is not atomic. If `uv sync` fails partway, the venv is in a half-installed state. Retry is safe (uv sync is idempotent). No temp-dir-then-swap for v1.

**Idempotency:** rerunning install on an already-populated venv is fine. uv sync is fast on no-op.

### `daemon refresh` mechanics

```python
@app.command()
def refresh(extra: str | None = None) -> None:
    """Stop the daemon, reinstall the daemon venv, start the daemon."""
    stop()                # idempotent — no-op if not running
    install(extra=extra)  # uses stamped extra if --extra omitted
    start()
```

If `install` raises, `start` is not called. The daemon stays down; the operator sees the install error and retries.

### Install stamp file

After a successful install, `~/.agent-core/.daemon-install-stamp.json` captures:

```json
{
  "installed_at": "2026-05-15T19:31:04Z",
  "installed_sha": "42713d7",
  "python_version": "3.12.5",
  "extra": "cu130",
  "uv_lock_hash": "sha256:abc..."
}
```

- `installed_sha` is read via `git rev-parse HEAD` at install time.
- `uv_lock_hash` is sha256 of the workspace's `uv.lock` at install time.
- `extra` lets `daemon refresh` skip the `--extra` flag when the value hasn't changed.

### `daemon status` diagnostics

After the existing PID/log output, three new lines:

```
running from: ~/.agent-core/.venv/Scripts/python.exe
installed at: 2026-05-15T19:31:04Z
installed sha: 42713d7
```

If `_daemon_python()` returned `sys.executable` (fallback path), the "running from" line gets a dim red `(fallback — vulnerable to uv sync; run \`daemon install\`)` suffix. Quiet pressure to migrate; doesn't block anything.

If the stamp file's `uv_lock_hash` doesn't match the current workspace's `uv.lock`, `daemon status` adds a yellow `daemon venv may be stale — run \`agent-core daemon refresh\`` line. Status check only, never auto-refreshes.

## Migration sequence

One-time on Jeff's box, after the PR lands:

```
agent-core daemon stop
agent-core daemon install --extra cu130
agent-core daemon start
# verify Pepper + testbot are alive
# run a smoke MCP call
```

After that, the daily flow is:

```
agent-core daemon refresh   # picks up new daemon code; no --extra needed (stamped)
```

The daemon supervises both Pepper and testbot in a single process, so the migration flips both at once. There is no per-agent gradual rollout. Validation comes from the test suite plus a careful first-flip on Jeff's box. Per [[project_pepper_hands_off_until_proven]], the prior validation requirement applied to bus-migration cutovers; this is a runtime-isolation change that does not alter Pepper's wire behavior.

## Testing

### Unit tests (fast, no network)

1. `_daemon_python()` returns the daemon-venv interpreter when the file exists at the expected path; returns `sys.executable` otherwise. Cross-platform branch covered via `sys.platform` monkeypatch.
2. `daemon install` refuses when a daemon is running. Mock `is_alive=True`, assert `typer.Exit(code=1)` and the error message names both `stop` and `refresh` as remediation.
3. `daemon install` workspace-root discovery — given a tmp tree containing a `pyproject.toml` with `[tool.uv.workspace]`, ascends correctly from a deep `agent_core.__file__` mock. Errors clearly when no workspace is found.
4. `daemon install` issues the expected `uv venv` and `uv sync` commands (mocked subprocess). Assert `--frozen`, `--no-editable`, `UV_PROJECT_ENVIRONMENT`, and the right `--extra` propagation.
5. `daemon install` writes the stamp file with all five fields populated. Subsequent installs update the stamp in place.
6. `daemon refresh` calls `stop` → `install` → `start` in order. If `install` raises, `start` is never called and the exception propagates.
7. `daemon status` renders the "fallback — vulnerable to uv sync" suffix only when `_daemon_python()` returned `sys.executable`.
8. `daemon status` flags lock-drift when the stamp's `uv_lock_hash` doesn't match the current workspace's `uv.lock`.

### Integration test (slow, network)

9. `pytest.mark.slow` end-to-end install against a tmp `AGENT_CORE_HOME`. Verifies the daemon venv is fully populated against the workspace, the daemon can start from it, and the stamp file matches reality. Skipped in CI by default; runs locally before merge.

### Manual verification (one-time, on Jeff's box)

10. After running the migration sequence, run `uv sync` in the workspace while the daemon is up. Verify daemon heartbeat continues. Hatch a test-being or add a workspace member to reproduce the original failure mode and confirm it no longer occurs.

## Edge cases

- **Partial install state** (interrupted `uv sync`): `daemon refresh` re-runs cleanly because `uv sync` is idempotent. No special handling.
- **Python version drift** (`.venv/` was created with 3.11, workspace wants 3.12): `uv sync --frozen` refuses; the uv error is already actionable ("delete the venv and reinstall").
- **`uv` not on PATH**: subprocess raises `FileNotFoundError`. Install catches it and emits `"uv not found on PATH — install uv first: https://docs.astral.sh/uv/getting-started/installation/"`.
- **Extra change across refreshes** (`cu130` → `cpu`): `uv sync` handles the swap natively. Stamp file is rewritten.
- **Stale stamp** (manually edited or corrupt JSON): `daemon status` falls back to `"stamp missing/unreadable"` and skips the version diagnostics. Doesn't crash.

## Acceptance criteria

Mapped to the issue body's six criteria:

1. **Supervisor launches from `~/.agent-core/.venv/`'s Python interpreter when present, falls back to `sys.executable` when absent.** Covered by unit test #1 + the supervisor change.
2. **`agent-core daemon install` subcommand creates `~/.agent-core/.venv/` if missing and installs all current workspace packages non-editable. Idempotent.** Covered by tests #2–#5 + integration #9.
3. **Running `uv sync` in the workspace while the daemon is up has zero effect on the daemon process.** Structural property of the `--no-editable` install. Verifiable by manual verification #10.
4. **Tests cover: daemon-venv-present-and-used, daemon-venv-missing-fallback, install fresh, install idempotent.** Covered.
5. **Docs updated with the new dev workflow.** A new `docs/setup/daemon.md` covers `daemon install` as one-time setup and `daemon refresh` as the daily flow. The repository root `README.md` gets a one-line pointer to that doc.
6. **Pepper's existing setup is migrated cleanly: she runs from `~/.agent-core/.venv/` after the migration sequence.** Covered by manual verification #10; flipped atomically with testbot since they share a daemon.

## Path to containerization (forward-compatibility note)

If/when Pepper-style daemons need to ship to other machines (Cynthia, Stephanie, a server), the natural path is a Docker image. Three design choices made here translate directly:

- **State directory at `~/.agent-core/`** → bind-mounted as `/data/` in the container.
- **Daemon venv at `~/.agent-core/.venv/` outside the workspace** → lives at `/app/.venv` inside the image, baked at build time.
- **Frozen non-editable install** → already the right semantics for a container image; the lockfile-driven install becomes the Dockerfile's `RUN uv sync --frozen --no-editable`.

In other words, this design is the first step toward containerization, not orthogonal to it. The decision to defer Docker is about effort/payoff today, not about the wrong architecture.

## Composes with

- **[#75 (agent-core-hatchery)](https://github.com/jeffrichley/agent_core/issues/75)** — hatched beings each get their own project-scope `agent_core.yaml` and invoke `uv run agent-core hooks run X` against their project venv. Independent of the daemon-venv work but related; the hatchery's `templates/config/claude-settings.json.j2` should pin `--config <absolute-path>` to avoid the CWD-dependency bug Pepper hit on 2026-05-10.
- **Future containerization** — see "Path to containerization" above.
- **Future daemon auto-start at boot** — separate ticket. Task Scheduler action invokes `~/.agent-core/.venv/Scripts/agent-core.exe daemon start`. The daemon-venv design is the substrate.

## Open design questions

None remaining. Resolved during the 2026-05-15 brainstorm:

- **Editable vs non-editable installs into the daemon venv** → non-editable (editable defeats the isolation by reintroducing `.pth` files into the daemon venv).
- **Daemon venv Python version pinning** → yes, default `3.12`, overridable via `--python` on install.
- **PYTHONPATH-only alternative (no second venv)** → rejected. Full second venv with non-editable install.
- **Refresh UX** → bundled `daemon refresh` subcommand; not auto-restart in install.
- **Install scope** → full workspace + cu130 GPU extras for Jeff's primary machine. Other machines can pick a different extra.
- **Fallback behavior** → silent fallback to `sys.executable` when daemon venv is missing; loud warning in `daemon status`.

## Out of scope

- Containerization (Option 4 from the issue body).
- A separate dev daemon on port 8790 (Option 3 from the issue body).
- Hot-reload / file-watcher-driven restart.
- Editable installs in the daemon venv.
- Daemon auto-start at boot.
- Pepper Claude Code session auto-launch.

## Provenance

Surfaced 2026-05-10 by Jeff during agent-core-hatchery (#75) implementation work after Pepper went offline silently mid-session. Research and options evaluation in [#79](https://github.com/jeffrichley/agent_core/issues/79). Design brainstormed 2026-05-15 with Jeff; recommendation aligns with the issue body's Option 2 (separate daemon venv) and resolves the three open design questions from the issue body.
