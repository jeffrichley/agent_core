# Daemon setup and refresh workflow

The agent-core bus daemon supervises every endpoint (bus, Discord adapters,
scheduler, briefs, webcam, voice). It runs from its **own** venv at
`~/.agent-core/.venv/` — not from the workspace's `.venv/` — so `uv sync`
activity in the workspace cannot disrupt the running daemon process.

## Instances (Phase 3)

The daemon supports two instances, selected with `--instance` on every
`agent-core daemon` subcommand:

| | `prod` (default) | `dev` |
|---|---|---|
| Home | `~/.agent-core/` | `~/.agent-core-dev/` |
| Bus port | 8789 | 8788 |
| Code source | release artifacts (`daemon install`) | the workspace `.venv` (editable) |
| Install stamp | yes | none — dev is not installed |

With no `--instance` flag and no `AGENT_CORE_INSTANCE` env var, every
command resolves to `prod` with port 8789 — the live Pepper + Wren home.
The dev instance is purely additive: a dev `start`/`stop`/`refresh`/crash
can never touch prod's venv, port, or state.

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

## Dev instance

The dev instance lets you iterate on daemon code without bouncing the
prod daemon that Pepper and Wren depend on. It runs **editable from the
workspace `.venv`** — your source edits are live on the next restart.

### One-time dev setup

```bash
agent-core daemon init --instance dev      # scaffolds ~/.agent-core-dev/agent_core.yaml
agent-core daemon start --instance dev     # runs from the workspace .venv
```

`start --instance dev` must be run from inside the agent_core repo — it
resolves the workspace `.venv` for the daemon interpreter.

### The dev loop

Edit daemon code in the repo, then:

```bash
agent-core daemon refresh --instance dev
```

For dev, `refresh` is a plain stop + start — **no install step**. The dev
daemon runs editable from the workspace `.venv`, so the restart picks up
your latest source edits.

`agent-core daemon install --instance dev` is intentionally an error: the
dev instance is never installed.

The instance can also be set with the `AGENT_CORE_INSTANCE` env var.

## Why the daemon runs from its own venv

Before the daemon venv was isolated, it ran from
`<workspace>/.venv/Scripts/python.exe`. On Windows, `uv sync` uses
unlink-then-relink semantics that disrupt running processes holding open
files in `.venv/`. Adding a workspace member re-resolved the lockfile and
rewrote every package's editable `.pth`, silently killing the running
daemon. Pepper went offline mid-session on 2026-05-10 from exactly this.

The fix: install the prod daemon non-editable into a venv outside the
workspace tree. The dev instance accepts the editable workspace venv on
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
| `No daemon config at ...` on `start` | config not scaffolded | `agent-core daemon init [--instance dev]` |
| `daemon is currently running` on `install` | prod daemon is up | `agent-core daemon refresh` |
| `dev daemon needs the workspace .venv` | ran `start --instance dev` outside the repo | `cd` into the agent-core repo |
| `the dev instance is not installed` | ran `install --instance dev` | dev needs no install — `daemon start --instance dev` |
| `unknown instance` | bad `--instance` value | use `prod` or `dev` |

## Related

- `docs/setup/releases.md` — the release flow (release-please + GH Release artifacts).
- `docs/setup/ci.md` — the CI gate and the one-time `just install-hooks` bootstrap.
- `docs/superpowers/specs/2026-05-21-phase3-dev-prod-daemon-design.md` — the dev/prod instance design.
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
