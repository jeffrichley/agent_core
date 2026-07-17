agent-core is a durable message-bus runtime for AI agent beings. This guide takes you from a fresh machine to a running being with a verified round-trip message — no repository clone needed. All five stages use the PyPI packages and should take roughly 10 minutes.

## Prerequisites

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/) installed

## Step 1 — Install

```bash
uv add agent-core agent-core-hatchery
```

`agent-core` provides the daemon CLI; `agent-core-hatchery` provides the `hatch-being` wizard. No other package is required for the quickstart. The following endpoint packages are optional extras:

| Package | What it adds |
|---|---|
| `agent-core-discord` | Discord bot adapter |
| `agent-core-inbound` | GitHub/webhook inbound events |
| `agent-core-briefs` | Scheduled morning briefs |
| `agent-core-voice` | Voice synthesis endpoint (GPU; see package README for extra `--index` flags) |

## Step 2 — Hatch a being

```bash
hatch-being
```

The interactive wizard collects a being name (lowercase, ASCII, no spaces — e.g. `myagent`), a vault root (default: your home directory), and optional channel selections (Discord, webcam, etc.). After completing the wizard, your being's vault is at `~/.<your-being>/Memory/` and the daemon has received a config fragment at `~/.agent-core/endpoints.d/<your-being>.yaml`.

!!! note
    The daemon does not need to be running during hatching. The wizard will report the daemon as unreachable — that is expected here; you will start it in Step 3.

## Step 3 — Start the daemon

```bash
agent-core daemon init
```

Scaffold `~/.agent-core/agent_core.yaml` (run once per machine).

```bash
agent-core daemon install
```

Install the daemon runtime into `~/.agent-core/.venv/`.

```bash
agent-core daemon start
```

Start the daemon.

Verify the daemon is running:

```bash
agent-core daemon status
```

Expected output:

```
prod daemon is running (PID: NNNNN)
running from: ~/.agent-core/.venv/bin/python
installed at: <ISO timestamp>
installed sha: <git sha>
installed version: <X.Y.Z>
...
--- last 20 lines of daemon.log ---
<log output>
```

*(The `installed at:`, `installed sha:`, and `installed version:` fields appear for a release-installed daemon, followed by the last 20 lines of `daemon.log`. The exact log content varies; its presence confirms the daemon started successfully.)*

## Step 4 — Connect your Claude Code session

Your being connects to the bus through two stdio MCP servers bundled in the daemon venv. Place `.mcp.json` at `~/.<your-being>/.mcp.json` (or at the Claude Code project root if your being is tied to a project).

=== "Windows"
    ```json
    {
      "mcpServers": {
        "agent-core": {
          "type": "stdio",
          "command": "C:\\Users\\<you>\\.agent-core\\.venv\\Scripts\\python.exe",
          "args": ["-m", "agent_core_busproxy", "--agent", "<your-being>",
                   "--daemon-url", "http://127.0.0.1:8789"]
        },
        "agent-core-channel": {
          "type": "stdio",
          "command": "C:\\Users\\<you>\\.agent-core\\.venv\\Scripts\\python.exe",
          "args": ["-m", "agent_core_channel", "--agent", "<your-being>",
                   "--daemon-url", "http://127.0.0.1:8789"]
        }
      }
    }
    ```

=== "POSIX (macOS / Linux)"
    ```json
    {
      "mcpServers": {
        "agent-core": {
          "type": "stdio",
          "command": "/home/<you>/.agent-core/.venv/bin/python",
          "args": ["-m", "agent_core_busproxy", "--agent", "<your-being>",
                   "--daemon-url", "http://127.0.0.1:8789"]
        },
        "agent-core-channel": {
          "type": "stdio",
          "command": "/home/<you>/.agent-core/.venv/bin/python",
          "args": ["-m", "agent_core_channel", "--agent", "<your-being>",
                   "--daemon-url", "http://127.0.0.1:8789"]
        }
      }
    }
    ```

> **macOS users:** replace `/home/<you>` with `/Users/<you>` in both command paths above.

!!! warning
    Do not use `"type": "http"` — that form strands live Claude Code sessions on daemon restart. Always use the stdio form above.

Then start Claude Code with the channel relay enabled:

```bash
claude --dangerously-load-development-channels server:agent-core-channel
```

The `--dangerously-load-development-channels` flag is required for the `agent-core-channel` wake relay. Start Claude Code with this flag each session.

## Step 5 — Verify the round trip

Once Claude Code connects, confirm the following in sequence:

1. Open Claude Code's MCP tool panel — confirm `agent-core` tools appear (`bus_send`, `list_pending`, `handle`, `list_endpoints`, …).
2. Send a message to yourself: use `bus_send` with `to="<your-being>"`, `kind="TextMessage"`, `payload={"text": "hello"}`.
3. List pending messages: use `list_pending` — your message should appear.
4. Acknowledge the message: use `handle` with the envelope ID from the previous step.

A successful round trip confirms that publish → SQLite persist → deliver → ack is working end-to-end.

## Next steps

| Page | What it covers |
|---|---|
| [Your first endpoint](first-agent.md) | The `Endpoint` protocol; writing and running a custom endpoint |
| [Running the daemon](daemon.md) | Prod/source/test instances, upgrades, troubleshooting |
| [Concepts](../concepts/index.md) | Bus, envelopes, endpoints, the full runtime model |
