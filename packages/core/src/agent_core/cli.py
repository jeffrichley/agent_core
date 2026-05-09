"""agent-core CLI — command-line interface for the agent_core framework.

Commands:
    agent-core hooks run <event>    Execute all tools registered for a hook event.
    agent-core notify <title> <msg> Send a desktop toast notification.
    agent-core vault plan-dry-run …  Dry-run vault + YAML path audit (cutover #06).

Plugin-contributed subapps (e.g. ``agent-core briefs``) are wired in via the
``register_cli_subapps`` pluggy hookspec — see
:mod:`agent_core.plugins.specs` and :mod:`agent_core.plugins.manager`.
That keeps satellite packages off ``agent_core``'s import graph; this
module no longer imports plugin CLIs directly.

See Also:
    agent_core.hooks.pipeline.Pipeline: The engine that runs the tools.
"""

import json
import sys
from pathlib import Path

import typer

from agent_core.bus.cli import app as bus_app
from agent_core.bus_log.cli import bus_log_app
from agent_core.daemon.cli import app as daemon_app
from agent_core.email.cli import email_app
from agent_core.hooks.pipeline import Pipeline
from agent_core.plugins.manager import apply_cli_subapps, create_plugin_manager
from agent_core.vault_migration_plan import vault_app

app = typer.Typer(
    name="agent-core",
    help="Core infrastructure for AI agents — hooks, memory, and tooling.",
    no_args_is_help=True,
)

hooks_app = typer.Typer(
    name="hooks",
    help="Hook tool pipeline — run registered tools for Claude Code lifecycle events.",
    no_args_is_help=True,
)
app.add_typer(hooks_app, name="hooks")
app.add_typer(email_app, name="email")
app.add_typer(bus_app, name="bus")
app.add_typer(bus_log_app, name="bus-log")
app.add_typer(daemon_app, name="daemon")
app.add_typer(vault_app, name="vault")

_HOOK_CONFIG_OPTION = typer.Option(
    "agent_core.yaml",
    help="Path to pipeline config file.",
    exists=False,
)


@hooks_app.command("run")
def run_hook(
    event: str = typer.Argument(help="The lifecycle event name (e.g., SessionStart, PreToolUse)."),
    config: Path = _HOOK_CONFIG_OPTION,
) -> None:
    """Execute all tools registered for a hook event."""
    try:
        pipeline = Pipeline(config)
    except FileNotFoundError as exc:
        typer.echo(f"Error: Config file not found: {config}", err=True)
        raise typer.Exit(code=1) from exc

    raw_input = sys.stdin.read().strip()
    if raw_input:
        try:
            hook_input = json.loads(raw_input)
        except json.JSONDecodeError:
            hook_input = {}
    else:
        hook_input = {}

    results = pipeline.run(event, hook_input)
    markdown = pipeline.render(results)

    output = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": markdown,
        }
    }
    typer.echo(json.dumps(output))


@app.command("notify")
def notify(
    title: str = typer.Argument(help="Notification title (e.g., agent name)."),
    message: str = typer.Argument(help="Notification message body."),
) -> None:
    """Send a desktop toast notification. Cross-platform (Windows, macOS, Linux)."""
    import asyncio

    from desktop_notifier import DesktopNotifier

    notifier = DesktopNotifier()
    asyncio.run(notifier.send(title=title, message=message))
    typer.echo(f"Notification sent: {title} — {message}")


_DEFAULT_WAKE_AUDIT_DIR = Path.home() / ".agent-core" / "wake_audit"
_DEFAULT_MCP_AUDIT_DIR = Path.home() / ".agent-core" / "mcp_audit"
_WAKE_AUDIT_DIR_OPTION = typer.Option(
    _DEFAULT_WAKE_AUDIT_DIR,
    "--wake-audit-dir",
    help="Directory holding per-agent wake-audit JSONL files.",
)
_MCP_AUDIT_DIR_OPTION = typer.Option(
    _DEFAULT_MCP_AUDIT_DIR,
    "--mcp-audit-dir",
    help="Directory holding per-agent mcp-audit JSONL files.",
)
_WINDOW_SECONDS_OPTION = typer.Option(
    300,
    "--window-seconds",
    help="Time window after each wake to look for the next agent call.",
)


@app.command("wake-stats")
def wake_stats(
    agent: str = typer.Argument(..., help="Agent name (e.g., 'pepper')."),
    window_seconds: int = _WINDOW_SECONDS_OPTION,
    wake_audit_dir: Path = _WAKE_AUDIT_DIR_OPTION,
    mcp_audit_dir: Path = _MCP_AUDIT_DIR_OPTION,
) -> None:
    """Classify each wake's outcome and print rolling rates (issue #70)."""
    from agent_core.wake_stats import classify_wakes, summarize

    wake_path = wake_audit_dir / f"{agent}.jsonl"
    audit_path = mcp_audit_dir / f"{agent}.jsonl"
    outcomes = classify_wakes(
        wake_audit_path=wake_path,
        mcp_audit_path=audit_path,
        window_seconds=window_seconds,
    )
    summary = summarize(outcomes)
    typer.echo(json.dumps(summary, indent=2))


# Mount plugin-contributed Typer subapps (e.g. agent-core-briefs ships
# the ``briefs`` subapp via this hook). Done at module load so
# ``agent-core --help`` lists them; satellite packages register their
# subapp inside the hookimpl so the import only happens when their
# entry-point is loaded.
apply_cli_subapps(create_plugin_manager(), app)


if __name__ == "__main__":
    app()
