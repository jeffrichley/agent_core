"""TimeInjector — injects the current date and time into session context.

This is the reference implementation of the HookTool protocol. It solves
a common problem: Claude Code agents often don't know what time it is,
leading to incorrect date references in conversations.

The output format is configurable via the 'format' param in agent_core.yaml.
The default format produces a human-readable string like:
    "Monday, April 13, 2026 10:45 AM CDT"

Configuration:
    In agent_core.yaml, register under any lifecycle event:

        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.time_injector.TimeInjector
              params:
                format: "%A, %B %d, %Y %I:%M %p %Z"

    Supported params:
        format (str): Python strftime format string.
            Default: "%A, %B %d, %Y %I:%M %p %Z"

Example output:
    ToolResult(
        heading="Current Time",
        content="Monday, April 13, 2026 10:45 AM CDT"
    )
"""

from datetime import datetime, timezone

from agent_core.models import ToolResult


class TimeInjector:
    """Injects the current date and time into the session context."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        fmt = params.get("format", "%A, %B %d, %Y %I:%M %p %Z")
        now = datetime.now(timezone.utc).astimezone()
        return ToolResult(
            heading="Current Time",
            content=now.strftime(fmt),
        )
