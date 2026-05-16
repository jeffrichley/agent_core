"""Typer CLI for agent-core-busproxy.

Runs a FastMCP proxy over stdio, backed by the daemon's per-agent HTTP
endpoint. Spawned by Claude Code as a stdio MCP server; its lifetime is
the Claude Code session, decoupled from the daemon's lifetime.
"""

from __future__ import annotations

import anyio
import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    agent: str = typer.Option(..., "--agent", help="Agent name on the bus."),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:8789",
        "--daemon-url",
        help="agent-core daemon URL (default: http://127.0.0.1:8789).",
    ),
) -> None:
    """Run the agent-core stdio bus proxy."""
    from agent_core_busproxy.proxy import build_busproxy

    proxy = build_busproxy(agent=agent, daemon_url=daemon_url)

    async def _run() -> None:
        await proxy.run_async(transport="stdio")

    anyio.run(_run)


if __name__ == "__main__":
    app()
