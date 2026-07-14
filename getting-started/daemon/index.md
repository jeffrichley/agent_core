# Running the Daemon

The agent-core daemon is a supervised process that runs every endpoint defined in `agent_core.yaml`: the bus, Claude Code MCP adapters, Discord connectors, the scheduler, brief framework, and any plugin-contributed types. It manages endpoint lifecycle, delivery sweeps, and the HTTP surface the bus tools connect to.

Authoritative reference

This page is the adopter-facing getting-started guide. The full operator reference — troubleshooting tables, instance internals, Windows autostart — lives at [`docs/setup/daemon.md`](https://github.com/jeffrichley/agent_core/blob/main/docs/setup/daemon.md).

## Why a separate venv

The daemon runs from its **own** venv at `~/.agent-core/.venv/`, not from the workspace `.venv/`. This is intentional: `uv sync` activity in the workspace (adding members, resolving the lockfile) rewrites editable `.pth` files and can disrupt a running process that holds open files in `.venv/`. Isolating the daemon venv means workspace iteration never drops a live agent session.

## Instances

The daemon supports three named instances, each with an independent home directory, port, and venv:

| Instance         | Home                    | Port | Code source                  |
| ---------------- | ----------------------- | ---- | ---------------------------- |
| `prod` (default) | `~/.agent-core/`        | 8789 | Release artifacts            |
| `source`         | `~/.agent-core-source/` | 8788 | Workspace `.venv` (editable) |
| `test`           | `~/.agent-core-test/`   | 8787 | Release artifacts            |

With no `--instance` flag and no `AGENT_CORE_INSTANCE` env var, all commands target `prod`. The `source` and `test` instances are additive — they cannot touch prod's venv, port, or state.

## Prod: one-time setup

Prod installs from GitHub Release artifacts, not from the workspace source.

```
# Scaffold the prod config if this machine has never run the daemon.
agent-core daemon init

# Install the latest release into ~/.agent-core/.venv/.
agent-core daemon install

# Start the daemon.
agent-core daemon start

# Verify.
agent-core daemon status
```

A healthy `daemon status` looks like:

```
prod daemon is running (PID: NNNNN)
running from: ~/.agent-core/.venv/Scripts/python.exe
installed at: <timestamp>
installed sha: <git short hash>
installed version: <X.Y.Z>
```

## Prod: picking up a new release

```
# Install and restart with the latest release.
agent-core daemon refresh

# Pin a specific version (useful for rollbacks).
agent-core daemon refresh --release v0.2.0
```

`refresh` runs stop → install → start in order. See [`docs/setup/releases.md`](https://github.com/jeffrichley/agent_core/blob/main/docs/setup/releases.md) for the release flow.

### Live agent sessions survive a bounce

Agents connect to the bus through the `agent-core` and `agent-core-channel` **stdio MCP servers**, not directly over HTTP. Each tool call opens a fresh backend connection, so a daemon restart (or crash) during an in-flight call returns a structured transient error that the agent retries automatically. No session restart needed.

Your agent's `.mcp.json` should point at the daemon venv's binaries directly:

```
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

Do not use the HTTP MCP form

An older `{"type":"http","url":".../mcp/<agent>"}` shape exists but strands live Claude Code sessions on daemon restart. Always use the stdio form above.

## Source instance: iterate on daemon code

The `source` instance runs editable from the workspace `.venv`. Use it when you're changing daemon code and want a fast restart loop without touching the live `prod` daemon.

### One-time source setup

```
# Run from inside the agent_core repo.
agent-core daemon init --instance source
agent-core daemon start --instance source
```

### The edit → restart loop

```
# Edit code in the repo, then:
agent-core daemon refresh --instance source
```

For `source`, `refresh` is a plain stop + start — no install step. The workspace `.venv` is editable, so the restart picks up your latest changes immediately.

Note

`agent-core daemon install --instance source` is intentionally an error. The source instance is never installed from artifacts.

## Test instance: validate a release before promoting to prod

Use `test` to exercise the full release packaging path (entry points, wheel contents, install correctness) before `daemon refresh` on prod.

```
# One-time scaffold.
agent-core daemon init --instance test

# Install a specific release candidate.
agent-core daemon install --instance test --release vX.Y.Z

# Start on port 8787.
agent-core daemon start --instance test

# Run your checks, then stop.
agent-core daemon stop --instance test
```

Once satisfied, promote:

```
agent-core daemon refresh --release vX.Y.Z
```

## Common problems

| Symptom                                    | Cause                                          | Fix                                                             |
| ------------------------------------------ | ---------------------------------------------- | --------------------------------------------------------------- |
| `No daemon config at ...` on `start`       | Config not scaffolded                          | `agent-core daemon init [--instance source\|test]`              |
| `daemon is currently running` on `install` | Prod daemon is up                              | Use `agent-core daemon refresh` instead                         |
| `source daemon needs the workspace .venv`  | Ran `start --instance source` outside the repo | `cd` into the agent_core repo first                             |
| `the source instance is not installed`     | Ran `install --instance source`                | Source needs no install — just `daemon start --instance source` |

## Windows: auto-start at boot

```
# Register a Task Scheduler task that starts the prod daemon at logon.
agent-core daemon install-autostart

# Remove it.
agent-core daemon uninstall-autostart
```

Requires the prod daemon to be installed first. The task runs as your own user; no admin rights needed (the daemon binds `127.0.0.1` only).

## Related

- [`docs/setup/daemon.md`](https://github.com/jeffrichley/agent_core/blob/main/docs/setup/daemon.md) — full operator reference including instance internals and troubleshooting
- [`docs/setup/releases.md`](https://github.com/jeffrichley/agent_core/blob/main/docs/setup/releases.md) — the release flow (release-please + GitHub Release artifacts)
- [`docs/setup/ci.md`](https://github.com/jeffrichley/agent_core/blob/main/docs/setup/ci.md) — CI gate and hook bootstrap
- [Concepts](https://jeffrichley.github.io/agent_core/concepts/index.md) — bus, envelopes, endpoints
