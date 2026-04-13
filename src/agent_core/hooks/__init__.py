"""agent_core.hooks — Pluggable hook tool system for Claude Code lifecycle events.

This package provides the framework for running registered Python tools at
Claude Code lifecycle events (SessionStart, PreToolUse, PostToolUse, etc.).
Tools implement the HookTool protocol and are declared in agent_core.yaml.

Modules:
    protocol: HookTool protocol that all tools must implement.
    pipeline: Pipeline class that loads config, runs tools, and renders output.
    tools: Built-in hook tools shipped with agent_core.
"""
