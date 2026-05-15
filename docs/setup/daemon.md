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
