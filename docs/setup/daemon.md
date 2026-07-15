# Daemon setup and refresh workflow

The agent-core bus daemon supervises every endpoint (bus, Discord adapters,
scheduler, briefs, webcam, voice). It runs from its **own** venv at
`~/.agent-core/.venv/` — not from the workspace's `.venv/` — so `uv sync`
activity in the workspace cannot disrupt the running daemon process.

## Instances (Phase 3.5)

The daemon supports three instances, selected with `--instance` on every
`agent-core daemon` subcommand:

| | `prod` (default) | `source` | `test` |
|---|---|---|---|
| Home | `~/.agent-core/` | `~/.agent-core-source/` | `~/.agent-core-test/` |
| Bus port | 8789 | 8788 | 8787 |
| Code source | release artifacts (`daemon install`) | the workspace `.venv` (editable) | release artifacts (`daemon install --release vX.Y.Z`) |
| Install stamp | yes | none — source is not installed | yes |

With no `--instance` flag and no `AGENT_CORE_INSTANCE` env var, every
command resolves to `prod` with port 8789 — the live Pepper + Wren home.
The `source` and `test` instances are purely additive: a `source`/`test`
`start`/`stop`/`refresh`/crash can never touch prod's venv, port, or state.

`AGENT_CORE_HOME`, when set, overrides the home directory directly (test
escape hatch).

## Prod: one-time setup

Prod is installed from a GitHub Release artifact (Phase 2.5 — the daemon
no longer builds from workspace source):

```bash
# Scaffold the prod config if this box has never run the daemon.
agent-core daemon init

# Install the latest release into ~/.agent-core/.venv/.
agent-core daemon install

# Start the daemon.
agent-core daemon start

# Verify.
agent-core daemon status
```

`daemon status` should show:

```
prod daemon is running (PID: NNNNN)
running from: ~/.agent-core/.venv/Scripts/python.exe   (or bin/python on POSIX)
installed at: <timestamp>
installed sha: <git short hash>
installed version: <X.Y.Z>
```

## Prod: picking up a new release

```bash
agent-core daemon refresh                  # install the latest release
agent-core daemon refresh --release v0.2.0 # pin a specific version (rollback)
```

`refresh` does, in order: `stop` → `install` (download the release wheels +
`requirements.txt`, `uv pip install` into the daemon venv) → `start`. See
`docs/setup/releases.md` for the full release flow.

> **Live agent sessions survive a bounce (#91).** Agents reach the bus
> tool surface through the `agent-core-busproxy` stdio MCP server (not
> directly over HTTP). Each tool call mints a fresh backend session, so
> `daemon refresh`/`install`/`start`/`stop` (and crashes) no longer
> strand a live Claude Code session: in-flight calls during the down
> window return a structured `{"error":"bus_unavailable","transient":true,
> "retry_after_seconds":5}` result that the agent retries, and the next
> call after the daemon is back succeeds with no session restart. The
> `agent-core-channel` wake relay reconnects on its own.

## Source instance

The source instance lets you iterate on daemon code without bouncing the
prod daemon that Pepper and Wren depend on. It runs **editable from the
workspace `.venv`** — your source edits are live on the next restart.

### One-time source setup

```bash
agent-core daemon init --instance source      # scaffolds ~/.agent-core-source/agent_core.yaml
agent-core daemon start --instance source     # runs from the workspace .venv
```

`start --instance source` must be run from inside the agent_core repo — it
resolves the workspace `.venv` for the daemon interpreter.

### The source loop

Edit daemon code in the repo, then:

```bash
agent-core daemon refresh --instance source
```

For source, `refresh` is a plain stop + start — **no install step**. The source
daemon runs editable from the workspace `.venv`, so the restart picks up
your latest source edits.

`agent-core daemon install --instance source` is intentionally an error: the
source instance is never installed.

The instance can also be set with the `AGENT_CORE_INSTANCE` env var.

## Test instance

The test instance validates the **release deploy path end-to-end** before
refreshing prod. Use it to confirm that a release candidate installs cleanly
from wheels, that entry points resolve correctly, and that the daemon starts
and handles traffic — all without touching the live prod or source instances.

### What distinguishes test from source

- **source** runs editable from the workspace `.venv` — code changes are live
  on the next restart, no install required.
- **test** installs from release wheels (same path as prod) — it exercises
  the packaging and entry-point wiring that only shows up in a built artifact.

### Typical test workflow

```bash
# 1. Scaffold the test config (one time).
agent-core daemon init --instance test

# 2. Install a specific release into ~/.agent-core-test/.venv/.
agent-core daemon install --instance test --release vX.Y.Z

# 3. Start the test daemon (port 8787).
agent-core daemon start --instance test

# 4. Exercise — run whatever checks, smoke-tests, or agent sessions you need.

# 5. Stop and clean up when done.
agent-core daemon stop --instance test
```

Once satisfied, promote to prod:

```bash
agent-core daemon refresh --release vX.Y.Z   # or just: agent-core daemon refresh
```

## Why the daemon runs from its own venv

Before the daemon venv was isolated, it ran from
`<workspace>/.venv/Scripts/python.exe`. On Windows, `uv sync` uses
unlink-then-relink semantics that disrupt running processes holding open
files in `.venv/`. Adding a workspace member re-resolved the lockfile and
rewrote every package's editable `.pth`, silently killing the running
daemon. Pepper went offline mid-session on 2026-05-10 from exactly this.

The fix: install the prod daemon non-editable into a venv outside the
workspace tree. The source instance accepts the editable workspace venv on
purpose — it exists for iteration, and is never the live Pepper/Wren home.

## Defect A — `cache-keys` history

uv's build cache for a *local/workspace path* dependency is keyed by the
`pyproject.toml` mtime, **not** the package version string (verified
empirically on uv 0.7.13, cf. uv issue #15224). Phase 0 added
`[tool.uv] cache-keys` with a `git commit` entry to every member
`pyproject.toml` so a source-only change invalidates the cached wheel.
Phase 2.5 then moved prod installs to **pre-built release artifacts**, so
the daemon no longer builds from workspace source at all — the cache-keys
remain as defence for the CI wheel build and any local `uv build`.
`packages/core/tests/test_member_cache_keys_guard.py` fails the fast test
gate if any member loses the key.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No daemon config at ...` on `start` | config not scaffolded | `agent-core daemon init [--instance source\|test]` |
| `daemon is currently running` on `install` | prod daemon is up | `agent-core daemon refresh` |
| `source daemon needs the workspace .venv` | ran `start --instance source` outside the repo | `cd` into the agent-core repo |
| `the source instance is not installed` | ran `install --instance source` | source needs no install — `daemon start --instance source` |
| `unknown instance` | bad `--instance` value | use `prod`, `source`, or `test` |

## Related

- `docs/setup/releases.md` — the release flow (release-please + GH Release artifacts).
- `docs/setup/ci.md` — the CI gate and the one-time `just install-hooks` bootstrap.
- `docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md` — the three-instance design (prod/source/test).
- `docs/superpowers/specs/2026-05-21-phase3-dev-prod-daemon-design.md` — the original dev/prod instance design.
- `docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md` — the maturity spec.
- [#91](https://github.com/jeffrichley/agent_core/issues/91) — daemon-bounce MCP session recovery.
- `agent_core.daemon.cli` — supervisor + instance code.
- `agent_core.daemon.instance` — instance resolution.

## Agent `.mcp.json` (resilient shape)

Each agent's `<agent_root>/.mcp.json` points the bus tool surface at the
stdio busproxy — never the daemon HTTP URL directly. The current shape
invokes the prod daemon venv's binaries directly (Phase 2.5 migration —
agents no longer launch via `uv run --project`):

```json
{
  "mcpServers": {
    "agent-core": {
      "type": "stdio",
      "command": "C:\\Users\\<you>\\.agent-core\\.venv\\Scripts\\python.exe",
      "args": ["-m", "agent_core_busproxy", "--agent", "<AGENT>",
               "--daemon-url", "http://127.0.0.1:8789"]
    },
    "agent-core-channel": {
      "type": "stdio",
      "command": "C:\\Users\\<you>\\.agent-core\\.venv\\Scripts\\python.exe",
      "args": ["-m", "agent_core_channel", "--agent", "<AGENT>",
               "--daemon-url", "http://127.0.0.1:8789"]
    }
  }
}
```

Both surfaces are stdio and reconnect independently. The old
`{"type":"http","url":".../mcp/<agent>"}` form is the #91 failure mode —
do not use it.

## Auto-start at boot (Windows) — Windows Service

The prod daemon registers as a **true Windows Service** (`AgentCoreProdDaemon`):
headless (no console window), runs as your user account (vault + keyring
access), and restarts immediately on failure with **no count limit**.

### Setup

```bash
# Install the prod daemon first (service needs the venv exe to exist).
agent-core daemon install

# Register the service. You will be prompted for your Windows password
# (stored in LSA by the SCM; never written to disk by this command).
agent-core daemon install-service

# Optional: remove the service.
agent-core daemon uninstall-service
```

`install-service` registers `AgentCoreProdDaemon` as an auto-start service
that runs `python -m agent_core.daemon.windows_service` from the prod venv.
On service start, the daemon spawns `bus run` as a hidden subprocess
(`CREATE_NO_WINDOW`). When the daemon exits (crash, watchdog self-terminate,
etc.), the service exits and the SCM restarts it immediately — indefinitely.

### Manual verification checklist

After `install-service` and a reboot (or after manually starting the
service via `sc start AgentCoreProdDaemon` / Services snap-in):

- [ ] `sc query AgentCoreProdDaemon` shows `STATE: RUNNING` (SCM-native confirmation that the service is active).
- [ ] Task Manager → Details tab: no `agent-core.exe` console window visible
      under the user's session processes.
- [ ] Services snap-in: `AgentCoreProdDaemon` shows `Status: Running`,
      `Startup type: Automatic`, `Log On As: <your account>`.
- [ ] Failure Actions (right-click → Properties → Recovery): all three
      actions are **Restart the Service**, delay 0 minutes.
- [ ] Kill the bus process manually (`taskkill /F /IM python.exe`) → wait
      5 s → `agent-core daemon status` shows running again (SCM restarted it).

### Migration from the old scheduled task

If you previously ran `install-autostart`, migrate as follows:

```bash
agent-core daemon uninstall-autostart   # remove the old Task Scheduler task
agent-core daemon install-service        # register the Windows Service
```

The old `install-autostart`/`uninstall-autostart` commands remain available
for rollback but are superseded.

### Why not Task Scheduler?

The `install-autostart` approach used `LogonType: InteractiveToken`, which
attaches the task to the interactive user session. When the session ends
(or a console window appears), the task — and the daemon — die. The
Windows Service runs in Session 0 (no interactive session, no console window)
and is independent of login/logout cycles. The scheduled task's
`RestartOnFailure` count was also bounded (max 3), which the B-1 watchdog
self-terminate exhausted; the SCM's failure-action list repeats the last
action indefinitely.
