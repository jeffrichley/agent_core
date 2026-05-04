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


@pytest.mark.asyncio
async def test_json_list_root_raises(tmp_path: Path):
    f = tmp_path / "x.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    with pytest.raises(ValueError, match="json root must be"):
        await fetcher.fetch({"path": str(f), "format": "json"}, datetime.now(UTC))


@pytest.mark.asyncio
async def test_yaml_list_root_raises(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("- a\n- b\n", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    with pytest.raises(ValueError, match="yaml root must be"):
        await fetcher.fetch({"path": str(f), "format": "yaml"}, datetime.now(UTC))


@pytest.mark.asyncio
async def test_path_supports_tilde_expansion(tmp_path: Path, monkeypatch):
    """``~/...`` paths in config get expanded via expand_path before reading."""
    # Cross-platform: ``Path.expanduser`` consults ``USERPROFILE``/``HOMEDRIVE``
    # on Windows in preference to ``HOME``. Override all of them so the test
    # sees ``tmp_path`` regardless of platform. Mirrors test_config._force_home.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    drive, _, tail = str(tmp_path).partition(":")
    if tail:
        monkeypatch.setenv("HOMEDRIVE", f"{drive}:")
        monkeypatch.setenv("HOMEPATH", tail)
    f = tmp_path / "x.md"
    f.write_text("hello", encoding="utf-8")
    fetcher = FilesystemReadFetcher()
    result = await fetcher.fetch({"path": "~/x.md", "format": "text"}, datetime.now(UTC))
    assert result["content"] == "hello"
