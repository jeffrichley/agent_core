"""agent_core — Core infrastructure for AI agents.

This library provides pluggable tool pipelines for Claude Code lifecycle hooks,
memory systems, and shared agent components. Agents declare their tool
configurations in YAML and agent_core handles loading, execution, and context
injection.

Modules:
    models: Shared Pydantic models used across the framework.
    hooks: Pluggable hook tool system for Claude Code lifecycle events.
    cli: Typer-based command-line interface.
"""

__version__ = "0.1.0"
