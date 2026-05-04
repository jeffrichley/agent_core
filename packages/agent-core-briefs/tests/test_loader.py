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


def test_hot_reload_picks_up_edited_file(tmp_path: Path):
    """Editing an existing plugin file mid-life is picked up on the next
    discovery call (the loader doesn't cache; each call re-imports)."""
    _write_fetcher_module(tmp_path / "f1.py", type_id="test.original")
    impls1 = discover_implementations([tmp_path], protocol=Fetcher)
    assert "test.original" in impls1

    _write_fetcher_module(tmp_path / "f1.py", type_id="test.edited")
    impls2 = discover_implementations([tmp_path], protocol=Fetcher)
    assert "test.edited" in impls2
    assert "test.original" not in impls2


def test_syntax_error_in_module_raises_loud(tmp_path: Path):
    (tmp_path / "bad.py").write_text("def broken(\n", encoding="utf-8")
    with pytest.raises(LoaderError, match="bad.py"):
        discover_implementations([tmp_path], protocol=Fetcher)
