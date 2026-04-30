"""Typer CLI entry point for agent-core-channel.

This is a tiny shim that parses --agent and --daemon-url, then hands off to
run_relay(). The real relay logic lives in agent_core_channel.stdio_server.
"""

from __future__ import annotations

import anyio
import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    agent: str = typer.Option(..., "--agent", help="Agent name on the bus."),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:8788",
        "--daemon-url",
        help="agent-core daemon URL (default: http://127.0.0.1:8788).",
    ),
) -> None:
    """Run the agent-core stdio channel relay."""
    from agent_core_channel.stdio_server import run_relay

    anyio.run(run_relay, agent, daemon_url)


if __name__ == "__main__":
    app()
