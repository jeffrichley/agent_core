"""Tests for agent_core.vault_migration_plan (cutover #06)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_core.vault_migration_plan import (
    absolute_paths_in_yaml,
    build_plan,
    is_probable_absolute_path,
    vault_layout_report,
)


def test_is_probable_absolute_path() -> None:
    # Real filesystem paths — break on home-dir rename.
    assert is_probable_absolute_path(r"C:\Users\me\.pepper\Memory")
    assert is_probable_absolute_path("C:/Users/me/x")
    assert is_probable_absolute_path(r"\\server\share\vault")
    assert is_probable_absolute_path("/home/me/vault")
    assert is_probable_absolute_path("/Users/me/vault")
    assert is_probable_absolute_path("/etc/agent-core/config")
    # User-relative paths are also home-bound and need rewriting on rename.
    assert is_probable_absolute_path("~/.pepper/Memory")
    assert is_probable_absolute_path("~jeffr/.pepper/Memory")

    # Non-paths: relatives, bare filenames, scheme URLs.
    assert not is_probable_absolute_path("relative/path")
    assert not is_probable_absolute_path("SOUL.md")

    # URL routes / mount paths that *look* POSIX but don't break on rename.
    # Without the fs-root prefix check, these would false-positive.
    assert not is_probable_absolute_path("/internal/handoff-jobs")
    assert not is_probable_absolute_path("/api/v1/users")
    assert not is_probable_absolute_path("/mcp/agent-a")


def test_absolute_paths_in_yaml_skips_url_route_keys() -> None:
    """A YAML value under a URL-route key (e.g. ``mount``) must not appear in
    the absolute-paths report — those are routes, not filesystem paths,
    and they don't break on a home-dir rename."""
    doc = {
        "endpoints": [
            {
                "type": "builtin.handoff_jobs",
                "params": {
                    "mount": "/internal/handoff-jobs",
                },
            }
        ],
        "http": {"bind_host": "127.0.0.1"},
        "pipelines": {
            "SessionStart": [
                {"params": {"base_path": r"C:\Users\jeffr\.pepper\Memory"}}
            ]
        },
    }
    paths = absolute_paths_in_yaml(yaml.safe_dump(doc))
    assert paths == [r"C:\Users\jeffr\.pepper\Memory"]
    # And specifically, the URL mount must not have leaked in.
    assert "/internal/handoff-jobs" not in paths


def test_absolute_paths_in_yaml_finds_base_path() -> None:
    doc = {
        "pipelines": {
            "SessionStart": [
                {
                    "params": {
                        "base_path": r"C:\Users\jeffr\.pepper\Memory",
                        "output_path": r"D:\other\out.md",
                    }
                }
            ]
        }
    }
    paths = absolute_paths_in_yaml(yaml.safe_dump(doc))
    assert r"C:\Users\jeffr\.pepper\Memory" in paths
    assert r"D:\other\out.md" in paths


def test_vault_layout_report_counts_files(tmp_path: Path) -> None:
    vault = tmp_path / "Memory"
    vault.mkdir()
    (vault / "SOUL.md").write_text("x", encoding="utf-8")
    (vault / "pepper").mkdir()
    (vault / "pepper" / "handoff.md").write_text("h", encoding="utf-8")
    rep = vault_layout_report(vault)
    assert rep["file_count"] == 2
    assert "SOUL.md" not in rep["missing_recommended_files"]
    assert "IDENTITY.md" in rep["missing_recommended_files"]
    names = {e["path"] for e in rep["files"]}
    assert "SOUL.md" in names
    assert "pepper/handoff.md" in names


def test_build_plan_merges_layout_and_configs(tmp_path: Path) -> None:
    vault = tmp_path / "Memory"
    vault.mkdir()
    (vault / "MEMORY.md").write_text("m", encoding="utf-8")
    for d in ("projects", "daily", "ideas", "playbooks", "pepper"):
        (vault / d).mkdir()
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        yaml.safe_dump({"bus": {"storage_path": r"C:\data\bus.sqlite"}}),
        encoding="utf-8",
    )
    plan = build_plan(vault, [cfg])
    assert plan["vault_layout"]["file_count"] >= 1
    assert plan["configs"][0]["absolute_paths"] == [r"C:\data\bus.sqlite"]
    json.dumps(plan)  # serializable
