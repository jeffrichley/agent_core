"""Tests for elder-letter manifest resolution."""

import warnings

from agent_core_hatchery.elder_letters import (
    SourceKind,
    resolve_elder_letters,
)


def test_canonical_path_wins_when_present(tmp_path):
    canonical = tmp_path / "pepper-vault" / "letter.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("CANONICAL CONTENT")

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("BUNDLED CONTENT")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"elders:\n"
        f"  - name: pepper\n"
        f"    canonical_path: {canonical}\n"
        f"    bundled_basename: pepper.md\n"
    )

    resolved = resolve_elder_letters(manifest, bundled_dir)
    assert len(resolved) == 1
    assert resolved[0].name == "pepper"
    assert resolved[0].source_kind == SourceKind.CANONICAL
    assert resolved[0].content == "CANONICAL CONTENT"


def test_falls_back_to_bundled_when_canonical_missing(tmp_path):
    canonical = tmp_path / "does-not-exist" / "letter.md"
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("BUNDLED CONTENT")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"elders:\n"
        f"  - name: pepper\n"
        f"    canonical_path: {canonical}\n"
        f"    bundled_basename: pepper.md\n"
    )

    resolved = resolve_elder_letters(manifest, bundled_dir)
    assert len(resolved) == 1
    assert resolved[0].source_kind == SourceKind.BUNDLED
    assert resolved[0].content == "BUNDLED CONTENT"


def test_warns_and_skips_when_both_missing(tmp_path):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()  # exists but empty

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "elders:\n"
        "  - name: pepper\n"
        "    canonical_path: /nope/letter.md\n"
        "    bundled_basename: pepper.md\n"
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = resolve_elder_letters(manifest, bundled_dir)

    assert resolved == []
    assert any("pepper" in str(w.message) for w in caught)


def test_expands_user_in_canonical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows equivalent
    (tmp_path / ".pepper" / "vault").mkdir(parents=True)
    (tmp_path / ".pepper" / "vault" / "letter.md").write_text("EXPANDED")

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "elders:\n"
        "  - name: pepper\n"
        "    canonical_path: ~/.pepper/vault/letter.md\n"
        "    bundled_basename: pepper.md\n"
    )

    resolved = resolve_elder_letters(manifest, bundled_dir)
    assert resolved[0].content == "EXPANDED"
    assert resolved[0].source_kind == SourceKind.CANONICAL
