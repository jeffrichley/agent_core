"""Tests for the file-class manifest loader."""

from pathlib import Path

import pytest
from agent_core_hatchery.file_classes import (
    FileClass,
    FileClassManifest,
    UnclassifiedFileError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_loads_manifest_from_yaml(tmp_path):
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text(
        "classes:\n"
        "  structural: [\"memory/SOUL.md.j2\"]\n"
        "  growth: [\"memory/_being_/diary.md\"]\n"
        "  system: [\"memory/_being_/handoff.md\"]\n"
        "  config: []\n"
        "  hook: []\n"
        "  reference: []\n"
        "  skill: []\n"
    )
    manifest = FileClassManifest.load(manifest_yaml)
    assert manifest.classify("memory/SOUL.md.j2") == FileClass.STRUCTURAL
    assert manifest.classify("memory/_being_/diary.md") == FileClass.GROWTH
    assert manifest.classify("memory/_being_/handoff.md") == FileClass.SYSTEM


def test_unknown_path_raises(tmp_path):
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text("classes:\n  structural: [\"a/b.md\"]\n")
    manifest = FileClassManifest.load(manifest_yaml)
    with pytest.raises(UnclassifiedFileError, match="unknown/path.md"):
        manifest.classify("unknown/path.md")


def test_glob_matches(tmp_path):
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text(
        "classes:\n"
        "  hook: [\"hooks/**/*\"]\n"
        "  skill: [\"skills/**/*\"]\n"
    )
    manifest = FileClassManifest.load(manifest_yaml)
    assert manifest.classify("hooks/backup-to-github.sh.j2") == FileClass.HOOK
    assert manifest.classify("skills/skill-author/SKILL.md") == FileClass.SKILL
    assert manifest.classify("skills/skill-author/scripts/foo.py") == FileClass.SKILL


def test_audit_walks_template_dir(tmp_path):
    """audit() returns paths in the template tree that aren't classified."""
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text(
        "classes:\n  structural: [\"memory/SOUL.md.j2\"]\n"
    )
    template_dir = tmp_path / "templates"
    (template_dir / "memory").mkdir(parents=True)
    (template_dir / "memory" / "SOUL.md.j2").write_text("hi")
    (template_dir / "memory" / "ORPHAN.md").write_text("uh oh")

    manifest = FileClassManifest.load(manifest_yaml)
    orphans = manifest.audit(template_dir)
    assert "memory/ORPHAN.md" in orphans
    assert "memory/SOUL.md.j2" not in orphans


def test_real_manifest_classifies_all_template_files():
    """Every file in templates/ is classified by templates/file-classes.yaml.

    This is the hard rail from the spec: hatcher errors at startup if any
    template is unclassified.
    """
    package_root = Path(__file__).parent.parent
    templates_dir = package_root / "templates"
    manifest_path = templates_dir / "file-classes.yaml"

    if not templates_dir.exists() or not any(p for p in templates_dir.rglob("*") if p.is_file() and p.name != "file-classes.yaml"):
        pytest.skip("templates/ not yet populated (Task 2.6+)")

    manifest = FileClassManifest.load(manifest_path)
    orphans = [p for p in manifest.audit(templates_dir) if p != "file-classes.yaml"]
    assert orphans == [], f"Unclassified template files: {orphans}"
