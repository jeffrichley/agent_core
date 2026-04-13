"""HookTool protocol — the interface all hook tools must implement.

This module defines the Protocol that every hook tool class must satisfy.
Tools don't need to inherit from anything or register themselves — they just
need to have an execute() method with the correct signature. Python's structural
subtyping (Protocol) handles the rest.

The protocol is intentionally minimal: one method, three arguments, one return type.
Tools are simple classes with no required state, no lifecycle methods, and no
framework coupling beyond returning a ToolResult.

Example:
    A minimal tool that satisfies the protocol::

        from agent_core.models import ToolResult

        class MyTool:
            def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
                return ToolResult(heading="My Tool", content="Hello from MyTool")

    The pipeline checks protocol compliance at load time using runtime_checkable,
    so a tool with a wrong signature fails fast with a clear error.

See Also:
    agent_core.models.ToolResult: The return type for execute().
    agent_core.hooks.pipeline.Pipeline: The runner that calls execute().
    agent_core.hooks.tools.time_injector.TimeInjector: Reference implementation.
"""

from typing import Protocol, runtime_checkable

from agent_core.models import ToolResult


@runtime_checkable
class HookTool(Protocol):
    """Protocol that all hook tools must implement.

    Hook tools are registered in agent_core.yaml and executed by the pipeline
    when their associated lifecycle event fires. Each tool returns a heading
    and content that get compiled into the markdown context document.

    The protocol uses @runtime_checkable so the pipeline can verify at load time
    that a class satisfies the interface, rather than failing at execution time
    with an opaque AttributeError.

    Methods:
        execute: Run the tool for a given event and return a ToolResult.
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Execute the tool and return a result to inject into context.

        This method is called by the pipeline once per registered event firing.
        Each invocation gets a fresh tool instance — tools should not rely on
        state persisting between calls.

        Args:
            event: The lifecycle event name that triggered this execution.
                One of: SessionStart, PreToolUse, PostToolUse, PostToolUseFailure,
                SubagentStart, UserPromptSubmit.
            hook_input: Data from Claude Code, passed as a dict. Always includes:
                - session_id (str): Unique session identifier
                - transcript_path (str): Path to conversation transcript
                - cwd (str): Current working directory
                - permission_mode (str): Current permission mode
                Event-specific fields vary:
                - tool_name (str): PreToolUse, PostToolUse, PostToolUseFailure
                - tool_input (dict): PreToolUse, PostToolUse
                - agent_type (str): SubagentStart
                - prompt (str): UserPromptSubmit
            params: Tool-specific configuration from agent_core.yaml.
                Defined per tool registration. Empty dict if no params declared.

        Returns:
            ToolResult with heading (rendered as ## markdown heading) and content
            (rendered as the section body below the heading).
        """
        ...
