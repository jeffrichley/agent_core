# Pluggable Hook Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pluggable tool pipeline for Claude Code lifecycle hooks, where tools are declared in YAML and implement a Python protocol to inject context into sessions.

**Architecture:** YAML config maps lifecycle events to ordered lists of Python tool classes. A Pipeline loads the config, instantiates tools, runs them, and compiles their outputs (heading + content) into a markdown document. A Typer CLI serves as the entrypoint that Claude Code hooks call.

**Tech Stack:** Python 3.12+, Pydantic (models/validation), Typer (CLI), Rich (logging), PyYAML (config), pytest (testing), Ruff (linting)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent_core/__init__.py` | Package root, version export |
| `src/agent_core/models.py` | Pydantic models: ToolResult, ToolConfig, PipelineConfig |
| `src/agent_core/hooks/__init__.py` | Hooks subpackage |
| `src/agent_core/hooks/protocol.py` | HookTool Protocol definition |
| `src/agent_core/hooks/pipeline.py` | Pipeline class: load YAML, run tools, render markdown |
| `src/agent_core/hooks/tools/__init__.py` | Built-in tools subpackage |
| `src/agent_core/hooks/tools/time_injector.py` | TimeInjector reference implementation |
| `src/agent_core/cli.py` | Typer CLI with `hooks run` command |
| `agent_core.yaml` | Local pipeline config |
| `tests/__init__.py` | Test package |
| `tests/test_models.py` | Tests for Pydantic models |
| `tests/test_pipeline.py` | Tests for Pipeline class |
| `tests/test_time_injector.py` | Tests for TimeInjector tool |
| `tests/test_cli.py` | Tests for CLI entrypoint |
| `pyproject.toml` | Updated with new deps, src layout, CLI entrypoint |

---

### Task 1: Project scaffolding and dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `src/agent_core/__init__.py`
- Create: `src/agent_core/hooks/__init__.py`
- Create: `src/agent_core/hooks/tools/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Update pyproject.toml with new dependencies, src layout, and CLI entrypoint**

```toml
[project]
name = "agent-core"
version = "0.1.0"
description = "Core infrastructure for AI agents - memory, knowledge compilation, and tooling"
requires-python = ">=3.12"
dependencies = [
    "claude-agent-sdk>=0.1.29",
    "python-dotenv>=1.0.0",
    "tzdata>=2024.1",
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
]

[project.scripts]
agent-core = "agent_core.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core"]

[tool.ruff]
line-length = 100
src = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Create package init files**

`src/agent_core/__init__.py`:
```python
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
```

`src/agent_core/hooks/__init__.py`:
```python
"""agent_core.hooks — Pluggable hook tool system for Claude Code lifecycle events.

This package provides the framework for running registered Python tools at
Claude Code lifecycle events (SessionStart, PreToolUse, PostToolUse, etc.).
Tools implement the HookTool protocol and are declared in agent_core.yaml.

Modules:
    protocol: HookTool protocol that all tools must implement.
    pipeline: Pipeline class that loads config, runs tools, and renders output.
    tools: Built-in hook tools shipped with agent_core.
"""
```

`src/agent_core/hooks/tools/__init__.py`:
```python
"""agent_core.hooks.tools — Built-in hook tools shipped with agent_core.

Each tool in this package implements the HookTool protocol and can be
registered in agent_core.yaml to run at lifecycle events.

Available tools:
    TimeInjector: Injects the current date and time into session context.
"""
```

`tests/__init__.py`:
```python
```

- [ ] **Step 3: Install dependencies**

Run: `uv sync`
Expected: All packages install successfully, including pydantic, typer, rich, pyyaml

- [ ] **Step 4: Verify the package is importable**

Run: `uv run python -c "import agent_core; print(agent_core.__version__)"`
Expected: `0.1.0`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/ tests/__init__.py
git commit -m "feat: scaffold agent_core package with dependencies and src layout"
```

---

### Task 2: Pydantic models

**Files:**
- Create: `src/agent_core/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for ToolResult**

`tests/test_models.py`:
```python
"""Tests for agent_core Pydantic models.

Validates the core data structures used throughout the framework:
ToolResult, ToolConfig, and PipelineConfig.
"""

from agent_core.models import ToolResult


class TestToolResult:
    """Tests for ToolResult — the output of a single hook tool execution."""

    def test_create_tool_result(self):
        """ToolResult stores a heading and content string."""
        result = ToolResult(heading="Test Heading", content="Test content here")
        assert result.heading == "Test Heading"
        assert result.content == "Test content here"

    def test_tool_result_requires_heading(self):
        """ToolResult must have a heading — it's not optional."""
        import pytest
        with pytest.raises(Exception):
            ToolResult(content="no heading")

    def test_tool_result_requires_content(self):
        """ToolResult must have content — it's not optional."""
        import pytest
        with pytest.raises(Exception):
            ToolResult(heading="no content")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.models'`

- [ ] **Step 3: Implement ToolResult model**

`src/agent_core/models.py`:
```python
"""Shared Pydantic models for the agent_core framework.

This module defines the core data structures used across the framework.
All models use Pydantic v2 for validation and serialization.

Models:
    ToolResult: The output of a single hook tool execution. Each tool returns
        a heading (rendered as a markdown ## heading) and content (the section
        body). The pipeline compiles these into a markdown document.

    ToolConfig: Configuration for a single tool registration in the pipeline.
        Declared in agent_core.yaml under a pipeline event. Contains the fully
        qualified class path and optional parameters.

    PipelineConfig: Root configuration model validated from agent_core.yaml.
        Maps lifecycle event names to ordered lists of ToolConfig entries.
"""

from pydantic import BaseModel


class ToolResult(BaseModel):
    """The output of a single hook tool execution.

    Each tool returns a heading and content. The pipeline compiles these
    into a markdown document with ## headings separated by --- dividers.

    Attributes:
        heading: The section heading text. Rendered as a ## markdown heading
            by the pipeline. Should be short and descriptive (e.g., "Current Time",
            "Knowledge Base Index").
        content: The section body in markdown format. Can be plain text, tables,
            lists, or any valid markdown. The pipeline renders it as-is below
            the heading.

    Example:
        >>> result = ToolResult(heading="Current Time", content="Monday, April 13, 2026")
        >>> print(result.heading)
        Current Time
    """

    heading: str
    content: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 3 passed

- [ ] **Step 5: Write failing tests for ToolConfig and PipelineConfig**

Append to `tests/test_models.py`:
```python
from agent_core.models import ToolConfig, PipelineConfig


class TestToolConfig:
    """Tests for ToolConfig — a single tool registration in the pipeline."""

    def test_create_tool_config(self):
        """ToolConfig stores a fully qualified class path and optional params."""
        config = ToolConfig(tool="agent_core.hooks.tools.time_injector.TimeInjector")
        assert config.tool == "agent_core.hooks.tools.time_injector.TimeInjector"
        assert config.params == {}

    def test_tool_config_with_params(self):
        """ToolConfig passes params through to the tool at execution time."""
        config = ToolConfig(
            tool="agent_core.hooks.tools.time_injector.TimeInjector",
            params={"format": "%Y-%m-%d"},
        )
        assert config.params == {"format": "%Y-%m-%d"}

    def test_tool_config_requires_tool(self):
        """ToolConfig must have a tool class path."""
        import pytest
        with pytest.raises(Exception):
            ToolConfig(params={"format": "%Y-%m-%d"})


class TestPipelineConfig:
    """Tests for PipelineConfig — root config validated from agent_core.yaml."""

    def test_create_pipeline_config(self):
        """PipelineConfig maps event names to lists of ToolConfig."""
        config = PipelineConfig(
            pipelines={
                "SessionStart": [
                    ToolConfig(tool="agent_core.hooks.tools.time_injector.TimeInjector"),
                ],
            }
        )
        assert "SessionStart" in config.pipelines
        assert len(config.pipelines["SessionStart"]) == 1

    def test_empty_pipelines(self):
        """PipelineConfig can have an empty pipelines dict."""
        config = PipelineConfig(pipelines={})
        assert config.pipelines == {}

    def test_multiple_events(self):
        """PipelineConfig supports multiple events, each with multiple tools."""
        tool = ToolConfig(tool="some.tool.Class")
        config = PipelineConfig(
            pipelines={
                "SessionStart": [tool, tool],
                "PreToolUse": [tool],
            }
        )
        assert len(config.pipelines["SessionStart"]) == 2
        assert len(config.pipelines["PreToolUse"]) == 1
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolConfig'`

- [ ] **Step 7: Implement ToolConfig and PipelineConfig**

Append to `src/agent_core/models.py`:
```python


class ToolConfig(BaseModel):
    """Configuration for a single tool registration in the pipeline.

    Declared in agent_core.yaml under a pipeline event. The tool field is a
    fully qualified Python class path that the pipeline imports dynamically.
    The params dict is passed through to the tool's execute() method, allowing
    the same tool class to behave differently in different pipelines.

    Attributes:
        tool: Fully qualified class path to the tool implementation.
            Example: "agent_core.hooks.tools.time_injector.TimeInjector"
        params: Tool-specific parameters passed to execute() at runtime.
            Defaults to empty dict. Each tool defines what params it accepts.

    Example YAML:
        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.time_injector.TimeInjector
              params:
                format: "%A, %B %d, %Y %I:%M %p %Z"
    """

    tool: str
    params: dict = {}


class PipelineConfig(BaseModel):
    """Root configuration model, validated from agent_core.yaml.

    Maps lifecycle event names to ordered lists of tool configurations.
    The pipeline processes tools in the order they appear in the list.

    Supported events (Claude Code hooks with additionalContext support):
        - SessionStart: Session begins or resumes
        - PreToolUse: Before a tool call executes
        - PostToolUse: After a tool call succeeds
        - PostToolUseFailure: After a tool call fails
        - SubagentStart: When a subagent is spawned
        - UserPromptSubmit: When user submits a prompt

    Attributes:
        pipelines: Dict mapping event names to ordered lists of ToolConfig.
            Events not listed here simply have no tools registered.

    Example YAML:
        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.time_injector.TimeInjector
          PreToolUse:
            - tool: agent_core.hooks.tools.time_injector.TimeInjector
    """

    pipelines: dict[str, list[ToolConfig]]
```

- [ ] **Step 8: Run all model tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: 9 passed

- [ ] **Step 9: Commit**

```bash
git add src/agent_core/models.py tests/test_models.py
git commit -m "feat: add Pydantic models — ToolResult, ToolConfig, PipelineConfig"
```

---

### Task 3: HookTool protocol

**Files:**
- Create: `src/agent_core/hooks/protocol.py`

- [ ] **Step 1: Implement the HookTool protocol**

`src/agent_core/hooks/protocol.py`:
```python
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
```

- [ ] **Step 2: Verify the protocol is importable**

Run: `uv run python -c "from agent_core.hooks.protocol import HookTool; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/agent_core/hooks/protocol.py
git commit -m "feat: add HookTool protocol definition"
```

---

### Task 4: TimeInjector — reference tool implementation

**Files:**
- Create: `src/agent_core/hooks/tools/time_injector.py`
- Create: `tests/test_time_injector.py`

- [ ] **Step 1: Write failing tests for TimeInjector**

`tests/test_time_injector.py`:
```python
"""Tests for the TimeInjector hook tool.

TimeInjector is the reference implementation of the HookTool protocol.
These tests verify both its behavior and its protocol compliance.
"""

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.time_injector import TimeInjector
from agent_core.models import ToolResult


class TestTimeInjector:
    """Tests for TimeInjector — injects current date/time into session context."""

    def test_implements_hook_tool_protocol(self):
        """TimeInjector must satisfy the HookTool protocol."""
        assert isinstance(TimeInjector(), HookTool)

    def test_returns_tool_result(self):
        """execute() must return a ToolResult instance."""
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        assert isinstance(result, ToolResult)

    def test_heading_is_current_time(self):
        """The heading should be 'Current Time'."""
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        assert result.heading == "Current Time"

    def test_content_is_nonempty(self):
        """The content should contain a formatted date/time string."""
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        assert len(result.content) > 0

    def test_custom_format_param(self):
        """The format param controls the datetime output format."""
        tool = TimeInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"format": "%Y-%m-%d"},
        )
        # Should match YYYY-MM-DD pattern
        assert len(result.content) == 10
        assert result.content[4] == "-"
        assert result.content[7] == "-"

    def test_default_format_includes_day_name(self):
        """The default format should include the day of the week."""
        tool = TimeInjector()
        result = tool.execute(event="SessionStart", hook_input={}, params={})
        # Default format starts with day name (e.g., "Monday")
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert any(result.content.startswith(day) for day in days)

    def test_works_for_any_event(self):
        """TimeInjector works the same regardless of which event triggers it."""
        tool = TimeInjector()
        result_start = tool.execute(event="SessionStart", hook_input={}, params={})
        result_pre = tool.execute(event="PreToolUse", hook_input={}, params={})
        assert result_start.heading == result_pre.heading
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_time_injector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.hooks.tools.time_injector'`

- [ ] **Step 3: Implement TimeInjector**

`src/agent_core/hooks/tools/time_injector.py`:
```python
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
            See: https://docs.python.org/3/library/datetime.html#strftime-codes

Example output:
    ToolResult(
        heading="Current Time",
        content="Monday, April 13, 2026 10:45 AM CDT"
    )

See Also:
    agent_core.hooks.protocol.HookTool: The protocol this tool implements.
"""

from datetime import datetime, timezone

from agent_core.models import ToolResult


class TimeInjector:
    """Injects the current date and time into the session context.

    Solves the common problem of agents not knowing what time it is.
    The format is configurable via the 'format' param — any Python strftime
    format string is accepted.
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Return the current date and time as a ToolResult.

        Args:
            event: The lifecycle event name (unused — time is time).
            hook_input: Data from Claude Code (unused by this tool).
            params: Optional configuration. Supported keys:
                format (str): strftime format string.
                    Default: "%A, %B %d, %Y %I:%M %p %Z"

        Returns:
            ToolResult with heading "Current Time" and the formatted
            date/time string as content.
        """
        fmt = params.get("format", "%A, %B %d, %Y %I:%M %p %Z")
        now = datetime.now(timezone.utc).astimezone()
        return ToolResult(
            heading="Current Time",
            content=now.strftime(fmt),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_time_injector.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/hooks/tools/time_injector.py tests/test_time_injector.py
git commit -m "feat: add TimeInjector — reference HookTool implementation"
```

---

### Task 5: Pipeline — load, run, render

**Files:**
- Create: `src/agent_core/hooks/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests for Pipeline loading**

`tests/test_pipeline.py`:
```python
"""Tests for the Pipeline class — the core engine of the hook tool system.

The pipeline loads YAML config, instantiates tools, runs them in order,
and compiles their outputs into a markdown document.
"""

import textwrap
from pathlib import Path

import pytest
import yaml

from agent_core.hooks.pipeline import Pipeline
from agent_core.models import PipelineConfig, ToolConfig, ToolResult


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Create a temporary agent_core.yaml with TimeInjector registered."""
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "tool": "agent_core.hooks.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y-%m-%d"},
                }
            ],
            "PreToolUse": [
                {"tool": "agent_core.hooks.tools.time_injector.TimeInjector"},
            ],
        }
    }
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


@pytest.fixture
def empty_config_file(tmp_path: Path) -> Path:
    """Create a temporary agent_core.yaml with no tools registered."""
    config = {"pipelines": {}}
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


class TestPipelineLoad:
    """Tests for Pipeline.__init__ — loading and validating YAML config."""

    def test_load_valid_config(self, config_file: Path):
        """Pipeline loads and validates a well-formed YAML config."""
        pipeline = Pipeline(config_file)
        assert "SessionStart" in pipeline.config.pipelines
        assert len(pipeline.config.pipelines["SessionStart"]) == 1

    def test_load_empty_config(self, empty_config_file: Path):
        """Pipeline handles an empty pipelines dict gracefully."""
        pipeline = Pipeline(empty_config_file)
        assert pipeline.config.pipelines == {}

    def test_load_missing_file_raises(self, tmp_path: Path):
        """Pipeline raises FileNotFoundError for missing config."""
        with pytest.raises(FileNotFoundError):
            Pipeline(tmp_path / "nonexistent.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path: Path):
        """Pipeline raises an error for malformed YAML."""
        bad_config = tmp_path / "agent_core.yaml"
        bad_config.write_text("pipelines:\n  - not: valid: yaml: [[", encoding="utf-8")
        with pytest.raises(Exception):
            Pipeline(bad_config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py::TestPipelineLoad -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.hooks.pipeline'`

- [ ] **Step 3: Implement Pipeline.__init__**

`src/agent_core/hooks/pipeline.py`:
```python
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

import importlib
import logging

from pathlib import Path

import yaml
from rich.logging import RichHandler

from agent_core.hooks.protocol import HookTool
from agent_core.models import PipelineConfig, ToolResult

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("agent_core.hooks.pipeline")


class Pipeline:
    """Loads tool configuration, instantiates tools, and runs them for lifecycle events.

    The pipeline reads agent_core.yaml, validates it against PipelineConfig,
    and provides run() and render() methods for executing the tool chain.

    Attributes:
        config: The validated PipelineConfig loaded from YAML.
        config_path: Path to the YAML config file.
    """

    def __init__(self, config_path: Path) -> None:
        """Load and validate pipeline configuration from YAML.

        Args:
            config_path: Path to the agent_core.yaml config file.

        Raises:
            FileNotFoundError: If the config file doesn't exist.
            yaml.YAMLError: If the file contains invalid YAML.
            pydantic.ValidationError: If the YAML doesn't match PipelineConfig schema.
        """
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.config = PipelineConfig(**raw)
        self.config_path = config_path

        # Log registered tools per event
        for event, tools in self.config.pipelines.items():
            tool_names = [t.tool.rsplit(".", 1)[-1] for t in tools]
            logger.info("Event '%s': %d tool(s) registered — %s", event, len(tools), ", ".join(tool_names))
```

- [ ] **Step 4: Run loading tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py::TestPipelineLoad -v`
Expected: 4 passed

- [ ] **Step 5: Write failing tests for Pipeline.run**

Append to `tests/test_pipeline.py`:
```python


class TestPipelineRun:
    """Tests for Pipeline.run — executing tools for a lifecycle event."""

    def test_run_returns_tool_results(self, config_file: Path):
        """run() returns a list of ToolResult from registered tools."""
        pipeline = Pipeline(config_file)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert isinstance(results[0], ToolResult)
        assert results[0].heading == "Current Time"

    def test_run_unregistered_event_returns_empty(self, config_file: Path):
        """run() returns empty list for events with no registered tools."""
        pipeline = Pipeline(config_file)
        results = pipeline.run("PostCompact", {})
        assert results == []

    def test_run_passes_params_to_tool(self, config_file: Path):
        """run() passes the params from YAML config to the tool's execute()."""
        pipeline = Pipeline(config_file)
        results = pipeline.run("SessionStart", {})
        # SessionStart has format: "%Y-%m-%d" — content should be 10 chars
        assert len(results[0].content) == 10

    def test_run_multiple_tools_in_order(self, tmp_path: Path):
        """run() executes tools in the order they appear in the YAML."""
        config = {
            "pipelines": {
                "SessionStart": [
                    {
                        "tool": "agent_core.hooks.tools.time_injector.TimeInjector",
                        "params": {"format": "%Y"},
                    },
                    {
                        "tool": "agent_core.hooks.tools.time_injector.TimeInjector",
                        "params": {"format": "%m"},
                    },
                ],
            }
        }
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 2
        # First tool returns year (4 chars), second returns month (2 chars)
        assert len(results[0].content) == 4
        assert len(results[1].content) == 2

    def test_run_bad_tool_class_skips_gracefully(self, tmp_path: Path):
        """run() logs an error and skips tools that can't be imported."""
        config = {
            "pipelines": {
                "SessionStart": [
                    {"tool": "nonexistent.module.FakeTool"},
                    {"tool": "agent_core.hooks.tools.time_injector.TimeInjector"},
                ],
            }
        }
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")

        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        # Bad tool is skipped, good tool still runs
        assert len(results) == 1
        assert results[0].heading == "Current Time"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py::TestPipelineRun -v`
Expected: FAIL — `AttributeError: 'Pipeline' object has no attribute 'run'`

- [ ] **Step 7: Implement Pipeline.run**

Add to `Pipeline` class in `src/agent_core/hooks/pipeline.py`:
```python
    def _import_tool_class(self, class_path: str) -> type | None:
        """Dynamically import a tool class from its fully qualified path.

        Args:
            class_path: Dotted path like "agent_core.hooks.tools.time_injector.TimeInjector"

        Returns:
            The class object, or None if import fails.
        """
        module_path, class_name = class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.error("Failed to import tool '%s': %s", class_path, e)
            return None

        if not isinstance(cls, type) or not issubclass(cls, HookTool):
            # runtime_checkable Protocol check — verify the class has execute()
            instance = cls()
            if not isinstance(instance, HookTool):
                logger.error("Tool '%s' does not implement HookTool protocol", class_path)
                return None

        return cls

    def run(self, event: str, hook_input: dict) -> list[ToolResult]:
        """Execute all tools registered for an event, in declared order.

        Each tool is instantiated fresh for every run. If a tool fails to import
        or raises an exception during execute(), the error is logged and that
        tool is skipped — remaining tools still run.

        Args:
            event: The lifecycle event name (e.g., "SessionStart").
            hook_input: Data from Claude Code, passed through to each tool.

        Returns:
            List of ToolResult from all successfully executed tools.
        """
        tool_configs = self.config.pipelines.get(event, [])
        if not tool_configs:
            logger.info("No tools registered for event '%s'", event)
            return []

        results: list[ToolResult] = []

        for tool_config in tool_configs:
            cls = self._import_tool_class(tool_config.tool)
            if cls is None:
                continue

            try:
                instance = cls()
                result = instance.execute(event=event, hook_input=hook_input, params=tool_config.params)
                results.append(result)
                logger.info("Tool '%s' executed successfully", tool_config.tool.rsplit(".", 1)[-1])
            except Exception as e:
                logger.error("Tool '%s' failed during execution: %s", tool_config.tool, e)

        return results
```

- [ ] **Step 8: Run run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py::TestPipelineRun -v`
Expected: 5 passed

- [ ] **Step 9: Write failing tests for Pipeline.render**

Append to `tests/test_pipeline.py`:
```python


class TestPipelineRender:
    """Tests for Pipeline.render — compiling ToolResults into markdown."""

    def test_render_single_result(self, config_file: Path):
        """render() formats one ToolResult as ## heading + content."""
        pipeline = Pipeline(config_file)
        results = [ToolResult(heading="Test", content="Hello world")]
        markdown = pipeline.render(results)
        assert markdown == "## Test\n\nHello world"

    def test_render_multiple_results_separated_by_dividers(self, config_file: Path):
        """render() separates multiple results with --- dividers."""
        pipeline = Pipeline(config_file)
        results = [
            ToolResult(heading="First", content="AAA"),
            ToolResult(heading="Second", content="BBB"),
        ]
        markdown = pipeline.render(results)
        assert markdown == "## First\n\nAAA\n\n---\n\n## Second\n\nBBB"

    def test_render_empty_results(self, config_file: Path):
        """render() returns empty string when no results are provided."""
        pipeline = Pipeline(config_file)
        markdown = pipeline.render([])
        assert markdown == ""
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py::TestPipelineRender -v`
Expected: FAIL — `AttributeError: 'Pipeline' object has no attribute 'render'`

- [ ] **Step 11: Implement Pipeline.render**

Add to `Pipeline` class in `src/agent_core/hooks/pipeline.py`:
```python
    def render(self, results: list[ToolResult]) -> str:
        """Compile tool results into a single markdown document.

        Each ToolResult becomes a ## heading followed by its content.
        Multiple results are separated by --- dividers.

        Args:
            results: List of ToolResult from a pipeline run.

        Returns:
            Markdown string ready for additionalContext injection.
            Empty string if no results.
        """
        if not results:
            return ""

        sections = [f"## {r.heading}\n\n{r.content}" for r in results]
        return "\n\n---\n\n".join(sections)
```

- [ ] **Step 12: Run all pipeline tests**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 12 passed

- [ ] **Step 13: Commit**

```bash
git add src/agent_core/hooks/pipeline.py tests/test_pipeline.py
git commit -m "feat: add Pipeline — load YAML config, run tools, render markdown"
```

---

### Task 6: Typer CLI

**Files:**
- Create: `src/agent_core/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for the CLI**

`tests/test_cli.py`:
```python
"""Tests for the agent-core CLI.

The CLI is the entrypoint that Claude Code hooks call. It reads hook_input
from stdin, runs the pipeline, and outputs JSON to stdout.
"""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_core.cli import app

runner = CliRunner()


def make_config(tmp_path: Path) -> Path:
    """Create a test config file with TimeInjector registered."""
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "tool": "agent_core.hooks.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y-%m-%d"},
                }
            ],
        }
    }
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


class TestHooksRunCommand:
    """Tests for `agent-core hooks run <event>`."""

    def test_run_session_start(self, tmp_path: Path):
        """CLI outputs valid JSON with additionalContext for SessionStart."""
        config_path = make_config(tmp_path)
        hook_input = json.dumps({"session_id": "test-123"})

        result = runner.invoke(app, ["hooks", "run", "SessionStart", "--config", str(config_path)], input=hook_input)

        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "## Current Time" in output["hookSpecificOutput"]["additionalContext"]

    def test_run_unregistered_event(self, tmp_path: Path):
        """CLI returns empty additionalContext for events with no tools."""
        config_path = make_config(tmp_path)
        hook_input = json.dumps({"session_id": "test-123"})

        result = runner.invoke(app, ["hooks", "run", "PostCompact", "--config", str(config_path)], input=hook_input)

        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["additionalContext"] == ""

    def test_run_missing_config(self, tmp_path: Path):
        """CLI exits with error when config file doesn't exist."""
        hook_input = json.dumps({"session_id": "test-123"})

        result = runner.invoke(app, ["hooks", "run", "SessionStart", "--config", str(tmp_path / "missing.yaml")], input=hook_input)

        assert result.exit_code != 0

    def test_run_with_empty_stdin(self, tmp_path: Path):
        """CLI handles empty stdin gracefully (treats as empty dict)."""
        config_path = make_config(tmp_path)

        result = runner.invoke(app, ["hooks", "run", "SessionStart", "--config", str(config_path)], input="")

        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.cli'`

- [ ] **Step 3: Implement the CLI**

`src/agent_core/cli.py`:
```python
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
    # Default config (agent_core.yaml in cwd)
    echo '{"session_id": "abc"}' | agent-core hooks run SessionStart

    # Explicit config path
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
        exists=False,  # We handle missing file ourselves for a better error message
    ),
) -> None:
    """Execute all tools registered for a hook event.

    Reads hook_input JSON from stdin (provided by Claude Code), runs each
    registered tool in order, compiles the results into markdown, and outputs
    the JSON response that Claude Code expects.

    The output format is:
        {"hookSpecificOutput": {"hookEventName": "<event>", "additionalContext": "<markdown>"}}
    """
    # Load pipeline config
    try:
        pipeline = Pipeline(config)
    except FileNotFoundError:
        typer.echo(f"Error: Config file not found: {config}", err=True)
        raise typer.Exit(code=1)

    # Read hook_input from stdin
    raw_input = sys.stdin.read().strip()
    if raw_input:
        try:
            hook_input = json.loads(raw_input)
        except json.JSONDecodeError:
            hook_input = {}
    else:
        hook_input = {}

    # Run the pipeline
    results = pipeline.run(event, hook_input)
    markdown = pipeline.render(results)

    # Output the JSON that Claude Code expects
    output = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": markdown,
        }
    }
    typer.echo(json.dumps(output))
```

- [ ] **Step 4: Run CLI tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Run all tests**

Run: `uv run pytest -v`
Expected: All tests pass (models: 9, time_injector: 7, pipeline: 12, cli: 4 = 32 total)

- [ ] **Step 6: Commit**

```bash
git add src/agent_core/cli.py tests/test_cli.py
git commit -m "feat: add Typer CLI — agent-core hooks run command"
```

---

### Task 7: YAML config and hook wiring

**Files:**
- Create: `agent_core.yaml`
- Modify: `.claude/settings.json`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create agent_core.yaml**

`agent_core.yaml`:
```yaml
# agent_core.yaml — Pipeline configuration
#
# Maps Claude Code lifecycle events to ordered lists of tools.
# Each tool is a Python class implementing the HookTool protocol.
# Tools execute in declared order. The same tool can appear in
# multiple events with different params.
#
# Supported events (those with additionalContext injection):
#   - SessionStart: Session begins or resumes
#   - PreToolUse: Before a tool call executes
#   - PostToolUse: After a tool call succeeds
#   - PostToolUseFailure: After a tool call fails
#   - SubagentStart: When a subagent is spawned
#   - UserPromptSubmit: When user submits a prompt

pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
```

- [ ] **Step 2: Update .claude/settings.json to use the CLI**

Replace the current hooks in `.claude/settings.json` with the CLI entrypoint. Keep the memory-compiler hooks for SessionEnd and PreCompact since those aren't additionalContext events — they handle transcript capture:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run agent-core hooks run SessionStart",
            "timeout": 15
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory . python memory-compiler/hooks/pre-compact.py",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory . python memory-compiler/hooks/session-end.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Test the hook end-to-end**

Run: `echo '{"session_id": "test"}' | uv run agent-core hooks run SessionStart`
Expected: JSON output with `## Current Time` in additionalContext

- [ ] **Step 4: Update CLAUDE.md with CLI documentation**

Add to the existing CLAUDE.md after the Memory Compiler section:

```markdown
## Hook Tool Pipeline

Pluggable tools that run at Claude Code lifecycle events. Tools are Python classes
implementing the HookTool protocol, declared in `agent_core.yaml`.

### CLI Commands

\`\`\`bash
# Run tools for a lifecycle event (called by Claude Code hooks)
agent-core hooks run SessionStart

# Run with explicit config
agent-core hooks run SessionStart --config /path/to/config.yaml
\`\`\`

### Writing a Tool

Tools implement the HookTool protocol — a single `execute()` method:

\`\`\`python
from agent_core.models import ToolResult

class MyTool:
    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        return ToolResult(heading="My Heading", content="My content")
\`\`\`

Register in `agent_core.yaml`:

\`\`\`yaml
pipelines:
  SessionStart:
    - tool: my_package.my_module.MyTool
      params:
        key: value
\`\`\`
```

- [ ] **Step 5: Commit**

```bash
git add agent_core.yaml .claude/settings.json CLAUDE.md
git commit -m "feat: wire up YAML config and Claude Code hooks to CLI pipeline"
```

---

### Task 8: Knowledge wiki documentation

**Files:**
- Create: `memory-compiler/knowledge/concepts/hook-tool-protocol.md`
- Create: `memory-compiler/knowledge/concepts/pipeline-system.md`
- Create: `memory-compiler/knowledge/concepts/agent-core-cli.md`
- Create: `memory-compiler/knowledge/concepts/agent-core-yaml-config.md`
- Modify: `memory-compiler/knowledge/index.md`
- Modify: `memory-compiler/knowledge/log.md`

- [ ] **Step 1: Create hook-tool-protocol article**

`memory-compiler/knowledge/concepts/hook-tool-protocol.md`:
```markdown
---
title: "HookTool Protocol"
aliases: [hook-tool, tool-protocol]
tags: [agent-core, hooks, protocol]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# HookTool Protocol

The HookTool protocol is the interface that all hook tools in agent_core must implement. It uses Python's structural subtyping (typing.Protocol) — tools don't inherit from a base class, they just need an execute() method with the correct signature.

## Key Points

- Tools are plain Python classes with a single required method: execute()
- The protocol is @runtime_checkable, so the pipeline verifies compliance at load time
- Tools receive three arguments: the event name, hook_input from Claude Code, and params from YAML config
- Tools return a ToolResult(heading, content) — the pipeline compiles these into markdown
- Tools are stateless — each invocation gets a fresh instance

## The Protocol

```python
from typing import Protocol, runtime_checkable
from agent_core.models import ToolResult

@runtime_checkable
class HookTool(Protocol):
    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        ...
```

## Writing a Tool

A minimal tool that satisfies the protocol:

```python
from agent_core.models import ToolResult

class GreetingTool:
    """Injects a greeting message into the session context."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        name = params.get("name", "World")
        return ToolResult(
            heading="Greeting",
            content=f"Hello, {name}!",
        )
```

Register it in agent_core.yaml:

```yaml
pipelines:
  SessionStart:
    - tool: my_tools.GreetingTool
      params:
        name: Jeff
```

## Arguments

- **event** (str): The lifecycle event name — SessionStart, PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart, or UserPromptSubmit.
- **hook_input** (dict): Data from Claude Code. Always includes session_id, transcript_path, cwd, permission_mode. Event-specific fields vary.
- **params** (dict): Tool-specific config from agent_core.yaml. Empty dict if no params declared.

## Related Concepts

- [[concepts/pipeline-system]] - The runner that loads and executes tools
- [[concepts/agent-core-yaml-config]] - How tools are registered in YAML

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
```

- [ ] **Step 2: Create pipeline-system article**

`memory-compiler/knowledge/concepts/pipeline-system.md`:
```markdown
---
title: "Pipeline System"
aliases: [pipeline, hook-pipeline]
tags: [agent-core, hooks, pipeline]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# Pipeline System

The Pipeline class is the core engine of agent_core's hook tool system. It loads YAML configuration, dynamically imports tool classes, executes them in order, and compiles their outputs into a markdown document for Claude Code context injection.

## Key Points

- Pipeline reads agent_core.yaml and validates it with Pydantic (PipelineConfig model)
- Tools are dynamically imported from their fully qualified class paths at runtime
- Tools execute in the order declared in YAML — order matters
- Failed tools are logged and skipped — remaining tools still run
- Output is compiled markdown: ## headings separated by --- dividers

## Lifecycle

1. `Pipeline(config_path)` — load and validate YAML config
2. `pipeline.run(event, hook_input)` — instantiate and execute tools for an event
3. `pipeline.render(results)` — compile ToolResult list into markdown string

## Details

The pipeline is designed for resilience. If a tool can't be imported (typo in class path, missing dependency), or if it raises an exception during execute(), the error is logged with Rich and that tool is skipped. This prevents one broken tool from taking down the entire context injection.

Dynamic import uses Python's importlib: the fully qualified class path (e.g., "agent_core.hooks.tools.time_injector.TimeInjector") is split into module path and class name, imported, and verified against the HookTool protocol before execution.

Each tool gets a fresh instance per run — tools should not rely on state persisting between calls.

## Rendered Output Format

```markdown
## First Tool Heading

First tool content here.

---

## Second Tool Heading

Second tool content here.
```

This markdown string is placed into Claude Code's additionalContext field.

## Related Concepts

- [[concepts/hook-tool-protocol]] - The interface tools must implement
- [[concepts/agent-core-yaml-config]] - Configuration format
- [[concepts/agent-core-cli]] - CLI that invokes the pipeline

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
```

- [ ] **Step 3: Create agent-core-cli article**

`memory-compiler/knowledge/concepts/agent-core-cli.md`:
```markdown
---
title: "agent-core CLI"
aliases: [cli, agent-core-command]
tags: [agent-core, cli, typer]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# agent-core CLI

The agent-core CLI is the Typer-based command-line interface for the agent_core framework. It serves as the entrypoint that Claude Code hooks call to run the tool pipeline.

## Key Points

- Built with Typer for automatic help text, argument parsing, and tab completion
- Primary command: `agent-core hooks run <event>` — runs all tools for a lifecycle event
- Reads hook_input from stdin (JSON from Claude Code), outputs JSON to stdout
- Config file defaults to agent_core.yaml in cwd, overridable with --config flag
- Registered as a console script in pyproject.toml via `[project.scripts]`

## Commands

### agent-core hooks run

Execute all tools registered for a hook event.

```bash
# Called by Claude Code hooks (stdin is provided automatically)
agent-core hooks run SessionStart

# With explicit config
agent-core hooks run SessionStart --config /path/to/agent_core.yaml

# Manual testing
echo '{"session_id": "test"}' | agent-core hooks run SessionStart
```

### Output Format

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "## Current Time\n\nMonday, April 13, 2026..."
  }
}
```

## Hook Wiring

In .claude/settings.json:

```json
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
```

## Related Concepts

- [[concepts/pipeline-system]] - The engine the CLI invokes
- [[concepts/agent-core-yaml-config]] - Config file the CLI loads

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
```

- [ ] **Step 4: Create agent-core-yaml-config article**

`memory-compiler/knowledge/concepts/agent-core-yaml-config.md`:
```markdown
---
title: "agent_core.yaml Configuration"
aliases: [yaml-config, pipeline-config]
tags: [agent-core, config, yaml]
sources:
  - "docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# agent_core.yaml Configuration

The agent_core.yaml file declares which tools run at each Claude Code lifecycle event. It's the central configuration for the hook tool pipeline — the single place that controls what happens when.

## Key Points

- YAML format — readable, supports comments, handles nesting well
- Validated at load time by Pydantic (PipelineConfig model)
- Maps event names to ordered lists of tool class paths
- Same tool class can appear in multiple events with different params
- Each agent brings its own config — agent_core is just the framework

## Format

```yaml
pipelines:
  <EventName>:
    - tool: <fully.qualified.ClassName>
      params:
        key: value
    - tool: <another.tool.ClassName>
```

## Complete Example

```yaml
pipelines:
  SessionStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"

  PreToolUse:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector

  SubagentStart:
    - tool: agent_core.hooks.tools.time_injector.TimeInjector
```

## Supported Events

Events that support additionalContext injection:

| Event | When it fires |
|-------|--------------|
| SessionStart | Session begins or resumes |
| PreToolUse | Before a tool call executes |
| PostToolUse | After a tool call succeeds |
| PostToolUseFailure | After a tool call fails |
| SubagentStart | When a subagent is spawned |
| UserPromptSubmit | When user submits a prompt |

## Pydantic Models

The YAML is validated against these models:

- **PipelineConfig**: Root model with `pipelines: dict[str, list[ToolConfig]]`
- **ToolConfig**: Single registration with `tool: str` and `params: dict = {}`

## Related Concepts

- [[concepts/hook-tool-protocol]] - The interface tools must implement
- [[concepts/pipeline-system]] - The engine that reads this config
- [[concepts/agent-core-cli]] - CLI that loads this config

## Sources

- [[docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md]] - Original design spec
```

- [ ] **Step 5: Update knowledge/index.md**

Replace the contents of `memory-compiler/knowledge/index.md`:
```markdown
# Knowledge Base Index

| Article | Summary | Compiled From | Updated |
|---------|---------|---------------|---------|
| [[concepts/hook-tool-protocol]] | HookTool Protocol — interface all hook tools implement, with execute() method returning ToolResult | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/pipeline-system]] | Pipeline class — loads YAML config, imports tools, runs them in order, renders markdown output | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/agent-core-cli]] | Typer CLI entrypoint — agent-core hooks run command wired to Claude Code hooks | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
| [[concepts/agent-core-yaml-config]] | agent_core.yaml config format — maps lifecycle events to ordered tool lists with params | docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md | 2026-04-13 |
```

- [ ] **Step 6: Update knowledge/log.md**

Append to `memory-compiler/knowledge/log.md`:
```markdown

## [2026-04-13T00:00:00-05:00] manual | Pluggable Hook Tools Design
- Source: docs/superpowers/specs/2026-04-13-pluggable-hook-tools-design.md
- Articles created: [[concepts/hook-tool-protocol]], [[concepts/pipeline-system]], [[concepts/agent-core-cli]], [[concepts/agent-core-yaml-config]]
- Articles updated: (none)
```

- [ ] **Step 7: Commit**

```bash
git add memory-compiler/knowledge/
git commit -m "docs: add knowledge wiki articles for hook tool system"
```

---

## Self-Review

**Spec coverage:**
- ToolResult, ToolConfig, PipelineConfig models — Task 2
- HookTool protocol — Task 3
- TimeInjector reference tool — Task 4
- Pipeline load/run/render — Task 5
- Typer CLI with hooks run command — Task 6
- YAML config format — Task 7
- Claude Code hook wiring — Task 7
- Rich logging — Task 5 (Pipeline uses RichHandler)
- Documentation in knowledge wiki — Task 8
- Per-agent config via --config flag — Task 6

**Placeholder scan:** No TBD, TODO, or vague steps found. All steps contain actual code.

**Type consistency:** ToolResult, ToolConfig, PipelineConfig, HookTool, Pipeline — names consistent across all tasks. execute() signature matches everywhere: `(self, event: str, hook_input: dict, params: dict) -> ToolResult`.
