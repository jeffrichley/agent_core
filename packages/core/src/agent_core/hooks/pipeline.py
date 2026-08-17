"""Pipeline — the core engine of the hook tool system.

The Pipeline class is the bridge between Claude Code hooks and registered tools.
It reads agent_core.yaml, validates the config with Pydantic, dynamically imports
tool classes, and executes them in declared order.

Lifecycle:
    1. Pipeline(config_path) — load and validate YAML config
    2. pipeline.run(event, hook_input) — execute tools for an event
    3. pipeline.render(results) — compile tool results into markdown

The pipeline is designed to be resilient: if a single tool fails, the error
is logged and the remaining tools still run. This prevents one broken tool
from taking down the entire context injection.

Example:
    >>> from pathlib import Path
    >>> pipeline = Pipeline(Path("agent_core.yaml"))
    >>> results = pipeline.run("SessionStart", {"session_id": "abc123"})
    >>> markdown = pipeline.render(results)
    >>> print(markdown)
    ## Current Time
    ...

See Also:
    agent_core.hooks.protocol.HookTool: The protocol tools must implement.
    agent_core.models.PipelineConfig: The config model this class validates against.
"""

import logging
from pathlib import Path

import yaml
from rich.console import Console
from rich.logging import RichHandler

from agent_core.hooks.protocol import HookTool
from agent_core.models import PipelineConfig, ToolResult
from agent_core.plugins.manager import create_plugin_manager, get_hook_tool_types

# Diagnostics go to STDERR, never stdout.
#
# Claude Code consumes a hook's STDOUT as the payload — the JSON carrying
# ``additionalContext``, which is how a being's identity files reach the model.
# A default ``RichHandler()`` builds a ``Console()`` that writes to stdout, so
# every INFO line above shared a channel with that payload and was delivered
# ahead of it.
#
# Measured 2026-08-17: ~1,800 bytes of registration and per-tool logging sat in
# front of the identity payload on every SessionStart. Against the harness's
# 10,000-byte cap that is 18% of the delivery budget spent announcing that
# delivery was about to happen — and the being whose SOUL.md sat behind it could
# not tell from inside whether the file had arrived or been truncated away.
#
# The logging is worth keeping: it answers "did my config parse and find my
# tools?" for whoever is editing hook config. It is simply not payload, so it
# belongs on stderr, which Claude Code ignores.
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("agent_core.hooks.pipeline")


class Pipeline:
    """Loads tool configuration, instantiates tools, and runs them for lifecycle events."""

    def __init__(self, config_path: Path) -> None:
        """Load and validate pipeline configuration from YAML."""
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.config = PipelineConfig(**raw)
        self.config_path = config_path
        self._plugin_manager = create_plugin_manager()
        self._hook_tool_types = get_hook_tool_types(self._plugin_manager)

        for event, tools in self.config.pipelines.items():
            tool_names = [t.type for t in tools]
            logger.info(
                "Event '%s': %d tool(s) registered — %s", event, len(tools), ", ".join(tool_names)
            )

    def _import_tool_class(self, tool_type: str) -> type | None:
        """Resolve a hook tool class from a registered tool type id."""
        resolved = self._hook_tool_types.get(tool_type)
        if resolved is not None:
            cls = resolved
        else:
            logger.error("Failed to import tool '%s': unknown hook tool type", tool_type)
            return None

        if not isinstance(cls, type) or not issubclass(cls, HookTool):
            instance = cls()
            if not isinstance(instance, HookTool):
                logger.error("Tool '%s' does not implement HookTool protocol", tool_type)
                return None

        return cls

    def run(self, event: str, hook_input: dict) -> list[ToolResult]:
        """Execute all tools registered for an event, in declared order."""
        tool_configs = self.config.pipelines.get(event, [])
        if not tool_configs:
            logger.info("No tools registered for event '%s'", event)
            return []

        results: list[ToolResult] = []

        for tool_config in tool_configs:
            cls = self._import_tool_class(tool_config.type)
            if cls is None:
                continue

            try:
                instance = cls()
                result = instance.execute(
                    event=event, hook_input=hook_input, params=tool_config.params
                )
                results.append(result)
                logger.info("Tool '%s' executed successfully", tool_config.type)
            except Exception as e:
                logger.error("Tool '%s' failed during execution: %s", tool_config.type, e)

        return results

    def render(self, results: list[ToolResult]) -> str:
        """Compile tool results into a single markdown document."""
        if not results:
            return ""

        sections = [f"## {r.heading}\n\n{r.content}" for r in results]
        return "\n\n---\n\n".join(sections)
