"""Typer CLI entry point for agent-core-channel.

Parses --agent, --daemon-url, and the inline-content config flags
(issue #70), resolves layered RelayConfig (CLI > env > YAML > defaults),
then hands off to run_relay().
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import anyio
import typer

app = typer.Typer(add_completion=False)

_DEFAULT_CONFIG_PATH = Path.home() / ".agent-core" / "agent_core.yaml"
_CONFIG_PATH_OPTION = typer.Option(
    _DEFAULT_CONFIG_PATH,
    "--config-path",
    help="Path to agent_core.yaml (for the channel_relay block).",
)
_INLINE_MODE_OPTION = typer.Option(
    None,
    "--inline-mode",
    help="Override channel_relay.inline_mode (full|preview|disabled).",
)
_INLINE_MAX_ENVELOPES_OPTION = typer.Option(
    None,
    "--inline-max-envelopes",
    help="Override channel_relay.max_envelopes.",
)
_INLINE_MAX_BYTES_OPTION = typer.Option(
    None,
    "--inline-max-bytes",
    help="Override channel_relay.max_bytes.",
)
_INLINE_PER_ENVELOPE_BYTES_OPTION = typer.Option(
    None,
    "--inline-per-envelope-bytes",
    help="Override channel_relay.per_envelope_bytes.",
)


@app.command()
def main(
    agent: str = typer.Option(..., "--agent", help="Agent name on the bus."),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:8788",
        "--daemon-url",
        help="agent-core daemon URL (default: http://127.0.0.1:8788).",
    ),
    config_path: Path = _CONFIG_PATH_OPTION,
    inline_mode: str | None = _INLINE_MODE_OPTION,
    inline_max_envelopes: int | None = _INLINE_MAX_ENVELOPES_OPTION,
    inline_max_bytes: int | None = _INLINE_MAX_BYTES_OPTION,
    inline_per_envelope_bytes: int | None = _INLINE_PER_ENVELOPE_BYTES_OPTION,
) -> None:
    """Run the agent-core stdio channel relay."""
    from agent_core.plugins.manager import create_plugin_manager, get_envelope_renderers
    from agent_core_channel.config import load_config
    from agent_core_channel.rendering import set_plugin_renderers
    from agent_core_channel.stdio_server import run_relay

    # Wire plugin-registered envelope renderers into the rendering dispatch
    # table. agent_core PR #124 added the capability + dispatch but deferred
    # the bootstrap wiring; without this call, plugin renderers like
    # pepper-roots' `Desire` renderer never load and dispatch falls back to
    # JSON. Caught by Pepper's behavioral round-trip 2026-05-25.
    pm = create_plugin_manager()
    set_plugin_renderers(get_envelope_renderers(pm))

    cli_args = SimpleNamespace(
        inline_mode=inline_mode,
        inline_max_envelopes=inline_max_envelopes,
        inline_max_bytes=inline_max_bytes,
        inline_per_envelope_bytes=inline_per_envelope_bytes,
    )
    config = load_config(
        agent=agent,
        config_path=config_path,
        cli_args=cli_args,
        env=os.environ,
    )

    async def _run() -> None:
        await run_relay(agent, daemon_url, config=config)

    anyio.run(_run)


if __name__ == "__main__":
    app()
