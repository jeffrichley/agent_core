# Sentient Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the sentient monorepo workspace with three foundational packages — sentient-core, sentient-memory, and sentient-llm-anthropic.

**Architecture:** uv workspace monorepo with namespace packages under `sentient.*`. Each package has its own `pyproject.toml` and can be installed independently. sentient-core is migrated from agent_core (same logic, new namespace). sentient-memory is a fresh implementation modeled after Pepper's vault system — it is NOT a migration of the memory-compiler (that stays in agent_core as a project-specific tool).

**Note:** The `memory-compiler/` directory in agent_core is a separate system used to manage that specific project's knowledge base. It is NOT being migrated. sentient-memory is a new package for agent home directory vault operations (SOUL.md, IDENTITY.md, daily logs, etc.). The compiler/query/lint features may come to sentient-memory later as their own implementation.

**Tech Stack:** Python 3.12+, uv workspaces, hatchling build backend, pydantic, typer, rich, pyyaml, claude-agent-sdk, pytest, ruff

**Spec:** `docs/superpowers/specs/2026-04-13-sentient-library-design.md`

---

## File Map

### Workspace Root
- Create: `pyproject.toml` (workspace config, not a package)
- Create: `CLAUDE.md` (contributor instructions)

### sentient-core (`packages/sentient-core/`)
- Create: `packages/sentient-core/pyproject.toml`
- Create: `packages/sentient-core/src/sentient/core/__init__.py`
- Create: `packages/sentient-core/src/sentient/core/models.py` (from `src/agent_core/models.py`)
- Create: `packages/sentient-core/src/sentient/core/protocols.py` (new — LLMProvider, AuthProvider, AdapterProtocol, ChannelProvider + existing HookTool)
- Create: `packages/sentient-core/src/sentient/core/hooks/__init__.py`
- Create: `packages/sentient-core/src/sentient/core/hooks/pipeline.py` (from `src/agent_core/hooks/pipeline.py`)
- Create: `packages/sentient-core/src/sentient/core/transcript.py` (from `src/agent_core/transcript.py`)
- Create: `packages/sentient-core/src/sentient/core/tools/__init__.py`
- Create: `packages/sentient-core/src/sentient/core/tools/time_injector.py` (from `src/agent_core/hooks/tools/time_injector.py`)
- Create: `packages/sentient-core/src/sentient/core/tools/file_injector.py` (from `src/agent_core/hooks/tools/file_injector.py`)
- Create: `packages/sentient-core/src/sentient/core/tools/identity_injector.py` (from `src/agent_core/hooks/tools/identity_injector.py`)
- Create: `packages/sentient-core/src/sentient/core/tools/handoff_writer.py` (from `src/agent_core/hooks/tools/handoff_writer.py` — refactored to use LLMProvider)
- Create: `packages/sentient-core/src/sentient/core/cli.py` (from `src/agent_core/cli.py` — becomes `sentient-hook` entrypoint)
- Create: `packages/sentient-core/src/sentient/core/config.py` (TOML config loader for agent config.toml)

### sentient-memory (`packages/sentient-memory/`)
> **Note:** This is a NEW package, not a migration of memory-compiler. Modeled after Pepper's vault system.

- Create: `packages/sentient-memory/pyproject.toml`
- Create: `packages/sentient-memory/src/sentient/memory/__init__.py`
- Create: `packages/sentient-memory/src/sentient/memory/config.py` (path constants relative to agent home dir)
- Create: `packages/sentient-memory/src/sentient/memory/vault.py` (daily log I/O, vault file read/write)
- Create: `packages/sentient-memory/src/sentient/memory/hooks/__init__.py`
- Create: `packages/sentient-memory/src/sentient/memory/hooks/session_start.py` (HookTool: inject identity + vault context)
- Create: `packages/sentient-memory/src/sentient/memory/hooks/session_end.py` (HookTool: capture transcript to daily log)
- Create: `packages/sentient-memory/src/sentient/memory/hooks/pre_compact.py` (HookTool: capture context before compaction)

### sentient-llm-anthropic (`providers/sentient-llm-anthropic/`)
- Create: `providers/sentient-llm-anthropic/pyproject.toml`
- Create: `providers/sentient-llm-anthropic/src/sentient/llm/anthropic/__init__.py`
- Create: `providers/sentient-llm-anthropic/src/sentient/llm/anthropic/provider.py`

### Tests
- Create: `packages/sentient-core/tests/test_models.py`
- Create: `packages/sentient-core/tests/test_protocols.py`
- Create: `packages/sentient-core/tests/test_pipeline.py`
- Create: `packages/sentient-core/tests/test_transcript.py`
- Create: `packages/sentient-core/tests/test_time_injector.py`
- Create: `packages/sentient-core/tests/test_file_injector.py`
- Create: `packages/sentient-core/tests/test_handoff_writer.py`
- Create: `packages/sentient-core/tests/test_cli.py`
- Create: `packages/sentient-core/tests/test_config.py`
- Create: `packages/sentient-memory/tests/test_vault.py`
- Create: `packages/sentient-memory/tests/test_hooks.py`
- Create: `providers/sentient-llm-anthropic/tests/test_provider.py`

---

## Task 1: Workspace Root Setup

**Files:**
- Create: `pyproject.toml` (workspace root — at new repo root, NOT inside agent_core)
- Create: `CLAUDE.md`

> **Important:** This plan assumes we are creating a NEW repository for the sentient monorepo. The existing agent_core repo stays intact as a reference during migration. The new repo is where all work happens.

- [ ] **Step 1: Create the new repository directory**

```bash
mkdir -p /e/workspaces/ai/agents/sentient
cd /e/workspaces/ai/agents/sentient
git init
```

- [ ] **Step 2: Create the workspace root pyproject.toml**

```toml
[project]
name = "sentient-workspace"
version = "0.0.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = [
    "packages/*",
    "adapters/*",
    "providers/*",
    "integrations/*",
]

[tool.ruff]
line-length = 100
src = ["packages/*/src", "adapters/*/src", "providers/*/src", "integrations/*/src"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.pytest.ini_options]
testpaths = [
    "packages/sentient-core/tests",
    "packages/sentient-memory/tests",
    "providers/sentient-llm-anthropic/tests",
]
```

- [ ] **Step 3: Create the directory scaffold**

```bash
mkdir -p packages/sentient-core/src/sentient/core/hooks
mkdir -p packages/sentient-core/src/sentient/core/tools
mkdir -p packages/sentient-core/tests
mkdir -p packages/sentient-memory/src/sentient/memory/hooks
mkdir -p packages/sentient-memory/tests
mkdir -p providers/sentient-llm-anthropic/src/sentient/llm/anthropic
mkdir -p providers/sentient-llm-anthropic/tests
mkdir -p adapters
mkdir -p integrations
mkdir -p templates/default/Memory
```

- [ ] **Step 4: Create CLAUDE.md**

```markdown
# Sentient

Modular Python library for creating persistent AI agents. Monorepo managed by uv workspaces.

## Project Structure

- `packages/sentient-core/` — Protocols, models, pipeline engine, built-in tools, CLI
- `packages/sentient-memory/` — Memory compiler, knowledge base, vault system
- `providers/sentient-llm-anthropic/` — Anthropic/Claude LLM provider
- `adapters/` — Per-CLI-agent config generators (Claude Code, Cursor, Codex, etc.)
- `integrations/` — Optional services (Discord, scheduler, channel, credentials)
- `templates/` — Agent home directory templates

## Development

```bash
uv sync                          # Install all workspace packages
uv run pytest                    # Run all tests
uv run ruff check .              # Lint
uv run ruff format .             # Format
```

## Conventions

- Python 3.12+, managed by uv
- Ruff for linting (line-length 100)
- Namespace packages: `sentient.core`, `sentient.memory`, `sentient.llm.anthropic`, etc.
- Strategy pattern for all pluggable components (LLM, auth, adapters, channels)
- Entry points for runtime discovery of strategy implementations
```

- [ ] **Step 5: Create .gitignore**

```
__pycache__/
*.pyc
.venv/
*.egg-info/
dist/
build/
.ruff_cache/
.pytest_cache/
uv.lock
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml CLAUDE.md .gitignore
git commit -m "feat: initialize sentient monorepo workspace"
```

---

## Task 2: sentient-core Package Skeleton + Models

**Files:**
- Create: `packages/sentient-core/pyproject.toml`
- Create: `packages/sentient-core/src/sentient/core/__init__.py`
- Create: `packages/sentient-core/src/sentient/core/models.py`
- Test: `packages/sentient-core/tests/test_models.py`

- [ ] **Step 1: Write the test for models**

```python
# packages/sentient-core/tests/test_models.py
"""Tests for sentient.core.models."""

from sentient.core.models import PipelineConfig, ToolConfig, ToolResult


def test_tool_result_fields():
    result = ToolResult(heading="Test", content="Hello")
    assert result.heading == "Test"
    assert result.content == "Hello"


def test_tool_config_defaults():
    config = ToolConfig(tool="some.module.Tool")
    assert config.tool == "some.module.Tool"
    assert config.params == {}


def test_tool_config_with_params():
    config = ToolConfig(tool="some.Tool", params={"key": "value"})
    assert config.params["key"] == "value"


def test_pipeline_config_empty():
    config = PipelineConfig(pipelines={})
    assert config.pipelines == {}


def test_pipeline_config_with_tools():
    config = PipelineConfig(
        pipelines={
            "SessionStart": [
                ToolConfig(tool="a.B"),
                ToolConfig(tool="c.D", params={"x": 1}),
            ]
        }
    )
    assert len(config.pipelines["SessionStart"]) == 2
    assert config.pipelines["SessionStart"][1].params["x"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/workspaces/ai/agents/sentient
uv run pytest packages/sentient-core/tests/test_models.py -v
```

Expected: FAIL — `sentient.core.models` not found.

- [ ] **Step 3: Create the package pyproject.toml**

```toml
# packages/sentient-core/pyproject.toml
[project]
name = "sentient-core"
version = "0.1.0"
description = "Core infrastructure for Sentient AI agents — protocols, models, pipeline engine"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sentient"]

[project.scripts]
sentient-hook = "sentient.core.cli:app"
```

- [ ] **Step 4: Create __init__.py (namespace package — minimal)**

```python
# packages/sentient-core/src/sentient/core/__init__.py
"""sentient.core — Core infrastructure for Sentient AI agents."""

__version__ = "0.1.0"
```

Note: `packages/sentient-core/src/sentient/__init__.py` must NOT exist — this is a namespace package. The `sentient` namespace is shared across all packages.

- [ ] **Step 5: Create models.py**

```python
# packages/sentient-core/src/sentient/core/models.py
"""Pydantic models for the Sentient hook pipeline system.

Models:
- ToolResult: Output returned by every hook tool after execution.
- ToolConfig: A single entry in a pipeline configuration.
- PipelineConfig: Root config mapping hook event names to tool lists.
"""

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Output of a single hook tool execution.

    Attributes:
        heading: Section header in assembled output (e.g. "Current Time").
        content: The payload string to inject into context.
    """

    heading: str
    content: str


class ToolConfig(BaseModel):
    """Configuration for a single tool in a hook pipeline.

    Attributes:
        tool: Fully qualified Python class path to the hook tool.
        params: Keyword arguments passed to the tool's execute() method.
    """

    tool: str
    params: dict = {}


class PipelineConfig(BaseModel):
    """Root configuration mapping hook event names to tool pipelines.

    Attributes:
        pipelines: Dict of event name -> ordered list of ToolConfig.
    """

    pipelines: dict[str, list[ToolConfig]]
```

- [ ] **Step 6: Sync workspace and run test**

```bash
cd /e/workspaces/ai/agents/sentient
uv sync
uv run pytest packages/sentient-core/tests/test_models.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/sentient-core/
git commit -m "feat(core): add sentient-core package with Pydantic models"
```

---

## Task 3: Strategy Protocols

**Files:**
- Create: `packages/sentient-core/src/sentient/core/protocols.py`
- Test: `packages/sentient-core/tests/test_protocols.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-core/tests/test_protocols.py
"""Tests for sentient.core.protocols — verify protocol compliance checking."""

from sentient.core.models import ToolResult
from sentient.core.protocols import HookTool, LLMProvider


def test_hook_tool_protocol_compliance():
    """A class with the right execute() signature satisfies HookTool."""

    class GoodTool:
        def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
            return ToolResult(heading="Test", content="ok")

    assert isinstance(GoodTool(), HookTool)


def test_hook_tool_protocol_rejection():
    """A class without execute() does NOT satisfy HookTool."""

    class BadTool:
        def run(self) -> None:
            pass

    assert not isinstance(BadTool(), HookTool)


def test_llm_provider_protocol_compliance():
    """A class with the right query() signature satisfies LLMProvider."""

    class GoodProvider:
        async def query(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
            return "response"

    assert isinstance(GoodProvider(), LLMProvider)


def test_llm_provider_protocol_rejection():
    """A class without query() does NOT satisfy LLMProvider."""

    class BadProvider:
        def ask(self, question: str) -> str:
            return "nope"

    assert not isinstance(BadProvider(), LLMProvider)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-core/tests/test_protocols.py -v
```

Expected: FAIL — `sentient.core.protocols` not found.

- [ ] **Step 3: Create protocols.py**

```python
# packages/sentient-core/src/sentient/core/protocols.py
"""Strategy protocols for pluggable Sentient components.

All protocols use @runtime_checkable for isinstance() checking at load time.
Implementations register via Python entry points for runtime discovery.

Protocols:
- HookTool: Interface for pipeline hook tools.
- LLMProvider: Interface for LLM API calls.
- AuthProvider: Interface for credential retrieval.
- AdapterProtocol: Interface for CLI agent config generators.
- ChannelProvider: Interface for communication channels.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sentient.core.models import ToolResult


@runtime_checkable
class HookTool(Protocol):
    """Protocol that all hook tools must implement.

    Tools are registered in config.toml pipelines and executed by the
    pipeline engine when their associated lifecycle event fires.
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Execute the tool and return a result to inject into context."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for making LLM API calls.

    Implementations wrap specific SDKs (Anthropic, OpenAI, etc.).
    Registered via entry points under 'sentient.llm'.
    """

    async def query(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
        """Send a prompt to the LLM and return the text response."""
        ...


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for credential retrieval.

    Implementations: env vars, KeePass, config inline, cloud.
    """

    def get_credential(self, service: str) -> dict[str, str]:
        """Retrieve credentials for a named service.

        Returns dict with keys like 'api_key', 'username', 'password', etc.
        """
        ...


@runtime_checkable
class AdapterProtocol(Protocol):
    """Protocol for CLI agent config generators.

    Adapters generate platform-specific config files (.claude/, .cursor/, etc.)
    from the agent's config.toml. They run at scaffold time, not at runtime.
    """

    name: str
    display_name: str

    def generate(self, agent_dir: Path, manifest: dict) -> None:
        """Write all platform config files into agent_dir."""
        ...

    def validate(self, agent_dir: Path) -> list[str]:
        """Check generated config is valid. Returns list of issues."""
        ...


@runtime_checkable
class ChannelProvider(Protocol):
    """Protocol for communication channels (Discord, Slack, etc.).

    Channel IDs are namespaced: 'discord:12345', 'slack:C04ABCDEF'.
    The channel server routes to the correct provider based on prefix.
    """

    name: str

    async def send(
        self,
        channel_id: str,
        content: str,
        attachments: list | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Send a message. Returns the sent message as a dict."""
        ...

    async def fetch(
        self,
        channel_id: str,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[dict]:
        """Fetch recent messages from a channel."""
        ...

    async def edit(self, channel_id: str, message_id: str, content: str) -> dict:
        """Edit a previously sent message."""
        ...

    async def react(self, channel_id: str, message_id: str, reaction: str) -> None:
        """Add a reaction/acknowledgment."""
        ...

    def capabilities(self) -> set[str]:
        """What this channel supports (send, fetch, edit, react, threads, embeds, etc.)."""
        ...
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_protocols.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-core/src/sentient/core/protocols.py packages/sentient-core/tests/test_protocols.py
git commit -m "feat(core): add strategy protocols — HookTool, LLMProvider, AuthProvider, AdapterProtocol, ChannelProvider"
```

---

## Task 4: Pipeline Engine

**Files:**
- Create: `packages/sentient-core/src/sentient/core/hooks/__init__.py`
- Create: `packages/sentient-core/src/sentient/core/hooks/pipeline.py`
- Test: `packages/sentient-core/tests/test_pipeline.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-core/tests/test_pipeline.py
"""Tests for the Pipeline engine."""

import textwrap

import pytest
import yaml

from sentient.core.hooks.pipeline import Pipeline
from sentient.core.models import ToolResult


@pytest.fixture()
def config_file(tmp_path):
    """Write a minimal pipeline config YAML."""
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "tool": "sentient.core.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y-%m-%d"},
                }
            ]
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    return path


@pytest.fixture()
def empty_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({"pipelines": {}}), encoding="utf-8")
    return path


def test_pipeline_loads_config(config_file):
    pipeline = Pipeline(config_file)
    assert "SessionStart" in pipeline.config.pipelines


def test_pipeline_missing_config():
    from pathlib import Path

    with pytest.raises(FileNotFoundError):
        Pipeline(Path("/nonexistent/config.yaml"))


def test_pipeline_run_returns_results(config_file):
    pipeline = Pipeline(config_file)
    results = pipeline.run("SessionStart", {})
    assert len(results) == 1
    assert results[0].heading == "Current Time"


def test_pipeline_run_empty_event(config_file):
    pipeline = Pipeline(config_file)
    results = pipeline.run("NonExistentEvent", {})
    assert results == []


def test_pipeline_render():
    pipeline_results = [
        ToolResult(heading="One", content="First"),
        ToolResult(heading="Two", content="Second"),
    ]
    # Use a dummy config for render
    from pathlib import Path
    from unittest.mock import patch

    rendered = Pipeline._render_static(pipeline_results)
    assert "## One" in rendered
    assert "## Two" in rendered
    assert "First" in rendered
    assert "Second" in rendered


def test_pipeline_render_empty():
    assert Pipeline._render_static([]) == ""


def test_pipeline_resilient_to_bad_tool(tmp_path):
    """A broken tool doesn't crash the pipeline."""
    config = {
        "pipelines": {
            "SessionStart": [
                {"tool": "nonexistent.module.FakeTool"},
                {
                    "tool": "sentient.core.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y"},
                },
            ]
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    pipeline = Pipeline(path)
    results = pipeline.run("SessionStart", {})
    # The broken tool is skipped, the good one still runs
    assert len(results) == 1
    assert results[0].heading == "Current Time"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-core/tests/test_pipeline.py -v
```

Expected: FAIL — `sentient.core.hooks.pipeline` not found.

- [ ] **Step 3: Create hooks/__init__.py**

```python
# packages/sentient-core/src/sentient/core/hooks/__init__.py
"""sentient.core.hooks — Hook tool pipeline system for lifecycle events."""
```

- [ ] **Step 4: Create pipeline.py**

```python
# packages/sentient-core/src/sentient/core/hooks/pipeline.py
"""Pipeline — core engine of the hook tool system.

Loads config, dynamically imports tools, executes them in order, renders results.
Resilient: single tool failure doesn't crash the pipeline.
"""

import importlib
import logging
from pathlib import Path

import yaml
from rich.logging import RichHandler

from sentient.core.models import PipelineConfig, ToolResult
from sentient.core.protocols import HookTool

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("sentient.core.hooks.pipeline")


class Pipeline:
    """Loads tool configuration, instantiates tools, and runs them for lifecycle events."""

    def __init__(self, config_path: Path) -> None:
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.config = PipelineConfig(**raw)
        self.config_path = config_path

        for event, tools in self.config.pipelines.items():
            tool_names = [t.tool.rsplit(".", 1)[-1] for t in tools]
            logger.info(
                "Event '%s': %d tool(s) registered — %s",
                event,
                len(tools),
                ", ".join(tool_names),
            )

    def _import_tool_class(self, class_path: str) -> type | None:
        module_path, class_name = class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.error("Failed to import tool '%s': %s", class_path, e)
            return None

        if not isinstance(cls, type) or not issubclass(cls, HookTool):
            instance = cls()
            if not isinstance(instance, HookTool):
                logger.error("Tool '%s' does not implement HookTool protocol", class_path)
                return None

        return cls

    def run(self, event: str, hook_input: dict) -> list[ToolResult]:
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
                result = instance.execute(
                    event=event, hook_input=hook_input, params=tool_config.params
                )
                results.append(result)
                logger.info(
                    "Tool '%s' executed successfully", tool_config.tool.rsplit(".", 1)[-1]
                )
            except Exception as e:
                logger.error("Tool '%s' failed during execution: %s", tool_config.tool, e)

        return results

    def render(self, results: list[ToolResult]) -> str:
        return Pipeline._render_static(results)

    @staticmethod
    def _render_static(results: list[ToolResult]) -> str:
        if not results:
            return ""
        sections = [f"## {r.heading}\n\n{r.content}" for r in results]
        return "\n\n---\n\n".join(sections)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_pipeline.py -v
```

Expected: All 7 tests PASS (TimeInjector will fail until Task 5 — run after Task 5 completes).

- [ ] **Step 6: Commit**

```bash
git add packages/sentient-core/src/sentient/core/hooks/ packages/sentient-core/tests/test_pipeline.py
git commit -m "feat(core): add Pipeline engine with resilient tool execution"
```

---

## Task 5: Built-in Tools (TimeInjector, FileInjector, IdentityInjector)

**Files:**
- Create: `packages/sentient-core/src/sentient/core/tools/__init__.py`
- Create: `packages/sentient-core/src/sentient/core/tools/time_injector.py`
- Create: `packages/sentient-core/src/sentient/core/tools/file_injector.py`
- Create: `packages/sentient-core/src/sentient/core/tools/identity_injector.py`
- Test: `packages/sentient-core/tests/test_time_injector.py`
- Test: `packages/sentient-core/tests/test_file_injector.py`

- [ ] **Step 1: Write the TimeInjector test**

```python
# packages/sentient-core/tests/test_time_injector.py
"""Tests for TimeInjector."""

from sentient.core.protocols import HookTool
from sentient.core.tools.time_injector import TimeInjector


def test_protocol_compliance():
    assert isinstance(TimeInjector(), HookTool)


def test_default_format():
    result = TimeInjector().execute("SessionStart", {}, {})
    assert result.heading == "Current Time"
    assert len(result.content) > 0


def test_custom_format():
    result = TimeInjector().execute("SessionStart", {}, {"format": "%Y-%m-%d"})
    assert len(result.content) == 10  # e.g., "2026-04-13"


def test_event_agnostic():
    r1 = TimeInjector().execute("SessionStart", {}, {})
    r2 = TimeInjector().execute("PreToolUse", {}, {})
    assert r1.heading == r2.heading
```

- [ ] **Step 2: Write the FileInjector test**

```python
# packages/sentient-core/tests/test_file_injector.py
"""Tests for FileInjector and IdentityInjector."""

import pytest

from sentient.core.protocols import HookTool
from sentient.core.tools.file_injector import FileInjector
from sentient.core.tools.identity_injector import IdentityInjector


def test_protocol_compliance():
    assert isinstance(FileInjector(), HookTool)


def test_identity_protocol_compliance():
    assert isinstance(IdentityInjector(), HookTool)


def test_reads_files(tmp_path):
    (tmp_path / "a.md").write_text("Alpha content", encoding="utf-8")
    (tmp_path / "b.md").write_text("Beta content", encoding="utf-8")

    result = FileInjector().execute(
        "SessionStart",
        {},
        {"base_path": str(tmp_path), "files": ["a.md", "b.md"]},
    )
    assert "Alpha content" in result.content
    assert "Beta content" in result.content


def test_missing_file_skip(tmp_path):
    (tmp_path / "a.md").write_text("exists", encoding="utf-8")
    result = FileInjector().execute(
        "SessionStart",
        {},
        {"base_path": str(tmp_path), "files": ["a.md", "missing.md"]},
    )
    assert "exists" in result.content
    assert "missing" not in result.content


def test_missing_file_warn(tmp_path):
    result = FileInjector().execute(
        "SessionStart",
        {},
        {
            "base_path": str(tmp_path),
            "files": ["missing.md"],
            "missing_file_behavior": "warn",
        },
    )
    assert "file not found" in result.content


def test_missing_file_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileInjector().execute(
            "SessionStart",
            {},
            {
                "base_path": str(tmp_path),
                "files": ["missing.md"],
                "missing_file_behavior": "error",
            },
        )


def test_missing_base_path():
    with pytest.raises(ValueError, match="base_path"):
        FileInjector().execute("SessionStart", {}, {"files": ["a.md"]})


def test_missing_files_param():
    with pytest.raises(ValueError, match="files"):
        FileInjector().execute("SessionStart", {}, {"base_path": "/tmp"})


def test_identity_default_heading(tmp_path):
    (tmp_path / "SOUL.md").write_text("I am Pepper", encoding="utf-8")
    result = IdentityInjector().execute(
        "SessionStart",
        {},
        {"base_path": str(tmp_path), "files": ["SOUL.md"]},
    )
    assert result.heading == "Identity"


def test_subdirectory_files(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text("nested content", encoding="utf-8")
    result = FileInjector().execute(
        "SessionStart",
        {},
        {"base_path": str(tmp_path), "files": ["sub/nested.md"]},
    )
    assert "nested content" in result.content
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest packages/sentient-core/tests/test_time_injector.py packages/sentient-core/tests/test_file_injector.py -v
```

Expected: FAIL — modules not found.

- [ ] **Step 4: Create tools/__init__.py**

```python
# packages/sentient-core/src/sentient/core/tools/__init__.py
"""sentient.core.tools — Built-in hook tools shipped with sentient-core."""
```

- [ ] **Step 5: Create time_injector.py**

```python
# packages/sentient-core/src/sentient/core/tools/time_injector.py
"""TimeInjector — injects the current date and time into session context."""

from datetime import datetime, timezone

from sentient.core.models import ToolResult


class TimeInjector:
    """Injects the current date and time into the session context."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        fmt = params.get("format", "%A, %B %d, %Y %I:%M %p %Z")
        now = datetime.now(timezone.utc).astimezone()
        return ToolResult(
            heading="Current Time",
            content=now.strftime(fmt),
        )
```

- [ ] **Step 6: Create file_injector.py**

```python
# packages/sentient-core/src/sentient/core/tools/file_injector.py
"""FileInjector — reads files and injects their contents into session context."""

from pathlib import Path

from sentient.core.models import ToolResult


class FileInjector:
    """Reads a list of files and injects their contents into session context.

    Subclasses can override DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR.
    """

    DEFAULT_HEADING = "Injected Files"
    DEFAULT_MISSING_BEHAVIOR = "skip"

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        base_path_str = params.get("base_path")
        if not base_path_str:
            raise ValueError("Required param 'base_path' is missing")

        files = params.get("files")
        if not files:
            raise ValueError("Required param 'files' is missing")

        heading = params.get("heading", self.DEFAULT_HEADING)
        missing_behavior = params.get("missing_file_behavior", self.DEFAULT_MISSING_BEHAVIOR)

        valid_behaviors = ("skip", "warn", "error")
        if missing_behavior not in valid_behaviors:
            raise ValueError(
                f"Invalid missing_file_behavior '{missing_behavior}', "
                f"must be one of: {', '.join(valid_behaviors)}"
            )

        base_path = Path(base_path_str)
        sections: list[str] = []

        for file_rel in files:
            file_path = base_path / file_rel
            file_name = Path(file_rel).name

            if not file_path.exists():
                if missing_behavior == "error":
                    raise FileNotFoundError(f"Required file not found: {file_path}")
                elif missing_behavior == "warn":
                    sections.append(f"## {file_name}\n\n(file not found: {file_rel})")
                continue

            content = file_path.read_text(encoding="utf-8-sig")
            sections.append(f"## {file_name}\n\n{content}")

        return ToolResult(
            heading=heading,
            content="\n\n".join(sections),
        )
```

- [ ] **Step 7: Create identity_injector.py**

```python
# packages/sentient-core/src/sentient/core/tools/identity_injector.py
"""IdentityInjector — injects agent identity files into session context."""

from sentient.core.tools.file_injector import FileInjector


class IdentityInjector(FileInjector):
    """Thin subclass with identity-appropriate defaults."""

    DEFAULT_HEADING = "Identity"
    DEFAULT_MISSING_BEHAVIOR = "skip"
```

- [ ] **Step 8: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_time_injector.py packages/sentient-core/tests/test_file_injector.py -v
```

Expected: All tests PASS.

- [ ] **Step 9: Re-run pipeline tests (they depend on TimeInjector)**

```bash
uv run pytest packages/sentient-core/tests/test_pipeline.py -v
```

Expected: All PASS.

- [ ] **Step 10: Commit**

```bash
git add packages/sentient-core/src/sentient/core/tools/ packages/sentient-core/tests/test_time_injector.py packages/sentient-core/tests/test_file_injector.py
git commit -m "feat(core): add built-in tools — TimeInjector, FileInjector, IdentityInjector"
```

---

## Task 6: Transcript Reader

**Files:**
- Create: `packages/sentient-core/src/sentient/core/transcript.py`
- Test: `packages/sentient-core/tests/test_transcript.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-core/tests/test_transcript.py
"""Tests for the shared JSONL transcript reader."""

import json

import pytest

from sentient.core.transcript import read_transcript


@pytest.fixture()
def transcript_file(tmp_path):
    """Create a sample JSONL transcript."""
    lines = [
        {"message": {"role": "user", "content": "Hello"}},
        {"message": {"role": "assistant", "content": "Hi there!"}},
        {"message": {"role": "user", "content": "How are you?"}},
        {"message": {"role": "assistant", "content": "I'm doing well."}},
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(line) for line in lines),
        encoding="utf-8",
    )
    return path


def test_reads_all_turns(transcript_file):
    context, count = read_transcript(transcript_file)
    assert count == 4
    assert "Hello" in context
    assert "Hi there!" in context


def test_max_turns(transcript_file):
    context, count = read_transcript(transcript_file, max_turns=2)
    assert count == 2
    assert "How are you?" in context
    assert "Hello" not in context


def test_max_chars(transcript_file):
    context, count = read_transcript(transcript_file, max_chars=50)
    assert len(context) <= 50


def test_missing_file(tmp_path):
    context, count = read_transcript(tmp_path / "missing.jsonl")
    assert context == ""
    assert count == 0


def test_empty_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    context, count = read_transcript(path)
    assert context == ""
    assert count == 0


def test_content_blocks(tmp_path):
    """Handles content as list of blocks (Claude's format)."""
    lines = [
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "block content"}],
            }
        },
    ]
    path = tmp_path / "blocks.jsonl"
    path.write_text(json.dumps(lines[0]), encoding="utf-8")
    context, count = read_transcript(path)
    assert "block content" in context


def test_skips_non_user_assistant(tmp_path):
    lines = [
        {"message": {"role": "system", "content": "system prompt"}},
        {"message": {"role": "user", "content": "user msg"}},
    ]
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "\n".join(json.dumps(line) for line in lines),
        encoding="utf-8",
    )
    context, count = read_transcript(path)
    assert count == 1
    assert "system prompt" not in context
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-core/tests/test_transcript.py -v
```

- [ ] **Step 3: Create transcript.py**

```python
# packages/sentient-core/src/sentient/core/transcript.py
"""Shared JSONL transcript reader utility.

Extracts conversation turns from Claude Code's JSONL transcript format.
"""

import json
from pathlib import Path


def read_transcript(
    transcript_path: Path,
    max_turns: int = 200,
    max_chars: int = 15_000,
) -> tuple[str, int]:
    """Read a Claude Code JSONL transcript and extract conversation turns.

    Args:
        transcript_path: Path to the .jsonl transcript file.
        max_turns: Maximum number of turns to extract from the end.
        max_chars: Maximum total character count, truncated at turn boundary.

    Returns:
        Tuple of (formatted markdown string, number of turns extracted).
    """
    if not transcript_path.exists():
        return "", 0

    turns: list[str] = []

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {})
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            if isinstance(content, str) and content.strip():
                label = "User" if role == "user" else "Assistant"
                turns.append(f"**{label}:** {content.strip()}\n")

    recent = turns[-max_turns:]
    context = "\n".join(recent)

    if len(context) > max_chars:
        context = context[-max_chars:]
        boundary = context.find("\n**")
        if boundary > 0:
            context = context[boundary + 1 :]

    return context, len(recent)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_transcript.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-core/src/sentient/core/transcript.py packages/sentient-core/tests/test_transcript.py
git commit -m "feat(core): add JSONL transcript reader utility"
```

---

## Task 7: Config Loader (TOML)

**Files:**
- Create: `packages/sentient-core/src/sentient/core/config.py`
- Test: `packages/sentient-core/tests/test_config.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-core/tests/test_config.py
"""Tests for agent config.toml loader."""

from sentient.core.config import load_agent_config


def test_load_config(tmp_path):
    config_content = """
[agent]
name = "Pepper"
version = "0.1.0"

[llm]
provider = "sentient.llm.anthropic"
model = "claude-sonnet-4-20250514"

[llm.auth]
provider = "env"

[memory]
vault_path = "Memory"
knowledge_path = "knowledge"

[pipelines.SessionStart]
tools = [
    { tool = "sentient.core.tools.time_injector.TimeInjector", params = { format = "%Y-%m-%d" } },
]

[integrations]
discord = true
scheduler = false
"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content, encoding="utf-8")

    config = load_agent_config(config_file)
    assert config["agent"]["name"] == "Pepper"
    assert config["llm"]["provider"] == "sentient.llm.anthropic"
    assert config["llm"]["auth"]["provider"] == "env"
    assert len(config["pipelines"]["SessionStart"]["tools"]) == 1
    assert config["integrations"]["discord"] is True
    assert config["integrations"]["scheduler"] is False


def test_load_config_missing_file(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_agent_config(tmp_path / "missing.toml")


def test_load_config_finds_in_cwd(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[agent]\nname = "Test"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_agent_config()
    assert config["agent"]["name"] == "Test"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-core/tests/test_config.py -v
```

- [ ] **Step 3: Create config.py**

```python
# packages/sentient-core/src/sentient/core/config.py
"""Agent config.toml loader.

Each agent's home directory contains a config.toml that declares pipelines,
LLM provider, integrations, and other settings. This module loads and
provides access to that configuration.
"""

import tomllib
from pathlib import Path


def load_agent_config(config_path: Path | None = None) -> dict:
    """Load the agent's config.toml.

    Args:
        config_path: Path to config.toml. If None, looks in cwd.

    Returns:
        Parsed config as a dict.

    Raises:
        FileNotFoundError: If config.toml doesn't exist.
    """
    if config_path is None:
        config_path = Path.cwd() / "config.toml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        return tomllib.load(f)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_config.py -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-core/src/sentient/core/config.py packages/sentient-core/tests/test_config.py
git commit -m "feat(core): add TOML config loader for agent config.toml"
```

---

## Task 8: CLI Entrypoint (`sentient-hook`)

**Files:**
- Create: `packages/sentient-core/src/sentient/core/cli.py`
- Test: `packages/sentient-core/tests/test_cli.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-core/tests/test_cli.py
"""Tests for the sentient-hook CLI."""

import json

import yaml
from typer.testing import CliRunner

from sentient.core.cli import app

runner = CliRunner()


def test_hook_run_with_config(tmp_path):
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "tool": "sentient.core.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y"},
                }
            ]
        }
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    result = runner.invoke(app, ["hooks", "run", "SessionStart", "--config", str(config_path)])
    assert result.exit_code == 0

    output = json.loads(result.stdout)
    assert "hookSpecificOutput" in output
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert len(output["hookSpecificOutput"]["additionalContext"]) > 0


def test_hook_run_missing_config(tmp_path):
    result = runner.invoke(
        app, ["hooks", "run", "SessionStart", "--config", str(tmp_path / "missing.yaml")]
    )
    assert result.exit_code == 1


def test_hook_run_empty_event(tmp_path):
    config = {"pipelines": {}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    result = runner.invoke(app, ["hooks", "run", "SessionStart", "--config", str(config_path)])
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["additionalContext"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-core/tests/test_cli.py -v
```

- [ ] **Step 3: Create cli.py**

```python
# packages/sentient-core/src/sentient/core/cli.py
"""sentient-hook CLI — universal hook bridge for all CLI agent platforms.

Reads hook input from stdin (JSON), runs the pipeline for the event,
outputs JSON to stdout in the platform's expected format.

Usage:
    echo '{"session_id": "abc"}' | sentient-hook hooks run SessionStart
    sentient-hook hooks run SessionStart --config /path/to/config.yaml
"""

import json
import sys
from pathlib import Path

import typer

from sentient.core.hooks.pipeline import Pipeline

app = typer.Typer(
    name="sentient-hook",
    help="Sentient hook pipeline — run registered tools for lifecycle events.",
    no_args_is_help=True,
)

hooks_app = typer.Typer(
    name="hooks",
    help="Hook tool pipeline — run registered tools for lifecycle events.",
    no_args_is_help=True,
)
app.add_typer(hooks_app, name="hooks")


@hooks_app.command("run")
def run_hook(
    event: str = typer.Argument(help="The lifecycle event name (e.g., SessionStart)."),
    config: Path = typer.Option(
        "config.yaml",
        help="Path to pipeline config file (YAML or agent config.toml pipelines section).",
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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_cli.py -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-core/src/sentient/core/cli.py packages/sentient-core/tests/test_cli.py
git commit -m "feat(core): add sentient-hook CLI entrypoint"
```

---

## Task 9: sentient-llm-anthropic Provider

**Files:**
- Create: `providers/sentient-llm-anthropic/pyproject.toml`
- Create: `providers/sentient-llm-anthropic/src/sentient/llm/anthropic/__init__.py`
- Create: `providers/sentient-llm-anthropic/src/sentient/llm/anthropic/provider.py`
- Test: `providers/sentient-llm-anthropic/tests/test_provider.py`

- [ ] **Step 1: Write the test**

```python
# providers/sentient-llm-anthropic/tests/test_provider.py
"""Tests for the Anthropic LLM provider."""

import pytest

from sentient.core.protocols import LLMProvider
from sentient.llm.anthropic import AnthropicProvider


def test_protocol_compliance():
    provider = AnthropicProvider()
    assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_query_calls_sdk(monkeypatch):
    """Verify query() calls the Agent SDK with the right args."""
    calls = []

    async def mock_query(prompt, options):
        calls.append({"prompt": prompt, "options": options})
        # Simulate Agent SDK yielding an AssistantMessage then a ResultMessage
        from unittest.mock import MagicMock

        text_block = MagicMock()
        text_block.text = "mocked response"

        msg = MagicMock()
        msg.__class__.__name__ = "AssistantMessage"
        msg.content = [text_block]

        yield msg

    monkeypatch.setattr("sentient.llm.anthropic.provider.sdk_query", mock_query)

    provider = AnthropicProvider()
    result = await provider.query(system="You are helpful.", prompt="Hello")
    assert "mocked response" in result
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest providers/sentient-llm-anthropic/tests/test_provider.py -v
```

- [ ] **Step 3: Create pyproject.toml**

```toml
# providers/sentient-llm-anthropic/pyproject.toml
[project]
name = "sentient-llm-anthropic"
version = "0.1.0"
description = "Anthropic/Claude LLM provider for Sentient agents"
requires-python = ">=3.12"
dependencies = [
    "sentient-core",
    "claude-agent-sdk>=0.1.29",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sentient"]

[project.entry-points."sentient.llm"]
anthropic = "sentient.llm.anthropic:AnthropicProvider"
```

- [ ] **Step 4: Create __init__.py**

```python
# providers/sentient-llm-anthropic/src/sentient/llm/anthropic/__init__.py
"""sentient.llm.anthropic — Anthropic/Claude LLM provider."""

from sentient.llm.anthropic.provider import AnthropicProvider

__all__ = ["AnthropicProvider"]
```

Note: `providers/sentient-llm-anthropic/src/sentient/__init__.py` and `providers/sentient-llm-anthropic/src/sentient/llm/__init__.py` must NOT exist — namespace packages.

- [ ] **Step 5: Create provider.py**

```python
# providers/sentient-llm-anthropic/src/sentient/llm/anthropic/provider.py
"""Anthropic LLM provider using the Claude Agent SDK.

Wraps the claude-agent-sdk's query() function behind the LLMProvider protocol.
Sets CLAUDE_INVOKED_BY to prevent recursion when called from within Claude Code hooks.
"""

from __future__ import annotations

import asyncio
import os


class AnthropicProvider:
    """LLMProvider implementation using the Claude Agent SDK."""

    async def query(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
        """Send a prompt to Claude and return the text response."""
        os.environ["CLAUDE_INVOKED_BY"] = "sentient_llm"

        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
        )
        from claude_agent_sdk import query as sdk_query

        response_parts: list[str] = []

        async for message in sdk_query(
            prompt=f"{system}\n\n{prompt}" if system else prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[],
                max_turns=2,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_parts.append(block.text)

        return "".join(response_parts)

    def query_sync(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
        """Synchronous wrapper around query() for use in non-async contexts."""
        return asyncio.run(self.query(system, prompt, max_tokens))
```

- [ ] **Step 6: Run tests**

```bash
uv sync
uv run pytest providers/sentient-llm-anthropic/tests/test_provider.py -v
```

Expected: Protocol compliance PASS. The SDK mock test may need `pytest-asyncio` — add to workspace dev deps if needed.

- [ ] **Step 7: Commit**

```bash
git add providers/sentient-llm-anthropic/
git commit -m "feat(llm-anthropic): add Anthropic LLM provider via Claude Agent SDK"
```

---

## Task 10: HandoffWriter Refactored to Use LLMProvider

**Files:**
- Create: `packages/sentient-core/src/sentient/core/tools/handoff_writer.py`
- Test: `packages/sentient-core/tests/test_handoff_writer.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-core/tests/test_handoff_writer.py
"""Tests for HandoffWriter — refactored to use LLMProvider protocol."""

import json
import time

import pytest

from sentient.core.tools.handoff_writer import HandoffWriter


class MockLLMProvider:
    """Mock LLM provider for testing."""

    def __init__(self, response: str = "## What We Were Working On\n- Testing"):
        self.response = response
        self.calls: list[dict] = []

    async def query(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
        self.calls.append({"system": system, "prompt": prompt})
        return self.response


@pytest.fixture()
def transcript_file(tmp_path):
    lines = [
        {"message": {"role": "user", "content": "Hello"}},
        {"message": {"role": "assistant", "content": "Hi there!"}},
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(line) for line in lines),
        encoding="utf-8",
    )
    return path


def test_writes_handoff_note(tmp_path, transcript_file):
    output_path = tmp_path / "handoff.md"
    mock_llm = MockLLMProvider()

    writer = HandoffWriter(llm_provider=mock_llm)
    result = writer.execute(
        "PreCompact",
        {"session_id": "test-123", "transcript_path": str(transcript_file)},
        {"output_path": str(output_path), "agent_name": "Pepper"},
    )
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "What We Were Working On" in content
    assert "test-123" in content
    assert len(mock_llm.calls) == 1


def test_deduplication(tmp_path, transcript_file):
    output_path = tmp_path / "handoff.md"
    mock_llm = MockLLMProvider()

    writer = HandoffWriter(llm_provider=mock_llm)
    params = {"output_path": str(output_path), "agent_name": "Pepper"}
    hook_input = {"session_id": "test-123", "transcript_path": str(transcript_file)}

    writer.execute("PreCompact", hook_input, params)
    result = writer.execute("SessionEnd", hook_input, params)
    assert "already written" in result.content
    assert len(mock_llm.calls) == 1  # Only called once


def test_empty_transcript(tmp_path):
    output_path = tmp_path / "handoff.md"
    mock_llm = MockLLMProvider()

    writer = HandoffWriter(llm_provider=mock_llm)
    result = writer.execute(
        "PreCompact",
        {"session_id": "test-456", "transcript_path": "/nonexistent"},
        {"output_path": str(output_path)},
    )
    assert "No transcript" in result.content
    assert len(mock_llm.calls) == 0


def test_handoff_empty_response(tmp_path, transcript_file):
    output_path = tmp_path / "handoff.md"
    mock_llm = MockLLMProvider(response="HANDOFF_EMPTY")

    writer = HandoffWriter(llm_provider=mock_llm)
    result = writer.execute(
        "PreCompact",
        {"session_id": "test-789", "transcript_path": str(transcript_file)},
        {"output_path": str(output_path)},
    )
    assert "No significant content" in result.content


def test_missing_output_path():
    mock_llm = MockLLMProvider()
    writer = HandoffWriter(llm_provider=mock_llm)
    with pytest.raises(ValueError, match="output_path"):
        writer.execute("PreCompact", {}, {})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-core/tests/test_handoff_writer.py -v
```

- [ ] **Step 3: Create handoff_writer.py (refactored to accept LLMProvider)**

```python
# packages/sentient-core/src/sentient/core/tools/handoff_writer.py
"""HandoffWriter — writes structured continuity notes before context is lost.

Refactored from agent_core to use the LLMProvider protocol instead of
hard-coding the Claude Agent SDK. The provider is injected at construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sentient.core.models import ToolResult
from sentient.core.transcript import read_transcript

logger = logging.getLogger("sentient.core.tools.handoff_writer")


def _state_file_for(output_path: Path) -> Path:
    return output_path.parent / "handoff-state.json"


def _load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state), encoding="utf-8")


class HandoffWriter:
    """Writes a structured continuity note before context is lost.

    Args:
        llm_provider: An object implementing the LLMProvider protocol.
            If None, handoff extraction is skipped (writes fallback note).
    """

    def __init__(self, llm_provider=None):
        self.llm_provider = llm_provider

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        output_path_str = params.get("output_path")
        if not output_path_str:
            raise ValueError("Required param 'output_path' is missing")

        output_path = Path(output_path_str)
        tail_lines = params.get("transcript_tail_lines", 200)
        tz_name = params.get("timezone", "US/Eastern")
        agent_name = params.get("agent_name", "Assistant")
        session_id = hook_input.get("session_id", "unknown")
        transcript_path_str = hook_input.get("transcript_path", "")

        # Deduplication
        state_file = _state_file_for(output_path)
        state = _load_state(state_file)
        if (
            state.get("session_id") == session_id
            and time.time() - state.get("timestamp", 0) < 60
        ):
            return ToolResult(
                heading="Handoff Note Written",
                content="Handoff already written for this session.",
            )

        # Timestamp
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("US/Eastern")
        now = datetime.now(timezone.utc).astimezone(tz)
        timestamp = now.strftime("%A, %B %d, %Y %I:%M %p %Z")
        header = (
            f"# Handoff Note\n"
            f"**Written:** {timestamp}\n"
            f"**Session:** {session_id}\n"
            f"**Event:** {event}\n\n"
        )

        # Read transcript
        if transcript_path_str and Path(transcript_path_str).exists():
            transcript_context, turn_count = read_transcript(
                Path(transcript_path_str), max_turns=tail_lines
            )
        else:
            transcript_context = ""
            turn_count = 0

        if not transcript_context.strip():
            content = header + "No transcript available — session ended without accessible transcript.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No transcript available — wrote empty handoff note.",
            )

        # LLM extraction
        if self.llm_provider is None:
            content = header + "No LLM provider configured — handoff extraction skipped.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No LLM provider — wrote fallback note.",
            )

        prompt = f"""You are writing a handoff note for {agent_name} for continuity between sessions.
Write from {agent_name}'s perspective. Based on the conversation transcript
below, write a brief note covering:

## What We Were Working On
[Topics and tasks in progress — a few bullet points]

## Decisions Made
[Any decisions, agreements, or commitments — bullet points]

## Emotional Temperature
[One sentence: how was the conversation going? Casual? Deep work? Tense?]

## Open Threads
[Things started but not finished, unanswered questions — bullet points]

## Observations
[Patterns noticed, hunches worth remembering — bullet points]

Keep each section to 2-5 bullet points. Skip sections with nothing to report.
If the transcript is too short or trivial, respond with: HANDOFF_EMPTY

## Transcript

{transcript_context}"""

        try:
            llm_response = asyncio.run(
                self.llm_provider.query(system="", prompt=prompt)
            )
        except Exception as e:
            logger.error("LLM error during handoff extraction: %s", e)
            llm_response = f"HANDOFF_ERROR: {type(e).__name__}: {e}"

        if "HANDOFF_EMPTY" in llm_response:
            content = header + "No significant content to hand off.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No significant content to hand off.",
            )

        if "HANDOFF_ERROR" in llm_response:
            content = header + "Handoff extraction failed — no continuity note available.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="Handoff extraction failed — wrote fallback note.",
            )

        handoff_content = header + f"{llm_response}\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(handoff_content, encoding="utf-8")
        _save_state(state_file, {"session_id": session_id, "timestamp": time.time()})

        return ToolResult(
            heading="Handoff Note Written",
            content=f"Handoff note saved to {output_path} at {timestamp}.",
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest packages/sentient-core/tests/test_handoff_writer.py -v
```

Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-core/src/sentient/core/tools/handoff_writer.py packages/sentient-core/tests/test_handoff_writer.py
git commit -m "feat(core): add HandoffWriter refactored to use LLMProvider protocol"
```

---

## Task 11: sentient-memory Package Skeleton + Config

**Files:**
- Create: `packages/sentient-memory/pyproject.toml`
- Create: `packages/sentient-memory/src/sentient/memory/__init__.py`
- Create: `packages/sentient-memory/src/sentient/memory/config.py`

> **Note:** sentient-memory is a NEW package modeled after Pepper's vault system. It is NOT a migration of memory-compiler. The memory-compiler stays in agent_core as a project-specific tool.

- [ ] **Step 1: Create pyproject.toml**

```toml
# packages/sentient-memory/pyproject.toml
[project]
name = "sentient-memory"
version = "0.1.0"
description = "Vault and memory system for Sentient AI agents"
requires-python = ">=3.12"
dependencies = [
    "sentient-core",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sentient"]
```

- [ ] **Step 2: Create __init__.py**

```python
# packages/sentient-memory/src/sentient/memory/__init__.py
"""sentient.memory — Vault and memory system for Sentient agents."""

__version__ = "0.1.0"
```

Note: `packages/sentient-memory/src/sentient/__init__.py` must NOT exist — namespace package.

- [ ] **Step 3: Create config.py**

```python
# packages/sentient-memory/src/sentient/memory/config.py
"""Path constants and configuration for the memory system.

All paths are relative to the agent's home directory.
The agent's config.toml specifies vault_path.
"""

from datetime import datetime, timezone
from pathlib import Path


def get_paths(agent_dir: Path | None = None, vault_path: str = "Memory"):
    """Resolve all memory system paths relative to agent_dir.

    Args:
        agent_dir: Agent home directory. Defaults to cwd.
        vault_path: Relative path to vault (from config.toml).

    Returns:
        Dict with all resolved paths.
    """
    if agent_dir is None:
        agent_dir = Path.cwd()

    vault = agent_dir / vault_path

    return {
        "agent_dir": agent_dir,
        "vault": vault,
        "daily": vault / "daily" / "raw",
        "summaries": vault / "daily" / "summaries",
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
```

- [ ] **Step 4: Sync workspace**

```bash
cd /e/workspaces/ai/agents/sentient
uv sync
```

Expected: sentient-memory installs alongside sentient-core.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-memory/
git commit -m "feat(memory): add sentient-memory package skeleton with config"
```

---

## Task 12: sentient-memory Vault I/O

**Files:**
- Create: `packages/sentient-memory/src/sentient/memory/vault.py`
- Test: `packages/sentient-memory/tests/test_vault.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-memory/tests/test_vault.py
"""Tests for vault I/O operations."""

from datetime import datetime, timezone

from sentient.memory.vault import append_to_daily_log, get_recent_log


def test_append_creates_file(tmp_path):
    daily_dir = tmp_path / "daily" / "raw"
    daily_dir.mkdir(parents=True)

    append_to_daily_log(daily_dir, "Test content", section="Session")
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    log_path = daily_dir / f"{today}.md"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "Test content" in content
    assert "Session" in content


def test_append_appends(tmp_path):
    daily_dir = tmp_path / "daily" / "raw"
    daily_dir.mkdir(parents=True)

    append_to_daily_log(daily_dir, "First", section="S1")
    append_to_daily_log(daily_dir, "Second", section="S2")

    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    content = (daily_dir / f"{today}.md").read_text(encoding="utf-8")
    assert "First" in content
    assert "Second" in content


def test_get_recent_log_today(tmp_path):
    daily_dir = tmp_path / "daily" / "raw"
    daily_dir.mkdir(parents=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    (daily_dir / f"{today}.md").write_text("today's log content", encoding="utf-8")

    result = get_recent_log(daily_dir)
    assert "today's log content" in result


def test_get_recent_log_empty(tmp_path):
    daily_dir = tmp_path / "daily" / "raw"
    daily_dir.mkdir(parents=True)
    result = get_recent_log(daily_dir)
    assert "no recent daily log" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-memory/tests/test_vault.py -v
```

- [ ] **Step 3: Create vault.py**

```python
# packages/sentient-memory/src/sentient/memory/vault.py
"""Vault I/O — daily log append and read operations."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_LOG_LINES = 30


def append_to_daily_log(daily_dir: Path, content: str, section: str = "Session") -> None:
    """Append content to today's daily log."""
    today = datetime.now(timezone.utc).astimezone()
    log_path = daily_dir / f"{today.strftime('%Y-%m-%d')}.md"

    if not log_path.exists():
        daily_dir.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"# Daily Log: {today.strftime('%Y-%m-%d')}\n\n",
            encoding="utf-8",
        )

    time_str = today.strftime("%H:%M")
    entry = f"### {section} ({time_str})\n\n{content}\n\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def get_recent_log(daily_dir: Path, max_lines: int = MAX_LOG_LINES) -> str:
    """Read the most recent daily log (today or yesterday)."""
    today = datetime.now(timezone.utc).astimezone()

    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = daily_dir / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            recent = lines[-max_lines:] if len(lines) > max_lines else lines
            return "\n".join(recent)

    return "(no recent daily log)"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest packages/sentient-memory/tests/test_vault.py -v
```

Expected: All 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/sentient-memory/src/sentient/memory/vault.py packages/sentient-memory/tests/test_vault.py
git commit -m "feat(memory): add vault I/O for daily log operations"
```

---

## Task 13: sentient-memory Hook Tools (SessionStart, SessionEnd, PreCompact)

**Files:**
- Create: `packages/sentient-memory/src/sentient/memory/hooks/__init__.py`
- Create: `packages/sentient-memory/src/sentient/memory/hooks/session_start.py`
- Create: `packages/sentient-memory/src/sentient/memory/hooks/session_end.py`
- Create: `packages/sentient-memory/src/sentient/memory/hooks/pre_compact.py`
- Test: `packages/sentient-memory/tests/test_hooks.py`

- [ ] **Step 1: Write the test**

```python
# packages/sentient-memory/tests/test_hooks.py
"""Tests for memory hook tools."""

import json

import pytest

from sentient.core.protocols import HookTool
from sentient.memory.hooks.session_start import KnowledgeIndexInjector


@pytest.fixture()
def memory_dirs(tmp_path):
    """Set up a minimal memory directory structure."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "index.md").write_text(
        "| Article | Summary |\n|---|---|\n| [[concepts/test]] | A test article |",
        encoding="utf-8",
    )
    daily = tmp_path / "Memory" / "daily" / "raw"
    daily.mkdir(parents=True)
    return tmp_path


def test_knowledge_index_injector_protocol():
    assert isinstance(KnowledgeIndexInjector(), HookTool)


def test_knowledge_index_injector(memory_dirs):
    injector = KnowledgeIndexInjector()
    result = injector.execute(
        "SessionStart",
        {},
        {
            "knowledge_path": str(memory_dirs / "knowledge"),
            "vault_path": str(memory_dirs / "Memory"),
        },
    )
    assert "Knowledge Base Index" in result.content
    assert "concepts/test" in result.content


def test_knowledge_index_injector_empty(tmp_path):
    injector = KnowledgeIndexInjector()
    result = injector.execute(
        "SessionStart",
        {},
        {
            "knowledge_path": str(tmp_path / "knowledge"),
            "vault_path": str(tmp_path / "Memory"),
        },
    )
    assert "empty" in result.content.lower() or "no articles" in result.content.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest packages/sentient-memory/tests/test_hooks.py -v
```

- [ ] **Step 3: Create hooks/__init__.py**

```python
# packages/sentient-memory/src/sentient/memory/hooks/__init__.py
"""sentient.memory.hooks — Memory-specific hook tools for lifecycle events."""
```

- [ ] **Step 4: Create session_start.py**

```python
# packages/sentient-memory/src/sentient/memory/hooks/session_start.py
"""KnowledgeIndexInjector — injects knowledge base index into session context.

Implements HookTool protocol. Reads the knowledge base index and recent
daily log, assembles them into context for the agent.
"""

from datetime import datetime, timezone
from pathlib import Path

from sentient.core.models import ToolResult
from sentient.memory.vault import get_recent_log

MAX_CONTEXT_CHARS = 20_000


class KnowledgeIndexInjector:
    """Injects knowledge base index and recent daily log into session context."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        knowledge_path = Path(params.get("knowledge_path", "knowledge"))
        vault_path = Path(params.get("vault_path", "Memory"))
        index_file = knowledge_path / "index.md"
        daily_dir = vault_path / "daily" / "raw"

        parts = []

        # Today's date
        today = datetime.now(timezone.utc).astimezone()
        parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

        # Knowledge base index
        if index_file.exists():
            index_content = index_file.read_text(encoding="utf-8")
            parts.append(f"## Knowledge Base Index\n\n{index_content}")
        else:
            parts.append("## Knowledge Base Index\n\n(empty — no articles compiled yet)")

        # Recent daily log
        recent_log = get_recent_log(daily_dir)
        parts.append(f"## Recent Daily Log\n\n{recent_log}")

        context = "\n\n---\n\n".join(parts)
        if len(context) > MAX_CONTEXT_CHARS:
            context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

        return ToolResult(
            heading="Memory Context",
            content=context,
        )
```

- [ ] **Step 5: Create session_end.py**

```python
# packages/sentient-memory/src/sentient/memory/hooks/session_end.py
"""SessionEndFlusher — captures conversation transcript at session end.

Implements HookTool protocol. Reads the transcript, extracts context,
and spawns a background flush process to extract knowledge.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sentient.core.models import ToolResult
from sentient.core.transcript import read_transcript

logger = logging.getLogger("sentient.memory.hooks.session_end")

MAX_TURNS = 30
MAX_CONTEXT_CHARS = 15_000
MIN_TURNS_TO_FLUSH = 1


class SessionEndFlusher:
    """Captures conversation transcript and spawns background flush."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        # Recursion guard
        if os.environ.get("CLAUDE_INVOKED_BY"):
            return ToolResult(heading="Session End", content="Skipped (recursion guard).")

        session_id = hook_input.get("session_id", "unknown")
        transcript_path_str = hook_input.get("transcript_path", "")

        if not transcript_path_str:
            return ToolResult(heading="Session End", content="No transcript path provided.")

        transcript_path = Path(transcript_path_str)
        if not transcript_path.exists():
            return ToolResult(heading="Session End", content="Transcript file not found.")

        context, turn_count = read_transcript(
            transcript_path, max_turns=MAX_TURNS, max_chars=MAX_CONTEXT_CHARS
        )

        if not context.strip() or turn_count < MIN_TURNS_TO_FLUSH:
            return ToolResult(
                heading="Session End",
                content=f"Skipped — only {turn_count} turns.",
            )

        # Write context to temp file
        state_dir = Path(params.get("state_dir", "."))
        state_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        context_file = state_dir / f"session-flush-{session_id}-{timestamp}.md"
        context_file.write_text(context, encoding="utf-8")

        # Spawn background flush if flush_script is provided
        flush_script = params.get("flush_script")
        if flush_script and Path(flush_script).exists():
            cmd = [sys.executable, flush_script, str(context_file), session_id]
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
            except Exception as e:
                logger.error("Failed to spawn flush: %s", e)

        return ToolResult(
            heading="Session End",
            content=f"Captured {turn_count} turns for session {session_id}.",
        )
```

- [ ] **Step 6: Create pre_compact.py**

```python
# packages/sentient-memory/src/sentient/memory/hooks/pre_compact.py
"""PreCompactCapture — captures transcript before auto-compaction.

Implements HookTool protocol. Same logic as SessionEndFlusher but with
a higher minimum turn threshold (compaction implies meaningful conversation).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sentient.core.models import ToolResult
from sentient.core.transcript import read_transcript

logger = logging.getLogger("sentient.memory.hooks.pre_compact")

MAX_TURNS = 30
MAX_CONTEXT_CHARS = 15_000
MIN_TURNS_TO_FLUSH = 5


class PreCompactCapture:
    """Captures conversation transcript before auto-compaction."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        if os.environ.get("CLAUDE_INVOKED_BY"):
            return ToolResult(heading="Pre-Compact", content="Skipped (recursion guard).")

        session_id = hook_input.get("session_id", "unknown")
        transcript_path_str = hook_input.get("transcript_path", "")

        if not transcript_path_str:
            return ToolResult(heading="Pre-Compact", content="No transcript path provided.")

        transcript_path = Path(transcript_path_str)
        if not transcript_path.exists():
            return ToolResult(heading="Pre-Compact", content="Transcript file not found.")

        context, turn_count = read_transcript(
            transcript_path, max_turns=MAX_TURNS, max_chars=MAX_CONTEXT_CHARS
        )

        if not context.strip() or turn_count < MIN_TURNS_TO_FLUSH:
            return ToolResult(
                heading="Pre-Compact",
                content=f"Skipped — only {turn_count} turns (min {MIN_TURNS_TO_FLUSH}).",
            )

        state_dir = Path(params.get("state_dir", "."))
        state_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        context_file = state_dir / f"flush-context-{session_id}-{timestamp}.md"
        context_file.write_text(context, encoding="utf-8")

        flush_script = params.get("flush_script")
        if flush_script and Path(flush_script).exists():
            cmd = [sys.executable, flush_script, str(context_file), session_id]
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
            except Exception as e:
                logger.error("Failed to spawn flush: %s", e)

        return ToolResult(
            heading="Pre-Compact",
            content=f"Captured {turn_count} turns before compaction for session {session_id}.",
        )
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest packages/sentient-memory/tests/test_hooks.py -v
```

Expected: All 3 PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/sentient-memory/src/sentient/memory/hooks/ packages/sentient-memory/tests/test_hooks.py
git commit -m "feat(memory): add hook tools — KnowledgeIndexInjector, SessionEndFlusher, PreCompactCapture"
```

---

## Task 14: Full Test Suite Pass + Workspace Validation

**Files:**
- No new files — validation task

- [ ] **Step 1: Sync the full workspace**

```bash
cd /e/workspaces/ai/agents/sentient
uv sync
```

Expected: All packages resolve and install.

- [ ] **Step 2: Run the complete test suite**

```bash
uv run pytest -v
```

Expected: All tests PASS across all three packages.

- [ ] **Step 3: Run linting**

```bash
uv run ruff check .
```

Expected: No errors. Fix any that appear.

- [ ] **Step 4: Run formatting**

```bash
uv run ruff format --check .
```

Expected: All files formatted. Fix any that aren't.

- [ ] **Step 5: Verify entrypoint works**

```bash
echo '{}' | uv run sentient-hook hooks run SessionStart --config packages/sentient-core/tests/fixtures/test-config.yaml
```

Create a test fixture config if needed:

```yaml
# packages/sentient-core/tests/fixtures/test-config.yaml
pipelines:
  SessionStart:
    - tool: sentient.core.tools.time_injector.TimeInjector
      params:
        format: "%Y-%m-%d"
```

Expected: JSON output with `hookSpecificOutput.additionalContext` containing today's date.

- [ ] **Step 6: Verify namespace packages work across package boundaries**

```bash
uv run python -c "from sentient.core.models import ToolResult; from sentient.memory.utils import slugify; print('Namespace packages work!')"
```

Expected: Prints "Namespace packages work!" — confirming `sentient.*` namespace is shared.

- [ ] **Step 7: Commit any fixes**

```bash
git add -A
git commit -m "chore: fix lint and formatting issues from full workspace validation"
```

---

## Summary

After completing all 14 tasks, the sentient monorepo has:

- **Workspace root** with uv workspace config
- **sentient-core** (8 modules, ~600 LOC): models, protocols (HookTool, LLMProvider, AuthProvider, AdapterProtocol, ChannelProvider), pipeline engine, transcript reader, config loader, CLI entrypoint, 4 built-in tools
- **sentient-memory** (4 modules, ~200 LOC): config, vault I/O, 3 hook tools (session start, session end, pre-compact). This is a NEW package modeled after Pepper's vault system, not a migration of memory-compiler.
- **sentient-llm-anthropic** (1 module, ~50 LOC): Anthropic provider via Claude Agent SDK with entry point registration
- **Full test suite** covering all packages

Ready for Plan B: Scaffolder (adapter-claude, CLI, templates).
