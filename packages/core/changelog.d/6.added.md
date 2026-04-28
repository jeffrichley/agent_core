- `ClaudeCodeMCPEndpoint` adapter so Claude Code instances can connect
  to the bus over Streamable HTTP. Path-based identity at `/mcp/<name>`.
- Shared HTTP host (Starlette + Uvicorn) wired into the bus runner;
  mounts every registered `MCPHostable` endpoint automatically.
- `agent-core daemon start/stop/status` — PID-managed lifecycle for the
  long-running bus daemon. Spawns `agent-core bus run` detached.
