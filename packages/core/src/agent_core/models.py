"""Pydantic models for the agent_core hook pipeline system.

This module defines the data models that form the contract between hook tools,
the pipeline runner, and the CLI configuration layer. There are three models:

- **ToolResult**: The standardized output returned by every hook tool after
  execution. Each result carries a heading (used for display/logging) and
  content (the actual payload injected into the hook response).

- **ToolConfig**: A single entry in a pipeline configuration, identifying
  which tool class to load and what parameters to pass to it. The ``tool``
  field is a fully qualified Python class path that the pipeline runner
  resolves at runtime via importlib.

- **PipelineConfig**: The root configuration object that maps Claude Code
  hook event names (e.g. ``"SessionStart"``, ``"PreToolUse"``) to ordered
  lists of ``ToolConfig`` entries. This is what gets deserialized from the
  user's YAML/JSON config file.

Example usage::

    from agent_core.models import ToolResult, ToolConfig, PipelineConfig

    # A tool returns a ToolResult
    result = ToolResult(heading="Current Time", content="2026-04-13 09:00 EDT")

    # Configure a pipeline from data
    config = PipelineConfig(pipelines={
        "SessionStart": [
            ToolConfig(tool="agent_core.hooks.tools.time_injector.TimeInjector"),
            ToolConfig(
                tool="agent_core.hooks.tools.kb_injector.KBInjector",
                params={"index_path": "knowledge/index.md"},
            ),
        ],
    })
"""

from pydantic import BaseModel


class ToolResult(BaseModel):
    """The output of a single hook tool execution.

    Every hook tool must return a ``ToolResult`` when it runs. The pipeline
    runner collects these results and assembles them into the final hook
    response that Claude Code receives.

    Attributes:
        heading: A short human-readable label for this result, used as a
            section header in the assembled hook output. For example,
            ``"Current Time"`` or ``"Knowledge Base Index"``.
        content: The actual payload string to inject into the hook response.
            This is the substantive output of the tool -- it could be a
            timestamp, a file's contents, a formatted summary, etc.

    Example::

        result = ToolResult(
            heading="Current Time",
            content="2026-04-13 09:00 EDT",
        )
    """

    heading: str
    content: str


class ToolConfig(BaseModel):
    """Configuration for a single tool in a hook pipeline.

    Each ``ToolConfig`` tells the pipeline runner which tool class to
    instantiate and what parameters to pass to it. The ``tool`` field is
    a fully qualified Python class path (e.g.
    ``"agent_core.hooks.tools.time_injector.TimeInjector"``) that the
    runner resolves dynamically via ``importlib``.

    Attributes:
        tool: Fully qualified Python class path to the hook tool
            implementation. The referenced class must conform to the
            ``HookTool`` protocol (see ``agent_core.protocol``).
        params: Optional dictionary of keyword arguments passed to the
            tool's constructor. Defaults to an empty dict. These let
            users customize tool behavior without writing code -- for
            example, ``{"format": "%Y-%m-%d"}`` for a time injector.

    Example::

        config = ToolConfig(
            tool="agent_core.hooks.tools.time_injector.TimeInjector",
            params={"format": "%Y-%m-%d"},
        )
    """

    tool: str
    params: dict = {}


class PipelineConfig(BaseModel):
    """Root configuration mapping hook event names to tool pipelines.

    This is the top-level model deserialized from the user's configuration
    file (YAML or JSON). It maps each Claude Code hook event name to an
    ordered list of ``ToolConfig`` entries. The pipeline runner iterates
    through the list for the triggered event, instantiates each tool, and
    collects the results.

    Supported event names follow the Claude Code hook system:
    ``"SessionStart"``, ``"SessionEnd"``, ``"PreToolUse"``,
    ``"PostToolUse"``, ``"PreCompact"``, etc.

    Attributes:
        pipelines: A dictionary where keys are hook event names and values
            are ordered lists of ``ToolConfig`` objects. An empty dict is
            valid and means no tools are configured for any event.

    Example::

        config = PipelineConfig(pipelines={
            "SessionStart": [
                ToolConfig(tool="agent_core.hooks.tools.time_injector.TimeInjector"),
                ToolConfig(tool="agent_core.hooks.tools.kb_injector.KBInjector"),
            ],
            "PreToolUse": [
                ToolConfig(tool="agent_core.hooks.tools.guard.GuardTool"),
            ],
        })
    """

    pipelines: dict[str, list[ToolConfig]]
