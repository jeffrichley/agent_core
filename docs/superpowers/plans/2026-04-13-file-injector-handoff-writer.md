# FileInjector + HandoffWriter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two new hook tools — FileInjector (generic file reader with IdentityInjector subclass) and HandoffWriter (LLM-powered continuity notes) — plus a shared transcript reader utility.

**Architecture:** FileInjector reads configured files and injects them as context. HandoffWriter reads the JSONL transcript, calls Claude via the Agent SDK to extract structured sections, and writes a handoff note to disk. Both follow the existing HookTool protocol (execute → ToolResult). A shared transcript.py extracts the JSONL parsing logic used by both HandoffWriter and the memory-compiler.

**Tech Stack:** Python 3.12+, Pydantic, Claude Agent SDK (async query), pathlib, pytest (with monkeypatch/tmp_path for mocking)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent_core/hooks/tools/file_injector.py` | FileInjector base tool — reads files, concatenates with headings |
| `src/agent_core/hooks/tools/identity_injector.py` | IdentityInjector subclass — thin defaults override |
| `src/agent_core/transcript.py` | Shared JSONL transcript reader utility |
| `src/agent_core/hooks/tools/handoff_writer.py` | HandoffWriter tool — LLM extraction + file write |
| `tests/test_file_injector.py` | Tests for FileInjector + IdentityInjector |
| `tests/test_transcript.py` | Tests for transcript reader |
| `tests/test_handoff_writer.py` | Tests for HandoffWriter |

---

### Task 1: FileInjector

**Files:**
- Create: `src/agent_core/hooks/tools/file_injector.py`
- Create: `tests/test_file_injector.py`

- [ ] **Step 1: Write failing tests for FileInjector**

`tests/test_file_injector.py`:
```python
"""Tests for the FileInjector hook tool.

FileInjector is a generic tool that reads a list of files and injects
their contents into session context. Tests cover happy path, missing files,
BOM handling, and required params validation.
"""

from pathlib import Path

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.file_injector import FileInjector
from agent_core.models import ToolResult


def make_files(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create test files in a temp directory. Returns the base path."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


class TestFileInjector:
    """Tests for FileInjector — generic file reader tool."""

    def test_implements_hook_tool_protocol(self):
        """FileInjector must satisfy the HookTool protocol."""
        assert isinstance(FileInjector(), HookTool)

    def test_reads_single_file(self, tmp_path: Path):
        """Reads one file and returns its content with a heading."""
        base = make_files(tmp_path, {"hello.md": "Hello world"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["hello.md"]},
        )
        assert isinstance(result, ToolResult)
        assert result.heading == "Injected Files"
        assert "## hello.md" in result.content
        assert "Hello world" in result.content

    def test_reads_multiple_files_in_order(self, tmp_path: Path):
        """Reads files in declared order, each with its own heading."""
        base = make_files(tmp_path, {"a.md": "Content A", "b.md": "Content B"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["a.md", "b.md"]},
        )
        a_pos = result.content.index("## a.md")
        b_pos = result.content.index("## b.md")
        assert a_pos < b_pos

    def test_reads_files_in_subdirectories(self, tmp_path: Path):
        """Supports files in nested paths relative to base_path."""
        base = make_files(tmp_path, {"sub/deep.md": "Deep content"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["sub/deep.md"]},
        )
        assert "Deep content" in result.content

    def test_custom_heading(self, tmp_path: Path):
        """The heading param overrides the default."""
        base = make_files(tmp_path, {"f.md": "stuff"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["f.md"], "heading": "My Context"},
        )
        assert result.heading == "My Context"

    def test_missing_file_skip(self, tmp_path: Path):
        """With skip behavior, missing files are silently omitted."""
        base = make_files(tmp_path, {"exists.md": "I exist"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={
                "base_path": str(base),
                "files": ["exists.md", "gone.md"],
                "missing_file_behavior": "skip",
            },
        )
        assert "I exist" in result.content
        assert "gone.md" not in result.content

    def test_missing_file_warn(self, tmp_path: Path):
        """With warn behavior, missing files get a note in the output."""
        base = make_files(tmp_path, {"exists.md": "I exist"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={
                "base_path": str(base),
                "files": ["exists.md", "gone.md"],
                "missing_file_behavior": "warn",
            },
        )
        assert "I exist" in result.content
        assert "gone.md" in result.content
        assert "file not found" in result.content.lower()

    def test_missing_file_error(self, tmp_path: Path):
        """With error behavior, missing files raise an exception."""
        base = make_files(tmp_path, {"exists.md": "I exist"})
        tool = FileInjector()
        with pytest.raises(FileNotFoundError):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={
                    "base_path": str(base),
                    "files": ["gone.md"],
                    "missing_file_behavior": "error",
                },
            )

    def test_all_files_missing_skip_returns_empty_content(self, tmp_path: Path):
        """If all files are missing with skip, return empty content."""
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={
                "base_path": str(tmp_path),
                "files": ["nope.md"],
                "missing_file_behavior": "skip",
            },
        )
        assert result.content == ""

    def test_utf8_bom_handled(self, tmp_path: Path):
        """Files with UTF-8 BOM are read correctly (BOM stripped)."""
        bom_file = tmp_path / "bom.md"
        bom_file.write_bytes(b"\xef\xbb\xbf# Title\nContent with BOM")
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(tmp_path), "files": ["bom.md"]},
        )
        assert "# Title" in result.content
        assert not result.content.startswith("\ufeff")

    def test_missing_base_path_param_raises(self):
        """Missing base_path param raises a clear error."""
        tool = FileInjector()
        with pytest.raises(ValueError, match="base_path"):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={"files": ["a.md"]},
            )

    def test_missing_files_param_raises(self, tmp_path: Path):
        """Missing files param raises a clear error."""
        tool = FileInjector()
        with pytest.raises(ValueError, match="files"):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={"base_path": str(tmp_path)},
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_file_injector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.hooks.tools.file_injector'`

- [ ] **Step 3: Implement FileInjector**

`src/agent_core/hooks/tools/file_injector.py`:
```python
"""FileInjector — reads a list of files and injects their contents into session context.

This is a generic tool for loading any set of files into a Claude Code session.
Files are read in order and concatenated with ## headings derived from the filename.
All behavior is configurable via YAML params.

This tool serves as the base class for specialized injectors like IdentityInjector.
Subclasses override DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR to set
domain-appropriate defaults without changing any logic.

Configuration:
    In agent_core.yaml, register under any lifecycle event:

        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.file_injector.FileInjector
              params:
                base_path: "/path/to/files"
                files: ["readme.md", "config/settings.md"]
                heading: "Project Context"
                missing_file_behavior: "skip"

    Required params:
        base_path (str): Root directory for file resolution.
        files (list[str]): Ordered list of file paths relative to base_path.

    Optional params:
        heading (str): The ToolResult heading. Default: class DEFAULT_HEADING.
        missing_file_behavior (str): "skip", "warn", or "error".
            Default: class DEFAULT_MISSING_BEHAVIOR.

Example output:
    ToolResult(
        heading="Injected Files",
        content="## readme.md\\n\\n[file contents]\\n\\n## settings.md\\n\\n[file contents]"
    )

See Also:
    agent_core.hooks.tools.identity_injector.IdentityInjector: Subclass for identity files.
    agent_core.hooks.protocol.HookTool: The protocol this tool implements.
"""

from pathlib import Path

from agent_core.models import ToolResult


class FileInjector:
    """Reads a list of files and injects their contents into session context.

    Generic tool for loading any set of files into a Claude Code session.
    Files are read in order and concatenated with ## headings derived from
    the filename. Configurable via YAML params.

    Subclasses can override DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR
    to set domain-appropriate defaults.

    Attributes:
        DEFAULT_HEADING: Default ToolResult heading. Override in subclasses.
        DEFAULT_MISSING_BEHAVIOR: Default missing file behavior. Override in subclasses.
    """

    DEFAULT_HEADING = "Injected Files"
    DEFAULT_MISSING_BEHAVIOR = "skip"

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Read configured files and return their contents as a ToolResult.

        Args:
            event: The lifecycle event name (unused by this tool).
            hook_input: Data from Claude Code (unused by this tool).
            params: Required configuration:
                base_path (str): Root directory for file resolution.
                files (list[str]): Ordered list of file paths relative to base_path.
                heading (str, optional): ToolResult heading. Default: DEFAULT_HEADING.
                missing_file_behavior (str, optional): "skip", "warn", or "error".

        Returns:
            ToolResult with heading and concatenated file contents.

        Raises:
            ValueError: If base_path or files params are missing.
            FileNotFoundError: If missing_file_behavior is "error" and a file is missing.
        """
        base_path_str = params.get("base_path")
        if not base_path_str:
            raise ValueError("Required param 'base_path' is missing")

        files = params.get("files")
        if not files:
            raise ValueError("Required param 'files' is missing")

        heading = params.get("heading", self.DEFAULT_HEADING)
        missing_behavior = params.get("missing_file_behavior", self.DEFAULT_MISSING_BEHAVIOR)

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
                # "skip" — do nothing
                continue

            content = file_path.read_text(encoding="utf-8-sig")
            sections.append(f"## {file_name}\n\n{content}")

        return ToolResult(
            heading=heading,
            content="\n\n".join(sections),
        )
```

Key implementation notes:
- `encoding="utf-8-sig"` handles BOM automatically — Python strips it on read.
- `Path(file_rel).name` extracts just the filename for the heading (so `pepper/handoff.md` becomes `## handoff.md`).
- `missing_behavior` defaults to the class attribute, so subclasses can override.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_file_injector.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/hooks/tools/file_injector.py tests/test_file_injector.py
git commit -m "feat: add FileInjector — generic file reader hook tool"
```

---

### Task 2: IdentityInjector

**Files:**
- Create: `src/agent_core/hooks/tools/identity_injector.py`
- Modify: `tests/test_file_injector.py` (append IdentityInjector tests)

- [ ] **Step 1: Write failing tests for IdentityInjector**

Append to `tests/test_file_injector.py`:
```python
from agent_core.hooks.tools.identity_injector import IdentityInjector


class TestIdentityInjector:
    """Tests for IdentityInjector — thin FileInjector subclass."""

    def test_implements_hook_tool_protocol(self):
        """IdentityInjector must satisfy the HookTool protocol."""
        assert isinstance(IdentityInjector(), HookTool)

    def test_is_subclass_of_file_injector(self):
        """IdentityInjector inherits from FileInjector."""
        assert issubclass(IdentityInjector, FileInjector)

    def test_default_heading_is_identity(self, tmp_path: Path):
        """Default heading should be 'Identity', not 'Injected Files'."""
        base = make_files(tmp_path, {"soul.md": "I am me"})
        tool = IdentityInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["soul.md"]},
        )
        assert result.heading == "Identity"

    def test_default_missing_behavior_is_skip(self, tmp_path: Path):
        """Missing files should be silently skipped by default."""
        base = make_files(tmp_path, {"soul.md": "I am me"})
        tool = IdentityInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={
                "base_path": str(base),
                "files": ["soul.md", "nonexistent.md"],
            },
        )
        assert "I am me" in result.content
        assert "nonexistent" not in result.content

    def test_heading_can_be_overridden_via_params(self, tmp_path: Path):
        """Even with identity defaults, params can override the heading."""
        base = make_files(tmp_path, {"soul.md": "I am me"})
        tool = IdentityInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={
                "base_path": str(base),
                "files": ["soul.md"],
                "heading": "Pepper Identity",
            },
        )
        assert result.heading == "Pepper Identity"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_file_injector.py::TestIdentityInjector -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.hooks.tools.identity_injector'`

- [ ] **Step 3: Implement IdentityInjector**

`src/agent_core/hooks/tools/identity_injector.py`:
```python
"""IdentityInjector — injects agent identity files into session context.

Thin subclass of FileInjector with identity-appropriate defaults. Use this
when loading personality, preferences, and continuity files that define
who an agent is.

The only difference from FileInjector is the default heading ("Identity"
instead of "Injected Files") and the default missing file behavior ("skip"
instead of "skip" — same, but explicitly declared for clarity).

All logic is inherited from FileInjector. This subclass exists so that:
1. Agent configs read 'IdentityInjector' instead of 'FileInjector' — clearer intent.
2. Identity-appropriate defaults don't need to be spelled out in every config.

Configuration:
    In agent_core.yaml:

        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.identity_injector.IdentityInjector
              params:
                base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
                files:
                  - "SOUL.md"
                  - "pepper/preferences.md"
                  - "pepper/handoff.md"

See Also:
    agent_core.hooks.tools.file_injector.FileInjector: The base class with all logic.
"""

from agent_core.hooks.tools.file_injector import FileInjector


class IdentityInjector(FileInjector):
    """Injects agent identity files into session context.

    Thin subclass of FileInjector with identity-appropriate defaults.
    Use this when loading personality, preferences, and continuity files
    that define who an agent is.

    Attributes:
        DEFAULT_HEADING: "Identity" — signals that these files define the agent's self.
        DEFAULT_MISSING_BEHAVIOR: "skip" — identity files like handoff.md may not
            exist on first session, and that's expected.
    """

    DEFAULT_HEADING = "Identity"
    DEFAULT_MISSING_BEHAVIOR = "skip"
```

- [ ] **Step 4: Run all file injector tests**

Run: `uv run pytest tests/test_file_injector.py -v`
Expected: 17 passed (12 FileInjector + 5 IdentityInjector)

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/hooks/tools/identity_injector.py tests/test_file_injector.py
git commit -m "feat: add IdentityInjector — thin FileInjector subclass for agent identity"
```

---

### Task 3: Transcript reader utility

**Files:**
- Create: `src/agent_core/transcript.py`
- Create: `tests/test_transcript.py`

- [ ] **Step 1: Write failing tests for transcript reader**

`tests/test_transcript.py`:
```python
"""Tests for the shared transcript reader utility.

The transcript reader extracts conversation turns from Claude Code's
JSONL transcript format. Used by HandoffWriter and potentially by
the memory-compiler.
"""

import json
from pathlib import Path

from agent_core.transcript import read_transcript


def write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write a list of dicts as a JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def make_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Create a JSONL transcript with user/assistant turns.

    Args:
        path: Where to write the JSONL file.
        turns: List of (role, content) tuples.
    """
    entries = [
        {"message": {"role": role, "content": content}}
        for role, content in turns
    ]
    write_jsonl(path, entries)


class TestReadTranscript:
    """Tests for read_transcript — JSONL parsing and turn extraction."""

    def test_reads_simple_transcript(self, tmp_path: Path):
        """Extracts user and assistant turns from JSONL."""
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [
            ("user", "Hello"),
            ("assistant", "Hi there"),
        ])
        context, count = read_transcript(transcript)
        assert count == 2
        assert "**User:** Hello" in context
        assert "**Assistant:** Hi there" in context

    def test_filters_non_conversation_roles(self, tmp_path: Path):
        """Only user and assistant roles are included."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"message": {"role": "system", "content": "system prompt"}},
            {"message": {"role": "user", "content": "Hello"}},
            {"message": {"role": "assistant", "content": "Hi"}},
        ]
        write_jsonl(transcript, entries)
        context, count = read_transcript(transcript)
        assert count == 2
        assert "system" not in context.lower()

    def test_handles_content_as_list_of_blocks(self, tmp_path: Path):
        """Content can be a list of {type: text, text: ...} blocks."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "First block"},
                {"type": "text", "text": "Second block"},
            ]}},
        ]
        write_jsonl(transcript, entries)
        context, count = read_transcript(transcript)
        assert count == 1
        assert "First block" in context
        assert "Second block" in context

    def test_respects_max_turns(self, tmp_path: Path):
        """Only the last max_turns turns are included."""
        transcript = tmp_path / "transcript.jsonl"
        turns = [(f"{'user' if i % 2 == 0 else 'assistant'}", f"Turn {i}") for i in range(20)]
        make_transcript(transcript, turns)
        context, count = read_transcript(transcript, max_turns=5)
        assert count == 5
        assert "Turn 19" in context
        assert "Turn 0" not in context

    def test_empty_transcript(self, tmp_path: Path):
        """Empty file returns empty string and 0 count."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        context, count = read_transcript(transcript)
        assert context == ""
        assert count == 0

    def test_missing_file_returns_empty(self, tmp_path: Path):
        """Non-existent file returns empty string and 0 count."""
        context, count = read_transcript(tmp_path / "missing.jsonl")
        assert context == ""
        assert count == 0

    def test_handles_malformed_json_lines(self, tmp_path: Path):
        """Malformed lines are skipped, valid lines still extracted."""
        transcript = tmp_path / "transcript.jsonl"
        with open(transcript, "w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps({"message": {"role": "user", "content": "Hello"}}) + "\n")
            f.write("{broken\n")
            f.write(json.dumps({"message": {"role": "assistant", "content": "Hi"}}) + "\n")
        context, count = read_transcript(transcript)
        assert count == 2
        assert "Hello" in context
        assert "Hi" in context

    def test_skips_empty_content(self, tmp_path: Path):
        """Turns with empty or whitespace-only content are excluded."""
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"message": {"role": "user", "content": ""}},
            {"message": {"role": "user", "content": "   "}},
            {"message": {"role": "user", "content": "Real content"}},
        ]
        write_jsonl(transcript, entries)
        context, count = read_transcript(transcript)
        assert count == 1
        assert "Real content" in context
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.transcript'`

- [ ] **Step 3: Implement transcript reader**

`src/agent_core/transcript.py`:
```python
"""Shared JSONL transcript reader utility.

Extracts conversation turns from Claude Code's JSONL transcript format.
Used by HandoffWriter and potentially by the memory-compiler hooks.

Claude Code stores conversations as .jsonl files where each line is a JSON
object with a 'message' key containing 'role' and 'content'. Content can
be a string or a list of content blocks ({type: "text", text: "..."}).

Example:
    >>> from pathlib import Path
    >>> context, count = read_transcript(Path("transcript.jsonl"), max_turns=50)
    >>> print(f"Extracted {count} turns")
    Extracted 42 turns

See Also:
    agent_core.hooks.tools.handoff_writer.HandoffWriter: Uses this to read transcripts.
    memory-compiler/hooks/session-end.py: Original extraction logic this replaces.
"""

import json
from pathlib import Path


def read_transcript(
    transcript_path: Path,
    max_turns: int = 200,
    max_chars: int = 15_000,
) -> tuple[str, int]:
    """Read a Claude Code JSONL transcript and extract conversation turns.

    Parses the JSONL file line by line, extracts user and assistant messages,
    and returns the last max_turns turns formatted as markdown.

    Args:
        transcript_path: Path to the .jsonl transcript file.
        max_turns: Maximum number of turns to extract from the end.
            Default: 200.
        max_chars: Maximum total character count for the output. If exceeded,
            the output is truncated from the beginning at a turn boundary.
            Default: 15,000.

    Returns:
        Tuple of (formatted markdown string, number of turns extracted).
        Returns ("", 0) if the file doesn't exist or is empty.
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
            context = context[boundary + 1:]

    return context, len(recent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_core/transcript.py tests/test_transcript.py
git commit -m "feat: add shared transcript reader utility"
```

---

### Task 4: HandoffWriter

**Files:**
- Create: `src/agent_core/hooks/tools/handoff_writer.py`
- Create: `tests/test_handoff_writer.py`

- [ ] **Step 1: Write failing tests for HandoffWriter**

`tests/test_handoff_writer.py`:
```python
"""Tests for the HandoffWriter hook tool.

HandoffWriter reads the conversation transcript, calls the Claude Agent SDK
for structured extraction, and writes a handoff note to disk. Tests mock
the Agent SDK to avoid real API calls.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.handoff_writer import HandoffWriter
from agent_core.models import ToolResult


def make_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Create a JSONL transcript file."""
    with open(path, "w", encoding="utf-8") as f:
        for role, content in turns:
            f.write(json.dumps({"message": {"role": role, "content": content}}) + "\n")


MOCK_LLM_RESPONSE = """## What We Were Working On
- Building a pluggable hook tool system
- Designing FileInjector and HandoffWriter

## Decisions Made
- Use LLM extraction instead of heuristics
- Drop SessionEndWriter — HandoffWriter covers it

## Emotional Temperature
Productive and collaborative — deep engineering work with good momentum.

## Open Threads
- Need to test end-to-end hook firing

## Observations
- The user prefers thorough documentation at every level"""

MOCK_EMPTY_RESPONSE = "HANDOFF_EMPTY"


class TestHandoffWriter:
    """Tests for HandoffWriter — LLM-powered continuity notes."""

    def test_implements_hook_tool_protocol(self):
        """HandoffWriter must satisfy the HookTool protocol."""
        assert isinstance(HandoffWriter(), HookTool)

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_writes_handoff_file(self, mock_extract, tmp_path: Path):
        """HandoffWriter creates a handoff note file."""
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "test-123"},
            params={"output_path": str(output), "agent_name": "TestAgent"},
        )

        assert isinstance(result, ToolResult)
        assert result.heading == "Handoff Note Written"
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "# Handoff Note" in content
        assert "test-123" in content
        assert "What We Were Working On" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_handoff_empty_response(self, mock_extract, tmp_path: Path):
        """HANDOFF_EMPTY response results in no-content message."""
        mock_extract.return_value = MOCK_EMPTY_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "hi"), ("assistant", "hello")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "test-123"},
            params={"output_path": str(output)},
        )

        assert "No significant content" in result.content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_missing_transcript(self, mock_extract, tmp_path: Path):
        """Missing transcript still writes a note with explanation."""
        mock_extract.return_value = MOCK_EMPTY_RESPONSE
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        result = tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(tmp_path / "missing.jsonl"), "session_id": "s1"},
            params={"output_path": str(output)},
        )

        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "No transcript available" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_creates_parent_directories(self, mock_extract, tmp_path: Path):
        """HandoffWriter creates parent directories if they don't exist."""
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "deep" / "nested" / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s2"},
            params={"output_path": str(output)},
        )

        assert output.exists()

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_overwrites_existing_handoff(self, mock_extract, tmp_path: Path):
        """Handoff file is overwritten, not appended."""
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"
        output.write_text("OLD CONTENT THAT SHOULD BE GONE", encoding="utf-8")

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s3"},
            params={"output_path": str(output)},
        )

        content = output.read_text(encoding="utf-8")
        assert "OLD CONTENT" not in content
        assert "What We Were Working On" in content

    @patch("agent_core.hooks.tools.handoff_writer.extract_handoff")
    def test_agent_name_in_output(self, mock_extract, tmp_path: Path):
        """Agent name appears in the handoff note."""
        mock_extract.return_value = MOCK_LLM_RESPONSE
        transcript = tmp_path / "transcript.jsonl"
        make_transcript(transcript, [("user", "Hello"), ("assistant", "Hi")])
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="PreCompact",
            hook_input={"transcript_path": str(transcript), "session_id": "s4"},
            params={"output_path": str(output), "agent_name": "Pepper"},
        )

        # Verify agent_name was passed to extract_handoff
        mock_extract.assert_called_once()
        call_kwargs = mock_extract.call_args
        assert "Pepper" in call_kwargs[0][1] or "Pepper" in str(call_kwargs)

    def test_missing_output_path_raises(self):
        """Missing output_path param raises a clear error."""
        tool = HandoffWriter()
        with pytest.raises(ValueError, match="output_path"):
            tool.execute(
                event="PreCompact",
                hook_input={},
                params={},
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_handoff_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.hooks.tools.handoff_writer'`

- [ ] **Step 3: Implement HandoffWriter**

`src/agent_core/hooks/tools/handoff_writer.py`:
```python
"""HandoffWriter — writes a structured continuity note before context is lost.

Uses LLM extraction via the Claude Agent SDK to analyze the conversation
transcript and produce a handoff note with topics, decisions, emotional
temperature, and open threads. The note is written to a file that gets
loaded by IdentityInjector on the next session start.

Register on both PreCompact and SessionEnd events to maximize coverage.
Deduplication prevents writing the same handoff twice if both events fire
for the same session.

Configuration:
    In agent_core.yaml:

        pipelines:
          PreCompact:
            - tool: agent_core.hooks.tools.handoff_writer.HandoffWriter
              params:
                output_path: "C:\\Users\\jeffr\\.pepper\\Memory\\pepper\\handoff.md"
                transcript_tail_lines: 200
                timezone: "US/Eastern"
                agent_name: "Pepper"

    Required params:
        output_path (str): Absolute path where the handoff note is written.

    Optional params:
        transcript_tail_lines (int): Lines from transcript end to analyze. Default: 200.
        timezone (str): Timezone for the timestamp. Default: "US/Eastern".
        agent_name (str): Agent name for the LLM prompt. Default: "Assistant".

See Also:
    agent_core.hooks.tools.identity_injector.IdentityInjector: Loads the handoff note.
    agent_core.transcript.read_transcript: Shared transcript reader.
    agent_core.hooks.protocol.HookTool: The protocol this tool implements.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_core.models import ToolResult
from agent_core.transcript import read_transcript

logger = logging.getLogger("agent_core.hooks.tools.handoff_writer")

# Deduplication state file — prevents duplicate handoffs when both
# PreCompact and SessionEnd fire for the same session
_STATE_FILE = Path(__file__).resolve().parent.parent.parent.parent.parent / "handoff-state.json"


def _load_state() -> dict:
    """Load deduplication state."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    """Save deduplication state."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def extract_handoff(transcript_context: str, agent_name: str) -> str:
    """Call Claude via the Agent SDK to extract a structured handoff note.

    Args:
        transcript_context: Formatted transcript text (from read_transcript).
        agent_name: Name of the agent for the LLM prompt perspective.

    Returns:
        The LLM's response — structured markdown sections or "HANDOFF_EMPTY".
    """
    # Set recursion guard before importing Agent SDK
    os.environ["CLAUDE_INVOKED_BY"] = "handoff_writer"

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
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

    response = ""

    async def _run() -> str:
        nonlocal response
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[],
                max_turns=2,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response += block.text
        return response

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("Agent SDK error during handoff extraction: %s", e)
        response = f"HANDOFF_ERROR: {type(e).__name__}: {e}"

    return response


class HandoffWriter:
    """Writes a structured continuity note before context is lost.

    Uses LLM extraction to analyze the conversation transcript and produce
    a handoff note. Register on both PreCompact and SessionEnd for coverage.
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Read transcript, extract via LLM, write handoff note.

        Args:
            event: The lifecycle event name (included in the handoff header).
            hook_input: Data from Claude Code. Expected keys:
                transcript_path (str): Path to the JSONL transcript.
                session_id (str): Unique session identifier.
            params: Required configuration:
                output_path (str): Where to write the handoff note.
            Optional:
                transcript_tail_lines (int): Lines to analyze. Default: 200.
                timezone (str): Timezone for timestamp. Default: "US/Eastern".
                agent_name (str): Agent name for LLM prompt. Default: "Assistant".

        Returns:
            ToolResult confirming the handoff was written.

        Raises:
            ValueError: If output_path param is missing.
        """
        output_path_str = params.get("output_path")
        if not output_path_str:
            raise ValueError("Required param 'output_path' is missing")

        output_path = Path(output_path_str)
        tail_lines = params.get("transcript_tail_lines", 200)
        tz_name = params.get("timezone", "US/Eastern")
        agent_name = params.get("agent_name", "Assistant")
        session_id = hook_input.get("session_id", "unknown")
        transcript_path_str = hook_input.get("transcript_path", "")

        # Deduplication: skip if same session was processed within 60 seconds
        state = _load_state()
        if (
            state.get("session_id") == session_id
            and time.time() - state.get("timestamp", 0) < 60
        ):
            logger.info("Skipping duplicate handoff for session %s", session_id)
            return ToolResult(
                heading="Handoff Note Written",
                content="Handoff already written for this session.",
            )

        # Get timestamp in configured timezone
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("US/Eastern")
        now = datetime.now(timezone.utc).astimezone(tz)
        timestamp = now.strftime("%A, %B %d, %Y %I:%M %p %Z")

        # Read transcript
        if transcript_path_str and Path(transcript_path_str).exists():
            transcript_context, turn_count = read_transcript(
                Path(transcript_path_str), max_turns=tail_lines
            )
        else:
            transcript_context = ""
            turn_count = 0

        # Handle missing/empty transcript
        if not transcript_context.strip():
            header = f"# Handoff Note\n**Written:** {timestamp}\n**Session:** {session_id}\n**Event:** {event}\n\nNo transcript available — session ended without accessible transcript.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(header, encoding="utf-8")
            _save_state({"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No transcript available — wrote empty handoff note.",
            )

        # LLM extraction
        logger.info("Extracting handoff from %d turns for session %s", turn_count, session_id)
        llm_response = extract_handoff(transcript_context, agent_name)

        # Handle HANDOFF_EMPTY
        if "HANDOFF_EMPTY" in llm_response:
            header = f"# Handoff Note\n**Written:** {timestamp}\n**Session:** {session_id}\n**Event:** {event}\n\nNo significant content to hand off.\n"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(header, encoding="utf-8")
            _save_state({"session_id": session_id, "timestamp": time.time()})
            return ToolResult(
                heading="Handoff Note Written",
                content="No significant content to hand off.",
            )

        # Write the handoff note
        handoff_content = f"# Handoff Note\n**Written:** {timestamp}\n**Session:** {session_id}\n**Event:** {event}\n\n{llm_response}\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(handoff_content, encoding="utf-8")

        # Update dedup state
        _save_state({"session_id": session_id, "timestamp": time.time()})

        logger.info("Handoff note written to %s", output_path)
        return ToolResult(
            heading="Handoff Note Written",
            content=f"Handoff note saved to {output_path} at {timestamp}.",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_handoff_writer.py -v`
Expected: 7 passed

- [ ] **Step 5: Run all tests**

Run: `uv run pytest -v`
Expected: All tests pass (32 existing + 12 FileInjector + 5 IdentityInjector + 8 transcript + 7 HandoffWriter = 64 total)

- [ ] **Step 6: Commit**

```bash
git add src/agent_core/hooks/tools/handoff_writer.py tests/test_handoff_writer.py
git commit -m "feat: add HandoffWriter — LLM-powered continuity notes"
```

---

### Task 5: Update YAML config and .gitignore

**Files:**
- Modify: `agent_core.yaml`
- Modify: `.gitignore`

- [ ] **Step 1: Add new tools to agent_core.yaml**

Replace `agent_core.yaml` with:
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

Note: IdentityInjector and HandoffWriter are Pepper-specific — they go in Pepper's config, not agent_core's default config. This YAML is agent_core's own development config.

- [ ] **Step 2: Add handoff-state.json to .gitignore**

Append to the memory compiler runtime state section in `.gitignore`:
```
# Handoff writer state
handoff-state.json
```

- [ ] **Step 3: Commit**

```bash
git add agent_core.yaml .gitignore
git commit -m "chore: update config and gitignore for new tools"
```

---

### Task 6: Knowledge wiki documentation

**Files:**
- Create: `memory-compiler/knowledge/concepts/file-injector.md`
- Create: `memory-compiler/knowledge/concepts/handoff-writer.md`
- Create: `memory-compiler/knowledge/concepts/transcript-reader.md`
- Modify: `memory-compiler/knowledge/index.md`
- Modify: `memory-compiler/knowledge/log.md`

- [ ] **Step 1: Create file-injector article**

`memory-compiler/knowledge/concepts/file-injector.md`:
```markdown
---
title: "FileInjector + IdentityInjector"
aliases: [file-injector, identity-injector]
tags: [agent-core, hooks, tools]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# FileInjector + IdentityInjector

FileInjector is a generic hook tool that reads a configurable list of files and injects their concatenated contents into session context. IdentityInjector is a thin subclass with identity-appropriate defaults (heading "Identity", missing files silently skipped).

## Key Points

- FileInjector reads files listed in params relative to a base_path
- Each file gets a ## heading in the output, content is concatenated
- Three missing file behaviors: skip (silent), warn (note in output), error (raise)
- IdentityInjector overrides only DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR
- Uses utf-8-sig encoding to handle Windows BOM automatically
- base_path and files are required params — clear errors if missing

## Writing a Custom Injector

Subclass FileInjector and override the class attributes:

```python
class ProjectContextInjector(FileInjector):
    DEFAULT_HEADING = "Project Context"
    DEFAULT_MISSING_BEHAVIOR = "warn"
```

## Related Concepts

- [[concepts/hook-tool-protocol]] - The protocol FileInjector implements
- [[concepts/pipeline-system]] - The pipeline that runs FileInjector
- [[concepts/handoff-writer]] - HandoffWriter writes files that IdentityInjector reads

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
```

- [ ] **Step 2: Create handoff-writer article**

`memory-compiler/knowledge/concepts/handoff-writer.md`:
```markdown
---
title: "HandoffWriter"
aliases: [handoff-writer, continuity-notes]
tags: [agent-core, hooks, tools, llm]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# HandoffWriter

HandoffWriter is a hook tool that writes structured continuity notes before context is lost. It uses LLM extraction via the Claude Agent SDK to analyze the conversation transcript and produce a handoff note covering topics, decisions, emotional temperature, open threads, and observations.

## Key Points

- Register on both PreCompact and SessionEnd for maximum coverage
- Uses Claude Agent SDK with allowed_tools=[] for text-only extraction
- Costs ~$0.02-0.05 per invocation — accurate but not free
- Deduplication prevents duplicate handoffs when both events fire for same session
- Recursion guard via CLAUDE_INVOKED_BY env var prevents infinite loops
- HANDOFF_EMPTY sentinel for trivial sessions
- agent_name param controls the LLM's perspective in the prompt
- Uses shared transcript reader from agent_core.transcript

## Related Concepts

- [[concepts/file-injector]] - IdentityInjector loads the handoff note on next session
- [[concepts/transcript-reader]] - Shared utility for reading JSONL transcripts
- [[concepts/hook-tool-protocol]] - The protocol HandoffWriter implements

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
```

- [ ] **Step 3: Create transcript-reader article**

`memory-compiler/knowledge/concepts/transcript-reader.md`:
```markdown
---
title: "Transcript Reader"
aliases: [transcript-reader, jsonl-reader]
tags: [agent-core, utility, transcript]
sources:
  - "docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md"
created: 2026-04-13
updated: 2026-04-13
---

# Transcript Reader

The transcript reader (agent_core.transcript) is a shared utility that extracts conversation turns from Claude Code's JSONL transcript format. It replaces duplicated extraction logic in the memory-compiler hooks.

## Key Points

- Parses JSONL line by line, extracts user and assistant messages
- Handles content as string or list of content blocks
- Filters to user/assistant roles only, skips system messages
- Configurable max_turns and max_chars limits
- Returns (formatted_markdown, turn_count) tuple
- Gracefully handles missing files, empty transcripts, malformed JSON
- Located at src/agent_core/transcript.py

## Related Concepts

- [[concepts/handoff-writer]] - Primary consumer of this utility
- [[concepts/pipeline-system]] - Part of the agent_core framework

## Sources

- [[docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md]] - Design spec
```

- [ ] **Step 4: Update knowledge/index.md**

Append these rows to the table in `memory-compiler/knowledge/index.md`:
```
| [[concepts/file-injector]] | FileInjector + IdentityInjector — generic file reader with identity subclass | docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md | 2026-04-13 |
| [[concepts/handoff-writer]] | HandoffWriter — LLM-powered continuity notes written before context loss | docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md | 2026-04-13 |
| [[concepts/transcript-reader]] | Shared JSONL transcript reader utility for extracting conversation turns | docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md | 2026-04-13 |
```

- [ ] **Step 5: Append to knowledge/log.md**

```markdown

## [2026-04-13T12:00:00-05:00] manual | FileInjector + HandoffWriter
- Source: docs/superpowers/specs/2026-04-13-file-injector-handoff-writer-design.md
- Articles created: [[concepts/file-injector]], [[concepts/handoff-writer]], [[concepts/transcript-reader]]
- Articles updated: (none)
```

- [ ] **Step 6: Commit**

```bash
git add memory-compiler/knowledge/
git commit -m "docs: add knowledge wiki articles for FileInjector, HandoffWriter, transcript reader"
```

---

## Self-Review

**Spec coverage:**
- FileInjector with base_path, files, heading, missing_file_behavior params — Task 1
- IdentityInjector subclass with defaults — Task 2
- BOM handling (utf-8-sig) — Task 1
- Required params validation — Task 1
- Shared transcript reader — Task 3
- HandoffWriter with LLM extraction — Task 4
- Agent name in LLM prompt — Task 4
- "Observations" section (renamed from "Notes To Self") — Task 4
- Deduplication via session_id — Task 4
- Recursion guard — Task 4
- HANDOFF_EMPTY sentinel — Task 4
- Parent directory creation — Task 4
- Overwrite behavior — Task 4
- Missing transcript handling — Task 4
- YAML config update — Task 5
- Knowledge wiki docs — Task 6

**Placeholder scan:** No TBD, TODO, or vague steps found. All steps contain code.

**Type consistency:** FileInjector.execute, IdentityInjector.execute, HandoffWriter.execute all match the HookTool protocol signature: `(self, event: str, hook_input: dict, params: dict) -> ToolResult`. read_transcript signature consistent between definition (Task 3) and usage (Task 4).
