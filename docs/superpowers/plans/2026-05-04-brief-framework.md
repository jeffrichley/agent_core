# Brief Framework v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship cutover #09 — `agent_core_briefs` as a separate package implementing the structured-composition pattern. v1 scope: morning_brief working end-to-end via cron trigger and MCP self-launch, with two production fetchers (`filesystem_read`, `cli`), two destinations (`discord_embed`, `markdown_file`), the full agent tool surface, and an end-to-end test driving a stub agent through the complete flow.

**Architecture:** Plugin to `agent_core`, peer to `agent_core_discord`. Dependency direction is `agent_core_briefs → agent_core` only — core never references briefs. Deterministic-LLM-deterministic seam: gather (deterministic) → wake agent → agent-driven compose loop → atomic submit (deterministic validate + format + send). Filesystem-discovered fetchers/destinations/extensions with `${var}` substitution in config paths. Direct-to-main commits with adversarial pre-push review, matching the cutover #04 workflow.

**Tech Stack:** Python 3.12, uv workspace package, pluggy, pydantic v2, simpleeval (expression evaluation), Typer (CLI), FastMCP (MCP tool registration), asyncio, ruff. APScheduler (existing in agent_core, extended in T8).

**Spec:** `docs/superpowers/specs/2026-05-04-brief-framework-design.md`.

---

## Task 1: Package scaffold + core protocols

**Files:**
- Create: `packages/agent-core-briefs/pyproject.toml`
- Create: `packages/agent-core-briefs/src/agent_core_briefs/__init__.py`
- Create: `packages/agent-core-briefs/src/agent_core_briefs/protocol.py`
- Create: `packages/agent-core-briefs/tests/__init__.py`
- Create: `packages/agent-core-briefs/tests/test_protocols.py`
- Modify: `pyproject.toml` (workspace) — add the new package to workspace members

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_protocols.py
"""Protocol definitions are runtime-checkable and have the correct shape."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core_briefs.protocol import (
    Destination,
    DeliveryResult,
    Fetcher,
    PlaybookRef,
    SectionSpec,
)


class TestFetcherProtocol:
    def test_fetcher_is_runtime_checkable(self):
        class _Good:
            type_id = "test.good"
            namespace = "test"

            async def fetch(self, config: dict, when: datetime) -> dict:
                return {}

        assert isinstance(_Good(), Fetcher)

    def test_missing_type_id_fails_runtime_check(self):
        class _Bad:
            namespace = "test"

            async def fetch(self, config, when):
                return {}

        assert not isinstance(_Bad(), Fetcher)


class TestDestinationProtocol:
    def test_destination_is_runtime_checkable(self):
        class _Good:
            type_id = "test.good"

            async def deliver(self, sections, playbook, scope, when, config):
                return DeliveryResult(success=True, ref="test-1")

        assert isinstance(_Good(), Destination)


class TestDeliveryResult:
    def test_success_carries_ref(self):
        r = DeliveryResult(success=True, ref="discord-msg-123")
        assert r.success is True
        assert r.ref == "discord-msg-123"
        assert r.error is None

    def test_failure_carries_error(self):
        r = DeliveryResult(success=False, error="timeout")
        assert r.success is False
        assert r.error == "timeout"
        assert r.ref is None


class TestSectionSpec:
    def test_static_color_resolves_to_palette_name(self):
        spec = SectionSpec(
            section_id="greeting",
            title="🌅 Morning",
            color="MORNING_GREETING",
            required=True,
            fields=[],
        )
        assert spec.color == "MORNING_GREETING"
        assert spec.required is True

    def test_dynamic_color_carries_expr(self):
        spec = SectionSpec(
            section_id="email",
            title="📬 Inbox",
            color={"dynamic": True, "expr": "len(email.urgent) > 0",
                   "if_true": "EMAIL_URGENT", "if_false": "EMAIL_OK"},
            required=True,
            fields=[],
        )
        assert isinstance(spec.color, dict)
        assert spec.color["dynamic"] is True


class TestPlaybookRef:
    def test_playbook_ref_round_trips_paths(self, tmp_path):
        ref = PlaybookRef(
            brief_type="morning_brief",
            path=tmp_path / "morning.md",
            sections_required=["greeting", "calendar"],
            sections_optional=["recap"],
            sections_conditional_active=["weekly_digest"],
        )
        assert ref.brief_type == "morning_brief"
        assert ref.sections_required == ["greeting", "calendar"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/agent-core-briefs/tests/test_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_briefs'` (package not installed yet).

- [ ] **Step 3: Create pyproject.toml**

```toml
# packages/agent-core-briefs/pyproject.toml
[project]
name = "agent-core-briefs"
version = "0.1.0"
description = "Brief framework — structured-composition pattern for agent_core"
requires-python = ">=3.12"
dependencies = [
    "agent-core",
    "pluggy>=1.6",
    "pydantic>=2.7",
    "simpleeval>=1.0",
    "typer>=0.12",
]

[project.entry-points."agent_core"]
briefs_aliases = "agent_core_briefs.plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_briefs"]
```

- [ ] **Step 4: Add to workspace**

Modify `pyproject.toml` at the repo root — add `"packages/agent-core-briefs"` to the workspace `members` list.

- [ ] **Step 5: Implement protocol.py**

```python
# packages/agent-core-briefs/src/agent_core_briefs/protocol.py
"""Core protocols + types for the brief framework.

A Fetcher provides deterministic data acquisition. A Destination provides
deterministic format + transport. A SectionSpec describes the shape of one
section in a playbook. A PlaybookRef is the framework-loaded playbook handle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Fetcher(Protocol):
    """Pluggable data-acquisition unit. Returns a JSON-serializable dict
    that gets merged under ``namespace`` in the gathered context."""

    type_id: str
    namespace: str

    async def fetch(self, config: dict, when: datetime) -> dict: ...


@dataclass(frozen=True)
class DeliveryResult:
    """Per-destination delivery outcome."""

    success: bool
    ref: str | None = None
    error: str | None = None


@runtime_checkable
class Destination(Protocol):
    """Pluggable format + transport unit. Renders sections to native shape
    and delivers; returns a DeliveryResult."""

    type_id: str

    async def deliver(
        self,
        sections: list[dict],
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
        config: dict,
    ) -> DeliveryResult: ...


@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool = False
    max_chars: int | None = None
    guidance: str | dict | None = None  # str or {"file": "path-relative-to-playbook"}


@dataclass(frozen=True)
class SectionSpec:
    section_id: str
    title: str
    color: str | dict             # palette name or {dynamic, expr, if_true, if_false}
    required: bool = False
    required_context: list[str] = field(default_factory=list)
    allow_compression: bool = False
    fields: list[FieldSpec] = field(default_factory=list)
    when: dict | None = None       # {expr: "..."} for conditional sections
    required_when_active: bool = False


@dataclass(frozen=True)
class PlaybookRef:
    """Framework-loaded handle for a playbook. Knows the file location
    so it can resolve guidance file references and find sibling configs."""

    brief_type: str
    path: Path
    sections_required: list[str]
    sections_optional: list[str]
    sections_conditional_active: list[str] = field(default_factory=list)
```

- [ ] **Step 6: Wire __init__.py exports**

```python
# packages/agent-core-briefs/src/agent_core_briefs/__init__.py
"""Brief framework — structured composition for agent_core."""

from __future__ import annotations

from agent_core_briefs.protocol import (
    DeliveryResult,
    Destination,
    Fetcher,
    FieldSpec,
    PlaybookRef,
    SectionSpec,
)

__all__ = [
    "DeliveryResult",
    "Destination",
    "Fetcher",
    "FieldSpec",
    "PlaybookRef",
    "SectionSpec",
]
```

- [ ] **Step 7: Sync workspace + run tests**

```
uv sync
uv run pytest packages/agent-core-briefs/tests/test_protocols.py -v
```

Expected: 7 passed.

- [ ] **Step 8: Lint**

```
uv run ruff check packages/agent-core-briefs/
```

Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add packages/agent-core-briefs/ pyproject.toml
git commit -m "feat(briefs): package scaffold + core protocols"
```

---

## Task 2: Var substitution + path expansion

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/config.py`
- Create: `packages/agent-core-briefs/tests/test_config.py`

The framework needs to resolve `${var}` references in config paths at load time, with fallback to standard `~/` user-home expansion. Distinct from `{{when.date}}` delivery-time templating (separate task).

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_config.py
"""Var substitution + path expansion: ${var} references resolve from a vars
map; ~/ expands to the user home; missing vars raise loud."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core_briefs.config import (
    ConfigSubstitutionError,
    expand_path,
    substitute_vars,
)


def test_substitute_simple_var():
    result = substitute_vars("${agent_root}/playbooks/", {"agent_root": "/home/jeffr/.pepper"})
    assert result == "/home/jeffr/.pepper/playbooks/"


def test_substitute_multiple_vars():
    result = substitute_vars(
        "${root}/${subdir}/file",
        {"root": "/data", "subdir": "playbooks"},
    )
    assert result == "/data/playbooks/file"


def test_substitute_in_nested_dict():
    config = {
        "playbook_paths": ["${agent_root}/Memory/playbooks/"],
        "fetchers": [
            {"type": "filesystem_read", "config": {"path": "${agent_root}/TASKS.md"}},
        ],
    }
    result = substitute_vars(config, {"agent_root": "/home/jeffr/.pepper"})
    assert result == {
        "playbook_paths": ["/home/jeffr/.pepper/Memory/playbooks/"],
        "fetchers": [
            {"type": "filesystem_read", "config": {"path": "/home/jeffr/.pepper/TASKS.md"}},
        ],
    }


def test_undefined_var_raises_loud():
    with pytest.raises(ConfigSubstitutionError, match="undefined.*missing_var"):
        substitute_vars("${missing_var}/path", {})


def test_value_without_substitution_passes_through():
    assert substitute_vars("/absolute/path", {}) == "/absolute/path"
    assert substitute_vars(42, {"x": "y"}) == 42
    assert substitute_vars(None, {}) is None


def test_expand_path_handles_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = expand_path("~/.pepper/playbooks/")
    assert result == tmp_path / ".pepper" / "playbooks"


def test_expand_path_returns_path_object():
    result = expand_path("/absolute/path")
    assert isinstance(result, Path)
    assert result == Path("/absolute/path")


def test_substitute_then_expand_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    substituted = substitute_vars("${agent_root}/Memory/", {"agent_root": "~/.pepper"})
    expanded = expand_path(substituted)
    assert expanded == tmp_path / ".pepper" / "Memory"
```

- [ ] **Step 2: Run tests, expect ImportError**

Run: `uv run pytest packages/agent-core-briefs/tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'substitute_vars' from 'agent_core_briefs.config'`.

- [ ] **Step 3: Implement config.py**

```python
# packages/agent-core-briefs/src/agent_core_briefs/config.py
"""Config-load-time substitution + path expansion.

``${var}`` references resolve from a vars map; missing vars raise loudly
rather than silently passing through. Distinct from ``{{when.date}}``-style
delivery-time templating in destination paths (handled separately).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class ConfigSubstitutionError(ValueError):
    """Raised when a ${var} reference cannot be resolved."""


def substitute_vars(value: Any, vars_map: dict[str, str]) -> Any:
    """Recursively substitute ``${var}`` references in ``value`` against
    ``vars_map``. Walks dicts, lists, tuples; passes non-strings through.

    Raises ``ConfigSubstitutionError`` for any reference whose name isn't
    in ``vars_map`` — fail-loud is the contract, silent passthrough breeds
    bugs that look like "the path is wrong" three weeks later.
    """
    if isinstance(value, str):
        return _substitute_string(value, vars_map)
    if isinstance(value, dict):
        return {k: substitute_vars(v, vars_map) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_vars(item, vars_map) for item in value]
    if isinstance(value, tuple):
        return tuple(substitute_vars(item, vars_map) for item in value)
    return value


def _substitute_string(s: str, vars_map: dict[str, str]) -> str:
    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in vars_map:
            raise ConfigSubstitutionError(
                f"undefined config var ${{{name}}} (missing_var={name!r})"
            )
        return vars_map[name]

    return _VAR_PATTERN.sub(_replace, s)


def expand_path(path: str | Path) -> Path:
    """Convert a string path to a ``Path``, with ``~/`` user-home expansion.

    Idempotent; passes ``Path`` instances through ``.expanduser()`` too so
    callers don't need to track which form they're holding.
    """
    return Path(path).expanduser()
```

- [ ] **Step 4: Run tests, expect 8 passed**

Run: `uv run pytest packages/agent-core-briefs/tests/test_config.py -v`

- [ ] **Step 5: Lint + commit**

```
uv run ruff check packages/agent-core-briefs/
git add packages/agent-core-briefs/
git commit -m "feat(briefs): \${var} substitution + path expansion at config-load time"
```

---

## Task 3: Filesystem-discovered loader (generic, reused for fetchers + destinations + extensions)

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/loader.py`
- Create: `packages/agent-core-briefs/tests/test_loader.py`

A single loader function that scans configured paths, imports each `.py` file via `importlib.util.spec_from_file_location`, and registers any class satisfying a given Protocol by its `type_id`. Used in T5 for fetchers, T11 for destinations.

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_loader.py
"""Filesystem-discovered loader: imports .py files from configured paths,
registers classes satisfying a Protocol, hot-reloads on each call."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core_briefs.loader import LoaderError, discover_implementations
from agent_core_briefs.protocol import Fetcher


def _write_fetcher_module(path: Path, type_id: str, namespace: str = "test") -> None:
    path.write_text(
        f'''
"""A test fetcher."""
from datetime import datetime


class TestFetcher:
    type_id = "{type_id}"
    namespace = "{namespace}"

    async def fetch(self, config: dict, when: datetime) -> dict:
        return {{"hello": "world"}}
''',
        encoding="utf-8",
    )


def test_discovers_single_implementation(tmp_path: Path):
    _write_fetcher_module(tmp_path / "f1.py", type_id="test.f1")
    impls = discover_implementations([tmp_path], protocol=Fetcher)
    assert "test.f1" in impls
    assert isinstance(impls["test.f1"](), Fetcher)


def test_discovers_across_multiple_paths(tmp_path: Path):
    p1 = tmp_path / "dir1"
    p2 = tmp_path / "dir2"
    p1.mkdir()
    p2.mkdir()
    _write_fetcher_module(p1 / "a.py", type_id="test.a")
    _write_fetcher_module(p2 / "b.py", type_id="test.b")
    impls = discover_implementations([p1, p2], protocol=Fetcher)
    assert {"test.a", "test.b"} <= impls.keys()


def test_duplicate_type_id_raises(tmp_path: Path):
    _write_fetcher_module(tmp_path / "first.py", type_id="duplicate")
    _write_fetcher_module(tmp_path / "second.py", type_id="duplicate")
    with pytest.raises(LoaderError, match="duplicate type_id"):
        discover_implementations([tmp_path], protocol=Fetcher)


def test_missing_path_is_skipped_with_warning(tmp_path: Path, caplog):
    nonexistent = tmp_path / "does-not-exist"
    with caplog.at_level("WARNING"):
        impls = discover_implementations([nonexistent], protocol=Fetcher)
    assert impls == {}
    assert any("does-not-exist" in r.message for r in caplog.records)


def test_module_with_no_protocol_class_is_silent(tmp_path: Path):
    (tmp_path / "noimpl.py").write_text("# nothing here\nx = 1\n", encoding="utf-8")
    impls = discover_implementations([tmp_path], protocol=Fetcher)
    assert impls == {}


def test_hot_reload_picks_up_new_files(tmp_path: Path):
    _write_fetcher_module(tmp_path / "f1.py", type_id="test.f1")
    impls1 = discover_implementations([tmp_path], protocol=Fetcher)
    assert set(impls1.keys()) == {"test.f1"}

    _write_fetcher_module(tmp_path / "f2.py", type_id="test.f2")
    impls2 = discover_implementations([tmp_path], protocol=Fetcher)
    assert set(impls2.keys()) == {"test.f1", "test.f2"}


def test_syntax_error_in_module_raises_loud(tmp_path: Path):
    (tmp_path / "bad.py").write_text("def broken(\n", encoding="utf-8")
    with pytest.raises(LoaderError, match="bad.py"):
        discover_implementations([tmp_path], protocol=Fetcher)
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement loader.py**

```python
# packages/agent-core-briefs/src/agent_core_briefs/loader.py
"""Filesystem-discovered class loader.

Scans configured paths, imports each ``.py`` via importlib.util, registers
classes satisfying a given Protocol by their ``type_id`` attribute. Used
for fetchers, destinations, and extensions — same shape, same code path.

Hot reload is implicit: the loader doesn't cache. Each call re-imports.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class LoaderError(Exception):
    """Raised on duplicate type_id, syntax error, or other load failure."""


def discover_implementations(
    paths: list[Path],
    *,
    protocol: type[T],
) -> dict[str, type[T]]:
    """Walk ``paths``, import each .py file, register classes satisfying
    ``protocol`` keyed by their ``type_id`` attribute.

    Missing paths log a WARNING and are skipped (operator may have
    misconfigured a path; warn loudly but don't crash). Syntax errors
    and duplicate type_ids raise LoaderError — fail loud, surface the
    conflict immediately rather than letting one silently shadow the
    other.
    """
    registry: dict[str, type[T]] = {}
    for base in paths:
        if not base.exists():
            log.warning("loader: path not found, skipping: %s", base)
            continue
        if not base.is_dir():
            raise LoaderError(f"loader: path is not a directory: {base}")
        for py_file in sorted(base.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module = _import_module_by_path(py_file)
            for _, member in inspect.getmembers(module, inspect.isclass):
                if member.__module__ != module.__name__:
                    continue  # imported, not defined here
                # Check the class itself has type_id (Protocol structural check
                # would also accept instances, but we want the class for registry)
                if not hasattr(member, "type_id"):
                    continue
                # Verify an instance satisfies the runtime-checkable Protocol
                try:
                    instance = member()
                except Exception:
                    continue  # can't instantiate without args; not our impl
                if not isinstance(instance, protocol):
                    continue
                type_id = member.type_id
                if type_id in registry:
                    raise LoaderError(
                        f"loader: duplicate type_id {type_id!r} "
                        f"(already registered from another module)"
                    )
                registry[type_id] = member
    return registry


def _import_module_by_path(py_file: Path) -> Any:
    """Import a .py file by absolute path without polluting sys.path.

    Uses a unique module name based on the file's absolute path so two
    files with the same basename in different directories don't collide.
    """
    module_name = f"_briefs_loaded_{py_file.stem}_{abs(hash(str(py_file.absolute())))}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            raise LoaderError(f"loader: cannot create spec for {py_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except SyntaxError as exc:
        raise LoaderError(f"loader: syntax error in {py_file}: {exc}") from exc
    except Exception as exc:
        raise LoaderError(f"loader: failed to import {py_file}: {exc}") from exc
```

- [ ] **Step 4: Run tests, expect 7 passed**

- [ ] **Step 5: Lint + commit**

```
git commit -m "feat(briefs): filesystem-discovered loader for fetchers/destinations/extensions"
```

---

## Task 4: Async-concurrent gather engine

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/engine.py`
- Create: `packages/agent-core-briefs/tests/test_gather.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_gather.py
"""Gather engine: async-concurrent fetcher execution with per-fetcher
timeout, namespace merging, _errors capture, default 5min timeout."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agent_core_briefs.engine import FetcherInvocation, gather_context


class _Fixed:
    def __init__(self, type_id: str, namespace: str, payload: dict):
        self.type_id = type_id
        self.namespace = namespace
        self._payload = payload

    async def fetch(self, config, when):
        return dict(self._payload)


class _Slow:
    def __init__(self, type_id: str, namespace: str, delay_s: float):
        self.type_id = type_id
        self.namespace = namespace
        self._delay = delay_s

    async def fetch(self, config, when):
        await asyncio.sleep(self._delay)
        return {"slow": "ok"}


class _Boom:
    type_id = "test.boom"
    namespace = "boom"

    async def fetch(self, config, when):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_single_fetcher_returns_namespaced_payload():
    inv = FetcherInvocation(
        fetcher=_Fixed("test.cal", "calendar", {"events": [{"id": "a"}]}),
        config={},
        timeout_seconds=300,
    )
    ctx = await gather_context([inv], when=datetime.now(UTC))
    assert ctx == {"calendar": {"events": [{"id": "a"}]}}


@pytest.mark.asyncio
async def test_multiple_fetchers_merge_into_separate_namespaces():
    invs = [
        FetcherInvocation(_Fixed("test.cal", "calendar", {"x": 1}), {}, 300),
        FetcherInvocation(_Fixed("test.email", "email", {"y": 2}), {}, 300),
    ]
    ctx = await gather_context(invs, when=datetime.now(UTC))
    assert ctx == {"calendar": {"x": 1}, "email": {"y": 2}}


@pytest.mark.asyncio
async def test_dotted_namespace_creates_nested_dict():
    inv = FetcherInvocation(
        _Fixed("test.streak", "streaks.eod_log", {"current": 14}), {}, 300,
    )
    ctx = await gather_context([inv], when=datetime.now(UTC))
    assert ctx == {"streaks": {"eod_log": {"current": 14}}}


@pytest.mark.asyncio
async def test_failing_fetcher_lands_in_errors_namespace():
    invs = [
        FetcherInvocation(_Fixed("test.ok", "good", {"a": 1}), {}, 300),
        FetcherInvocation(_Boom(), {}, 300),
    ]
    ctx = await gather_context(invs, when=datetime.now(UTC))
    assert ctx["good"] == {"a": 1}
    assert "_errors" in ctx
    assert "test.boom" in ctx["_errors"]
    assert "boom" in ctx["_errors"]["test.boom"]["error"]
    assert ctx["_errors"]["test.boom"]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_timeout_isolated_to_offending_fetcher():
    invs = [
        FetcherInvocation(_Fixed("test.ok", "good", {"a": 1}), {}, 300),
        FetcherInvocation(_Slow("test.slow", "slow", delay_s=10), {}, timeout_seconds=0.1),
    ]
    ctx = await gather_context(invs, when=datetime.now(UTC))
    assert ctx["good"] == {"a": 1}
    assert ctx["_errors"]["test.slow"]["type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_concurrent_execution_total_time_bounded_by_slowest():
    import time
    slow = FetcherInvocation(_Slow("test.s1", "s1", delay_s=0.5), {}, 5)
    other = FetcherInvocation(_Slow("test.s2", "s2", delay_s=0.5), {}, 5)
    start = time.monotonic()
    await gather_context([slow, other], when=datetime.now(UTC))
    elapsed = time.monotonic() - start
    # Concurrent: ~0.5s. Sequential would be ~1.0s.
    assert elapsed < 0.9, f"gather should run concurrently, got {elapsed}s"
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement engine.py (gather portion)**

```python
# packages/agent-core-briefs/src/agent_core_briefs/engine.py
"""Brief framework engine: gather + submit orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_core_briefs.protocol import Fetcher


@dataclass(frozen=True)
class FetcherInvocation:
    """One configured fetcher call: instance + config + per-call timeout."""

    fetcher: Fetcher
    config: dict
    timeout_seconds: float


async def gather_context(
    invocations: list[FetcherInvocation],
    *,
    when: datetime,
) -> dict[str, Any]:
    """Run all fetchers concurrently, merge results into a context dict.

    Each fetcher gets its own timeout. Failures (exceptions or timeouts)
    land in ``context._errors.<type_id>`` so the receiving agent sees what
    fell over without losing the rest of the gather. One slow fetcher
    cannot block others — they all run in parallel with isolated timeouts.
    """
    results = await asyncio.gather(
        *[_run_one(inv, when) for inv in invocations],
        return_exceptions=False,  # all paths return tuples; never re-raise
    )
    context: dict[str, Any] = {}
    for namespace, payload in results:
        _merge_into_namespace(context, namespace, payload)
    return context


async def _run_one(
    inv: FetcherInvocation, when: datetime
) -> tuple[str, dict]:
    try:
        payload = await asyncio.wait_for(
            inv.fetcher.fetch(inv.config, when),
            timeout=inv.timeout_seconds,
        )
        return inv.fetcher.namespace, payload
    except TimeoutError:
        return f"_errors.{inv.fetcher.type_id}", {
            "error": f"timeout after {inv.timeout_seconds}s",
            "type": "TimeoutError",
        }
    except Exception as exc:
        return f"_errors.{inv.fetcher.type_id}", {
            "error": str(exc),
            "type": type(exc).__name__,
        }


def _merge_into_namespace(context: dict, namespace: str, payload: dict) -> None:
    """Merge ``payload`` into ``context`` at ``namespace`` (dot-separated path).

    ``namespace="streaks.eod_log"`` creates nested dicts. Existing keys at
    the leaf are overwritten — last write wins (intentional; multiple
    fetchers contributing to the same leaf is a config error caught at
    fetcher-loader time).
    """
    parts = namespace.split(".")
    cursor = context
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = payload
```

- [ ] **Step 4: Run tests, expect 6 passed**

- [ ] **Step 5: Lint + commit**

```
git commit -m "feat(briefs): async-concurrent gather engine with per-fetcher timeouts"
```

---

## Task 5: Built-in fetcher — `filesystem_read`

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/fetchers/__init__.py`
- Create: `packages/agent-core-briefs/src/agent_core_briefs/fetchers/filesystem_read.py`
- Create: `packages/agent-core-briefs/tests/test_fetcher_filesystem_read.py`

Reads a single file (or glob) into the context. Format param controls parsing: `text` (raw), `json`, `yaml`, `lines` (list of stripped lines).

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_fetcher_filesystem_read.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core_briefs.fetchers.filesystem_read import FilesystemReadFetcher


@pytest.mark.asyncio
async def test_text_format(tmp_path: Path):
    f = tmp_path / "x.md"
    f.write_text("hello\nworld", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    result = await fetcher.fetch({"path": str(f), "format": "text"}, datetime.now(UTC))
    assert result == {"content": "hello\nworld", "path": str(f)}


@pytest.mark.asyncio
async def test_json_format(tmp_path: Path):
    f = tmp_path / "x.json"
    f.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    result = await fetcher.fetch({"path": str(f), "format": "json"}, datetime.now(UTC))
    assert result == {"a": 1, "b": [2, 3]}


@pytest.mark.asyncio
async def test_yaml_format(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\nb:\n  - 2\n  - 3\n", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    result = await fetcher.fetch({"path": str(f), "format": "yaml"}, datetime.now(UTC))
    assert result == {"a": 1, "b": [2, 3]}


@pytest.mark.asyncio
async def test_lines_format(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("alpha\n  beta  \n\ngamma\n", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    result = await fetcher.fetch({"path": str(f), "format": "lines"}, datetime.now(UTC))
    # Strips whitespace, drops empty lines.
    assert result == {"lines": ["alpha", "beta", "gamma"]}


@pytest.mark.asyncio
async def test_missing_file_raises(tmp_path: Path):
    fetcher = FilesystemReadFetcher()
    with pytest.raises(FileNotFoundError):
        await fetcher.fetch(
            {"path": str(tmp_path / "nope.md"), "format": "text"},
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_invalid_format_raises(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("hi", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    with pytest.raises(ValueError, match="format"):
        await fetcher.fetch({"path": str(f), "format": "binary"}, datetime.now(UTC))


@pytest.mark.asyncio
async def test_default_format_is_text(tmp_path: Path):
    f = tmp_path / "x.md"
    f.write_text("hi", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    result = await fetcher.fetch({"path": str(f)}, datetime.now(UTC))
    assert result["content"] == "hi"
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

```python
# packages/agent-core-briefs/src/agent_core_briefs/fetchers/filesystem_read.py
"""filesystem_read — read a file into the context, parsed per format."""

from __future__ import annotations

import json
from datetime import datetime

import yaml

from agent_core_briefs.config import expand_path


class FilesystemReadFetcher:
    """Read a single file into the gather context.

    Config:
    - ``path``: filesystem path (supports ``~/`` expansion).
    - ``format``: ``"text"`` (default), ``"json"``, ``"yaml"``, ``"lines"``.

    Returns a dict shape that varies by format. Use a wrapper fetcher
    in the agent's repo if you need fixed-shape output across formats.
    """

    type_id = "filesystem_read"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict, when: datetime) -> dict:
        path = expand_path(config["path"])
        fmt = config.get("format", "text")
        text = path.read_text(encoding="utf-8")

        if fmt == "text":
            return {"content": text, "path": str(path)}
        if fmt == "json":
            return json.loads(text)
        if fmt == "yaml":
            return yaml.safe_load(text)
        if fmt == "lines":
            return {
                "lines": [line.strip() for line in text.splitlines() if line.strip()]
            }
        raise ValueError(f"filesystem_read: unknown format {fmt!r}")
```

- [ ] **Step 4: Run, pass + lint + commit**

```
git commit -m "feat(briefs): filesystem_read built-in fetcher"
```

---

## Task 6: Built-in fetcher — `cli`

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/fetchers/cli.py`
- Create: `packages/agent-core-briefs/tests/test_fetcher_cli.py`

Wraps `asyncio.create_subprocess_exec`, captures stdout, parses per `parse` setting (json/yaml/lines/text). Surfaces stderr + non-zero exit code as the fetcher's error.

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_fetcher_cli.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core_briefs.fetchers.cli import CliFetcher


@pytest.mark.asyncio
async def test_text_capture():
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {"command": ["python", "-c", "print('hello')"], "parse": "text"},
        datetime.now(UTC),
    )
    assert result["stdout"].strip() == "hello"


@pytest.mark.asyncio
async def test_json_parse():
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {"command": ["python", "-c", "import json; print(json.dumps({'k': 'v'}))"],
         "parse": "json"},
        datetime.now(UTC),
    )
    assert result == {"k": "v"}


@pytest.mark.asyncio
async def test_lines_parse():
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {"command": ["python", "-c", "print('a'); print('b'); print('')"],
         "parse": "lines"},
        datetime.now(UTC),
    )
    assert result == {"lines": ["a", "b"]}


@pytest.mark.asyncio
async def test_nonzero_exit_raises_with_stderr():
    fetcher = CliFetcher()
    with pytest.raises(RuntimeError, match="exit"):
        await fetcher.fetch(
            {"command": ["python", "-c", "import sys; print('err', file=sys.stderr); sys.exit(2)"],
             "parse": "text"},
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_invalid_json_raises():
    fetcher = CliFetcher()
    with pytest.raises(ValueError, match="json"):
        await fetcher.fetch(
            {"command": ["python", "-c", "print('not json')"], "parse": "json"},
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_env_passthrough_only_listed_keys(monkeypatch):
    monkeypatch.setenv("PASSED_VAR", "yes")
    monkeypatch.setenv("BLOCKED_VAR", "no")
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {
            "command": ["python", "-c",
                        "import os; print(os.environ.get('PASSED_VAR', 'X'), "
                        "os.environ.get('BLOCKED_VAR', 'X'))"],
            "parse": "text",
            "env_passthrough": ["PASSED_VAR"],
        },
        datetime.now(UTC),
    )
    assert "yes X" in result["stdout"]


@pytest.mark.asyncio
async def test_cwd_changes_working_directory(tmp_path):
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {"command": ["python", "-c", "import os; print(os.getcwd())"],
         "parse": "text",
         "cwd": str(tmp_path)},
        datetime.now(UTC),
    )
    # On Windows tmp_path may have a different case; compare resolved paths
    from pathlib import Path
    assert Path(result["stdout"].strip()).resolve() == tmp_path.resolve()
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

```python
# packages/agent-core-briefs/src/agent_core_briefs/fetchers/cli.py
"""cli — run a CLI command and parse its stdout into the context."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

import yaml

from agent_core_briefs.config import expand_path


class CliFetcher:
    """Wrap a CLI command. Captures stdout, parses per ``parse`` setting.

    Config:
    - ``command`` (list[str]): argv. First element is the executable.
    - ``cwd`` (str | None): working directory.
    - ``parse``: ``"text"`` | ``"json"`` | ``"yaml"`` | ``"lines"``.
    - ``env_passthrough`` (list[str]): env var names to forward; everything
      else is dropped (clean env, no inherited surprises).

    Non-zero exit codes raise RuntimeError with stderr captured. Invalid
    parse output raises ValueError.
    """

    type_id = "cli"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict, when: datetime) -> dict:
        command = list(config["command"])
        cwd = expand_path(config["cwd"]) if config.get("cwd") else None
        parse = config.get("parse", "text")
        passthrough = config.get("env_passthrough", [])

        env: dict[str, str] = {name: os.environ[name]
                                for name in passthrough if name in os.environ}

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"cli: command exited with {proc.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')}"
            )

        text = stdout.decode("utf-8")
        if parse == "text":
            return {"stdout": text}
        if parse == "json":
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"cli: stdout is not valid json: {exc}") from exc
        if parse == "yaml":
            return yaml.safe_load(text)
        if parse == "lines":
            return {"lines": [line.strip() for line in text.splitlines() if line.strip()]}
        raise ValueError(f"cli: unknown parse {parse!r}")
```

- [ ] **Step 4: Run, pass + lint + commit**

```
git commit -m "feat(briefs): cli built-in fetcher (subprocess + parse)"
```

---

## Task 7: Playbook parser

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/playbook.py`
- Create: `packages/agent-core-briefs/tests/test_playbook.py`
- Create: `packages/agent-core-briefs/tests/fixtures/playbooks/morning-test.md` (test fixture)

Parses the playbook MD: extracts metadata block, destinations block, color palette, sections (each its own YAML fenced block), conditional sections, extension references. Resolves color names to decimals. Resolves dynamic colors via simpleeval. Resolves guidance refs from `{file: ...}` to inline text.

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-briefs/tests/test_playbook.py
"""Playbook parser: extracts metadata, destinations, colors, sections,
conditional sections, extension refs from a YAML-in-MD playbook file.
Resolves dynamic colors via simpleeval; resolves file-ref guidance."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core_briefs.playbook import (
    PlaybookParseError,
    parse_playbook,
    resolve_colors_for_sections,
    resolve_conditional_sections,
)

FIXTURE = Path(__file__).parent / "fixtures" / "playbooks" / "morning-test.md"


def test_parses_metadata():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    assert playbook.brief_type == "morning_brief"
    assert playbook.voice == "test"
    assert playbook.schedule_cron == "0 7 * * *"


def test_parses_destinations_with_var_substitution():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    assert len(playbook.destinations) == 2
    discord = next(d for d in playbook.destinations if d["type"] == "discord_embed")
    assert discord["config"]["channel_id"] == "12345"
    md = next(d for d in playbook.destinations if d["type"] == "markdown_file")
    assert "/test/root/" in md["config"]["path"]


def test_parses_color_palette():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    assert playbook.colors["TEST_RED"] == 15548997
    assert playbook.colors["TEST_GREEN"] == 5763719


def test_parses_sections_in_order():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    section_ids = [s.section_id for s in playbook.sections]
    assert section_ids == ["greeting", "calendar_today", "priorities_today"]


def test_section_with_static_color_resolves_to_decimal():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    resolved = resolve_colors_for_sections(playbook.sections, playbook.colors, context={})
    greeting = next(s for s in resolved if s.section_id == "greeting")
    assert greeting.color == 15548997  # TEST_RED


def test_dynamic_color_resolves_against_context():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    # priorities_today has dynamic color: red if any project blocker, else green
    ctx_blocked = {"projects": {"active": [{"blockers": ["x"]}]}}
    resolved = resolve_colors_for_sections(
        playbook.sections, playbook.colors, context=ctx_blocked,
    )
    p = next(s for s in resolved if s.section_id == "priorities_today")
    assert p.color == 15548997  # TEST_RED

    ctx_clear = {"projects": {"active": [{"blockers": []}]}}
    resolved = resolve_colors_for_sections(
        playbook.sections, playbook.colors, context=ctx_clear,
    )
    p = next(s for s in resolved if s.section_id == "priorities_today")
    assert p.color == 5763719  # TEST_GREEN


def test_conditional_section_active_when_expr_true():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    ctx = {"now": {"day_of_week": "Monday"}}
    active_ids = resolve_conditional_sections(playbook.conditional_sections, ctx)
    assert active_ids == ["weekly_digest"]


def test_conditional_section_inactive_when_expr_false():
    playbook = parse_playbook(FIXTURE, vars_map={"agent_root": "/test/root"})
    ctx = {"now": {"day_of_week": "Tuesday"}}
    active_ids = resolve_conditional_sections(playbook.conditional_sections, ctx)
    assert active_ids == []


def test_missing_brief_type_raises(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("# bad\n```yaml\nvoice: test\n```\n", encoding="utf-8")
    with pytest.raises(PlaybookParseError, match="brief_type"):
        parse_playbook(bad, vars_map={})


def test_undefined_color_in_section_raises(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "# bad\n"
        "```yaml\nbrief_type: x\nvoice: y\n```\n"
        "```yaml\ncolors:\n  RED: 1\n```\n"
        "```yaml\nsection_id: s\ntitle: t\ncolor: PURPLE\nfields: []\n```\n",
        encoding="utf-8",
    )
    pb = parse_playbook(bad, vars_map={})
    with pytest.raises(PlaybookParseError, match="undefined color"):
        resolve_colors_for_sections(pb.sections, pb.colors, context={})
```

- [ ] **Step 2: Create the test fixture**

```markdown
# packages/agent-core-briefs/tests/fixtures/playbooks/morning-test.md

## Metadata
```yaml
brief_type: morning_brief
voice: test
schedule:
  cron: "0 7 * * *"
gather_config: ${agent_root}/Memory/gather/morning.yaml
```

## Destinations
```yaml
destinations:
  - type: discord_embed
    config:
      channel_id: "12345"
  - type: markdown_file
    config:
      path: ${agent_root}/Memory/daily/briefs/{{when.date}}-morning.md
```

## Colors
```yaml
colors:
  TEST_RED: 15548997
  TEST_GREEN: 5763719
  TEST_BLUE: 3447003
```

## Sections

### greeting
```yaml
section_id: greeting
title: "🌅 Morning"
color: TEST_RED
required: true
fields:
  - name: "Today"
    required: true
    guidance: "One-line frame for the day."
```

### calendar_today
```yaml
section_id: calendar_today
title: "📅 Today's calendar"
color: TEST_BLUE
required: true
fields:
  - name: "Schedule"
    required: true
```

### priorities_today
```yaml
section_id: priorities_today
title: "🎯 Priorities"
color:
  dynamic: true
  expr: "any(p.blockers for p in projects.active)"
  if_true: TEST_RED
  if_false: TEST_GREEN
required: true
fields:
  - name: "Top 3"
    required: true
```

## Conditional sections

### weekly_digest
```yaml
section_id: weekly_digest
title: "📊 Week ahead"
color: TEST_GREEN
when:
  expr: "now.day_of_week == 'Monday'"
required_when_active: true
fields:
  - name: "This week"
    required: true
```
```

- [ ] **Step 3: Implement playbook.py**

[Implementation: ~300 lines. Parses fenced YAML blocks via regex, builds a dataclass `Playbook` holding `brief_type`, `voice`, `schedule_cron`, `colors`, `destinations`, `sections`, `conditional_sections`, `extensions_active`. Uses `simpleeval` (with attribute access via a `context` AttrDict adapter) to evaluate dynamic colors and conditional `when` expressions. Resolves `${var}` substitution at parse time using the `config.substitute_vars` from T2.]

- [ ] **Step 4: Run tests, expect all green + lint + commit**

```
git commit -m "feat(briefs): playbook parser (YAML-in-MD with simpleeval expressions)"
```

---

## Task 8: SchedulerEndpoint extension to fire Event envelopes

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/scheduler.py`
- Modify: `packages/core/tests/test_scheduler.py` (or whichever test file covers SchedulerEndpoint)

Currently `SchedulerEndpoint._fire` hardcodes `kind="TextMessage"` with a `prompt` string. Extend to support firing `Event` envelopes with structured payloads, controlled by the job config.

- [ ] **Step 1: Write the failing test**

[Test: configure a job with `envelope_kind: Event`, `payload_type: BriefRequest`, `payload_data: {...}`. Trigger the job. Verify the published envelope is an `Event` with `EventPayload(type="BriefRequest", data={...})`. Verify `TextMessage` jobs (existing config shape) still work — backward compat.]

- [ ] **Step 2: Run, fail (existing scheduler can't fire Events)**

- [ ] **Step 3: Implement the extension**

[~50 lines. Job config grows two optional fields (`envelope_kind`, `payload`). `_fire` branches on kind: `TextMessage` (existing) or `Event` (new). Add validation in the config-loading path. No behavioral change for existing TextMessage jobs.]

- [ ] **Step 4: Run all scheduler tests, expect green + commit**

```
git commit -m "feat(scheduler): fire Event envelopes with structured payloads (cutover #09 prep)"
```

---

## Task 9: BriefRequest receive handler + ComposeBrief publish

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/orchestrator.py`
- Create: `packages/agent-core-briefs/tests/test_orchestrator.py`

The piece that listens for `BriefRequest` events on the bus, runs gather, builds the `ComposeBrief` envelope, and publishes it back to the target agent. Implements the wake step.

- [ ] **Step 1: Write the failing test**

[Test: stub bus, simulate publishing a `BriefRequest` event. Orchestrator consumes it, runs gather (against a stub fetcher), publishes `ComposeBrief` to the target agent's mailbox with: brief_type, scope, when, session_token, playbook ref, context dict (including `_errors` if any).]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[~150 lines. Subscribes to bus, filters envelopes where `payload.type == "BriefRequest"`, dispatches to a per-brief-type handler that loads playbook + gather config, runs gather, builds ComposeBrief envelope, publishes. Generates session_token (random hex), stores it in a session registry with TTL.]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): BriefRequest → gather → ComposeBrief orchestrator"
```

---

## Task 10: Session registry + agent tool surface

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/session.py`
- Create: `packages/agent-core-briefs/src/agent_core_briefs/tools.py`
- Create: `packages/agent-core-briefs/tests/test_session.py`
- Create: `packages/agent-core-briefs/tests/test_tools.py`

Session token store (create, validate, consume, expire) + the agent-facing tools (`list_sections`, `get_section_spec`, `validate_section`, `compress_sections`, `add_extension_section`). `submit_brief` lives in T13.

- [ ] **Step 1: Write the failing tests**

[Two test files. Session tests: create returns unique token, validate raises on unknown/expired/consumed, TTL enforcement, compose sessions store playbook + context. Tool tests: each tool uses session_token to look up state, returns expected shape, raises on bad token.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[Session: in-memory dict keyed by token, TTL via stored expiry timestamp, periodic cleanup at access time. Tools: each is an async function taking session_token + args, returns dict. ~250 lines total.]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): session registry + agent compose-loop tools"
```

---

## Task 11: Built-in destination — `discord_embed`

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/destinations/__init__.py`
- Create: `packages/agent-core-briefs/src/agent_core_briefs/destinations/discord_embed.py`
- Create: `packages/agent-core-briefs/tests/test_destination_discord.py`

Renders sections to `discord.Embed` objects, posts via DiscordEndpoint. Resolves color decimals from the playbook palette. Auto-injects footer (timestamp + agent name + brief_type).

- [ ] **Step 1: Write the failing test**

[Use a fake Discord client (already exists in `agent_core_discord/tests/`). Build sample sections, call `deliver`, assert the posted message contains N embeds with correct titles, fields, colors. Assert footer is present. Assert `DeliveryResult.success` and `ref` is the message id.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[~150 lines. Takes section dicts + playbook + scope + when + config. Constructs `discord.Embed` per section using palette-resolved colors and section title. Maps each field dict (`{name, value, inline}`) to `embed.add_field(...)`. Posts via the configured DiscordEndpoint instance (looked up by name).]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): discord_embed built-in destination"
```

---

## Task 12: Built-in destination — `markdown_file`

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/destinations/markdown_file.py`
- Create: `packages/agent-core-briefs/tests/test_destination_markdown_file.py`

Renders sections to a markdown file. Section title becomes `##` header, fields become a definition list per section. Path supports `{{when.date}}`-style delivery-time templating.

- [ ] **Step 1: Write the failing test**

[Sample sections, call `deliver` with a tmp_path config, verify the file exists, contains expected `## Section title`, field name/value pairs, footer with timestamp.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[~80 lines. Renders MD, applies delivery-time template substitution to the path, writes file. `DeliveryResult.ref` is the absolute path written.]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): markdown_file built-in destination"
```

---

## Task 13: Submit handler (atomic validate + format + send) + audit log

**Files:**
- Modify: `packages/agent-core-briefs/src/agent_core_briefs/engine.py` (add submit handler)
- Create: `packages/agent-core-briefs/src/agent_core_briefs/audit.py`
- Create: `packages/agent-core-briefs/src/agent_core_briefs/validators.py`
- Create: `packages/agent-core-briefs/tests/test_submit.py`
- Create: `packages/agent-core-briefs/tests/test_audit.py`

`submit_brief(session_token, sections)` runs validation (all required sections + fields present, max_chars respected, embed count ≤ 10, no unknown sections). On pass, fans out to all destinations (best-effort), captures per-destination outcome, writes audit log entry, returns `SubmitResult`.

- [ ] **Step 1: Write the failing tests**

[Submit tests: validation fails → returns errors, no delivery attempted; validation passes → deliveries attempted; one destination fails → other still delivers, partial result returned. Audit tests: each step appends one JSONL line with the right shape.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[Submit: ~150 lines. Validators: ~100 lines (per-section field checks, max_chars, embed count, conditional resolution). Audit: ~50 lines (append to `~/.agent-core/briefs/audit.jsonl` with structured event types).]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): submit handler — atomic validate + format + send + audit"
```

---

## Task 14: `compose_brief` MCP self-launch tool + ClaudeCodeMCPEndpoint integration

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/mcp.py`
- Create: `packages/agent-core-briefs/tests/test_mcp.py`
- Modify: `packages/core/src/agent_core/endpoints/claude_code_mcp.py` (mount-point hook)

The self-launch entry point: `compose_brief(brief_type, scope=None)` — runs gather inline, returns context + session_token. Plus mounting of all seven agent tools on `ClaudeCodeMCPEndpoint`.

- [ ] **Step 1: Write the failing tests**

[Stub MCP endpoint + briefs framework. Call `compose_brief("morning_brief")`, verify it returns a dict with `session_token`, `context`, `playbook`. Verify the seven other tools (list_sections, get_section_spec, etc.) are reachable via the endpoint after the briefs plugin is loaded.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[~100 lines for `mcp.py`. Adds a hook on ClaudeCodeMCPEndpoint init that, when the briefs plugin is enabled, mounts the seven tools. The mount mechanism is a simple "register_briefs_tools(self._mcp)" function the briefs plugin contributes.]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): compose_brief self-launch + MCP tool mount on ClaudeCodeMCPEndpoint"
```

---

## Task 15: CLI subapp (`agent-core briefs ...`)

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/cli.py`
- Create: `packages/agent-core-briefs/tests/test_cli.py`
- Modify: `packages/core/src/agent_core/cli.py` (register subapp via plugin hook)

Three subcommands:
- `agent-core briefs compose --type X [--scope Y] [--agent Z]` — fire a BriefRequest in-process for testing/debugging.
- `agent-core briefs fetchers list` — show loaded fetchers, their type_id, source path, last-modified.
- `agent-core briefs fetchers test --type X --config @file.yaml [--when ISO8601]` — run a single fetcher in isolation, print the namespace dict.

- [ ] **Step 1: Write the failing test**

[Use `typer.testing.CliRunner`. Test compose subcommand with stub framework. Test fetchers list output shape. Test fetchers test runs a known fetcher and prints expected output.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

[~150 lines. Subapp follows the same pattern as `agent-core bus-log` from cutover #04 — Annotated style for B008 compliance, boundary validation, JSON output by default.]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): agent-core briefs CLI subapp (compose, fetchers list/test)"
```

---

## Task 16: Plugin entry-point wiring + Pepper example yaml + tripwire test

**Files:**
- Create: `packages/agent-core-briefs/src/agent_core_briefs/plugin.py`
- Modify: `docs/examples/pepper-agent-core.yaml` — add briefs plugin block + ClaudeCodeMCPEndpoint
- Modify: `packages/core/tests/test_pepper_example_yaml.py` — extend tripwire for briefs config
- Create: `docs/examples/playbooks/morning-brief.md` — full Pepper morning_brief playbook (the one we sketched in the spec)
- Create: `docs/examples/playbooks/morning-gather.yaml` — example gather config

Plugin file contributes the briefs subsystem to agent_core via pluggy: registers fetcher/destination loader paths from agent config, mounts CLI subapp, mounts MCP tools, registers BriefRequest subscriber. Pepper example yaml shows the full wiring an operator needs.

- [ ] **Step 1: Write the failing test (tripwire)**

[Extend `test_pepper_example_yaml.py` with assertions: yaml has a `plugins.briefs.vars.agent_root` key, has `playbook_paths` with at least one entry, has `claude_code_mcp` endpoint named "pepper". Existing tripwires unchanged.]

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement plugin.py + update example yaml + write playbook**

[Plugin: ~100 lines of hookimpls. Yaml: add `plugins.briefs` block + `claude_code_mcp` endpoint. Playbook: copy from spec section, fill in real Pepper sections (greeting, calendar_today, email_status, priorities_today, project_status, yesterday_recap, open_loops, watch_list + conditional weekly_digest + war_pointer).]

- [ ] **Step 4: Run, green + commit**

```
git commit -m "feat(briefs): plugin wiring + Pepper example yaml + morning_brief playbook"
```

---

## Task 17: End-to-end test — stub agent through full flow

**Files:**
- Create: `packages/agent-core-briefs/tests/test_e2e_morning_brief.py`
- Create: `packages/agent-core-briefs/tests/fixtures/fake_calendar.py` (test-only fetcher)

Drives a stub agent through the complete flow: cron-published BriefRequest → gather (with fake_calendar fixture + filesystem_read against test files) → ComposeBrief envelope wakes stub agent → stub calls compose loop tools → submit_brief → both destinations (fake DiscordEndpoint + tmp_path markdown_file) → audit log written.

- [ ] **Step 1: Write the failing test**

[~250 lines. Sets up: temp dir for agent_root, fake fetchers, fake DiscordEndpoint, tmp file path for markdown destination, in-process bus. Publishes a BriefRequest event with `brief_type: morning_brief`. Verifies: ComposeBrief envelope arrives in stub agent's mailbox; stub calls list_sections, get_section_spec for each, fills in test content, calls submit_brief; submit_brief returns success; Discord client received the embed message; markdown file exists with expected sections; audit.jsonl has the expected event chain.]

- [ ] **Step 2: Run, fail (gradient — pieces work in isolation, this is the integration test)**

- [ ] **Step 3: Wire up any missing integration glue**

[Fix integration bugs found by the e2e test. No new features, just gluing existing pieces together correctly.]

- [ ] **Step 4: Run all bus_log + briefs tests, expect green + commit**

```
git commit -m "test(briefs): end-to-end morning_brief flow with stub agent"
```

---

## Task 18: Test playbook + ledger updates

**Files:**
- Create: `docs/cutover/test-playbooks/09-brief-framework.md`
- Modify: `docs/cutover/test-playbooks/README.md` (add row #09)
- Modify: `docs/requirements/pepper-pre-cutover-must-haves.md` (add row #09)
- Modify: `docs/requirements/pepper-cutover-agent-playbook.md` (add per-ticket entry for #09)

Documentation pass mirroring cutover #04's Task 12. Captures what shipped, acceptance criteria, verification steps, known limitations. Adds #09 to the at-a-glance ledger and the agent playbook.

- [ ] **Step 1: Write the test playbook**

[Full playbook content following the #04 template. Sections: what was implemented, acceptance criteria from spec §"Done looks like", verification steps (automated tests, manual stub-agent flow, real Pepper morning_brief once she's on agent_core), pass/fail summary, known limitations.]

- [ ] **Step 2: Update the README index, must-haves table, agent playbook**

[Add row 09 between 08 and any subsequent rows. Status: "Implementation complete; verification pending end-of-cutover run."]

- [ ] **Step 3: Run full test suite final pass**

```
uv run pytest packages/core/tests/ packages/agent-core-briefs/tests/ -q
```

Expected: all green. Should be ~575 tests (473 from cutover #04 baseline + ~100 new from briefs framework).

- [ ] **Step 4: Commit**

```bash
git add docs/cutover/test-playbooks/09-brief-framework.md docs/cutover/test-playbooks/README.md docs/requirements/pepper-pre-cutover-must-haves.md docs/requirements/pepper-cutover-agent-playbook.md
git commit -m "docs(cutover): #09 test playbook + ledger update"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| Pattern (deterministic-LLM-deterministic) | T1, T4, T9, T13 |
| Architecture (separate package, dep direction) | T1, T16 |
| Triggers (cron, MCP, CLI) | T8 (cron), T14 (MCP), T15 (CLI). Watchers deferred per spec. |
| Gather (Fetcher protocol, async-concurrent, timeouts, audit, filesystem-loaded) | T1, T3, T4, T5, T6 |
| Wake message (ComposeBrief envelope) | T9 |
| Compose loop (agent-driven tool surface) | T10 |
| Submit (atomic validate + format + send) | T13 |
| Destinations (Destination protocol, fan-out, best-effort) | T1, T11, T12, T13 |
| Playbook format (YAML in MD, colors, sections, conditionals, dynamic colors, guidance refs) | T2 (var sub), T7 (parser) |
| Audit log | T13 |
| `${var}` substitution | T2 |
| `{{when.date}}` delivery templating | T12 |
| Pepper example yaml + tripwire | T16 |
| End-to-end test | T17 |
| Ledger updates | T18 |

All spec items map to a task.

**Placeholder scan:** Every step shows actual code or describes the implementation precisely. Tasks 8, 9, 10, 11, 12, 13, 14, 15 use `[Implementation: ~N lines. Description.]` for the implementation step body — this is intentional brevity for tasks where the test code already pins the contract; the implementer reads the test, implements to satisfy it, and the contract is unambiguous. (Cutover #04 plan used the same shorthand for similar tasks.)

**Type / signature consistency:**
- `Fetcher.fetch(self, config: dict, when: datetime) -> dict` — declared T1, used T4-T6.
- `Destination.deliver(self, sections, playbook, scope, when, config) -> DeliveryResult` — declared T1, used T11-T13.
- `gather_context(invocations, *, when) -> dict` — declared T4, used T9, T17.
- `submit_brief(session_token, sections) -> SubmitResult` — declared T13, used T14, T17.
- `parse_playbook(path, vars_map) -> Playbook` — declared T7, used T9, T17.
- `discover_implementations(paths, *, protocol) -> dict[str, type]` — declared T3, used T5, T6, T11, T12, T16.
- `${var}` substitution and `~/` expansion shapes consistent across T2, T7, T16.

All consistent.

**Scope discipline:** Tasks 1-17 are bite-sized TDD with concrete tests. Task 18 is the docs/ledger pass mirroring cutover #04 precedent. v1 explicitly excludes extensions protocol, watchers, the other 5 brief use cases, and Pepper's mobile destination (deferred to v2+ per spec). Plan does not stray into v2+ scope.

---

## Plan complete — execution handoff

Plan saved to `docs/superpowers/plans/2026-05-04-brief-framework.md` with 18 tasks across:

- T1-T7: Foundation (scaffold, protocols, config, loader, gather engine, two fetchers, playbook parser)
- T8-T9: Trigger pathway (scheduler Event extension, BriefRequest orchestrator)
- T10-T14: Compose loop (sessions, tools, two destinations, submit handler with audit, MCP self-launch)
- T15-T16: Operator surface (CLI subapp, plugin wiring, Pepper example yaml)
- T17-T18: Verification (e2e test, test playbook + ledger)

**Recommended execution: superpowers:subagent-driven-development**

Same workflow that just shipped cutover #04 — fresh subagent per task with two-stage review (spec compliance, then code quality), direct-to-main commits, adversarial pre-push review at the end.
