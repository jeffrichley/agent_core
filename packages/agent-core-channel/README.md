# agent-core-channel

`agent-core-channel` is a stdio MCP channel relay for Claude Code. It connects
to the agent_core daemon's `/notify/<agent>` SSE stream and re-emits each event
as `notifications/claude/channel` so Claude Code can wake autonomously when
mail arrives on the bus.

Claude Code still uses the daemon's HTTP MCP endpoint for tools such as
`send`, `list_pending`, and `handle`. This package only relays notifications.

## Claude Code Configuration

On Windows in this workspace, use the workspace venv executable for now:

```json
{
  "mcpServers": {
    "agent-core": {
      "type": "http",
      "url": "http://localhost:8788/mcp/agent-testbot"
    },
    "agent-core-channel": {
      "command": "E:\\workspaces\\ai\\agents\\agent_core\\.venv\\Scripts\\agent-core-channel.exe",
      "args": ["--agent", "agent-testbot"]
    }
  }
}
```

Launch Claude Code with the development channel enabled:

```powershell
claude --dangerously-load-development-channels server:agent-core-channel
```

## Windows Development Caveat

As of the 2026-04-30 live validation, `uv tool install --from
packages/agent-core-channel agent-core-channel` is not reliable on Windows in
this workspace. The installed tool environment includes `pywin32` wheels, but
the post-install step that stages `_win32sysloader.pyd` does not run, and the
MCP SDK's Windows utility import can fail at startup.

For non-Windows installs, or once the Windows packaging issue is fixed, a
PATH-resolved binary is the intended shape:

```json
{
  "mcpServers": {
    "agent-core-channel": {
      "command": "agent-core-channel",
      "args": ["--agent", "agent-testbot"]
    }
  }
}
```

Do not switch the testbot config back to a PATH-resolved `uv tool install`
binary until the `pywin32` packaging issue is fixed or the install path moves
to a wrapper that runs the required post-install setup.
