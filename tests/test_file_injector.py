"""Tests for the FileInjector hook tool."""

from pathlib import Path

import pytest

from agent_core.hooks.protocol import HookTool
from agent_core.hooks.tools.file_injector import FileInjector
from agent_core.hooks.tools.identity_injector import IdentityInjector
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
        """Custom heading param overrides the default."""
        base = make_files(tmp_path, {"f.md": "stuff"})
        tool = FileInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["f.md"], "heading": "My Context"},
        )
        assert result.heading == "My Context"

    def test_missing_file_skip(self, tmp_path: Path):
        """Skip behavior silently omits missing files."""
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
        """Warn behavior includes a note about the missing file."""
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
        """Error behavior raises FileNotFoundError for missing files."""
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

    def test_invalid_missing_file_behavior_raises(self, tmp_path: Path):
        """Unrecognized missing_file_behavior values raise ValueError."""
        base = make_files(tmp_path, {"f.md": "content"})
        tool = FileInjector()
        with pytest.raises(ValueError, match="Invalid missing_file_behavior"):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={
                    "base_path": str(base),
                    "files": ["f.md"],
                    "missing_file_behavior": "crash",
                },
            )

    def test_all_files_missing_skip_returns_empty_content(self, tmp_path: Path):
        """All files missing with skip behavior returns empty content."""
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
        """UTF-8 BOM is stripped from file content."""
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
        """Missing base_path param raises ValueError."""
        tool = FileInjector()
        with pytest.raises(ValueError, match="base_path"):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={"files": ["a.md"]},
            )

    def test_missing_files_param_raises(self, tmp_path: Path):
        """Missing files param raises ValueError."""
        tool = FileInjector()
        with pytest.raises(ValueError, match="files"):
            tool.execute(
                event="SessionStart",
                hook_input={},
                params={"base_path": str(tmp_path)},
            )


class TestIdentityInjector:
    """Tests for IdentityInjector — thin FileInjector subclass."""

    def test_implements_hook_tool_protocol(self):
        """IdentityInjector must satisfy the HookTool protocol."""
        assert isinstance(IdentityInjector(), HookTool)

    def test_is_subclass_of_file_injector(self):
        """IdentityInjector is a subclass of FileInjector."""
        assert issubclass(IdentityInjector, FileInjector)

    def test_default_heading_is_identity(self, tmp_path: Path):
        """Default heading is 'Identity', not 'Injected Files'."""
        base = make_files(tmp_path, {"soul.md": "I am me"})
        tool = IdentityInjector()
        result = tool.execute(
            event="SessionStart",
            hook_input={},
            params={"base_path": str(base), "files": ["soul.md"]},
        )
        assert result.heading == "Identity"

    def test_default_missing_behavior_is_skip(self, tmp_path: Path):
        """Default missing file behavior is skip, not the base class default."""
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
        """Heading can be overridden via params even with subclass defaults."""
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
