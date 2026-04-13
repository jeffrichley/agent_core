"""agent-core CLI — command-line interface for the agent_core framework.

This module provides the Typer-based CLI that serves as the entrypoint for
Claude Code hooks and other agent_core operations.

Commands:
    agent-core hooks run <event>    Execute all tools registered for a hook event.
                                    Reads hook_input from stdin (JSON from Claude Code),
                                    runs the pipeline, outputs JSON to stdout.

Usage from Claude Code hooks (.claude/settings.json):
    {
        "hooks": {
            "SessionStart": [{
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": "uv run agent-core hooks run SessionStart",
                    "timeout": 15
                }]
            }]
        }
    }

Usage from the command line:
    echo '{"session_id": "abc"}' | agent-core hooks run SessionStart
    agent-core hooks run SessionStart --config /path/to/agent_core.yaml

See Also:
    agent_core.hooks.pipeline.Pipeline: The engine that runs the tools.
"""

import json
import sys
from pathlib import Path

import typer

from agent_core.hooks.pipeline import Pipeline

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


@hooks_app.command("run")
def run_hook(
    event: str = typer.Argument(help="The lifecycle event name (e.g., SessionStart, PreToolUse)."),
    config: Path = typer.Option(
        "agent_core.yaml",
        help="Path to pipeline config file.",
        exists=False,
    ),
) -> None:
    """Execute all tools registered for a hook event."""
    try:
        pipeline = Pipeline(config)
    except FileNotFoundError:
        typer.echo(f"Error: Config file not found: {config}", err=True)
        raise typer.Exit(code=1)

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
