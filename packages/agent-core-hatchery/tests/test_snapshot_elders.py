"""Tests for hatchery-snapshot-elders CLI."""

from agent_core_hatchery.snapshot_elders import app
from typer.testing import CliRunner


def test_snapshot_refreshes_bundled_from_canonical(tmp_path):
    canonical = tmp_path / "elder.md"
    canonical.write_text("FRESH CONTENT")

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("STALE")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"elders:\n"
        f"  - name: pepper\n"
        f"    canonical_path: {canonical}\n"
        f"    bundled_basename: pepper.md\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--manifest", str(manifest), "--bundled-dir", str(bundled_dir)],
    )
    assert result.exit_code == 0, result.stdout
    assert (bundled_dir / "pepper.md").read_text() == "FRESH CONTENT"


def test_snapshot_skips_when_canonical_missing(tmp_path):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("KEEP THIS")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "elders:\n"
        "  - name: pepper\n"
        "    canonical_path: /nope/letter.md\n"
        "    bundled_basename: pepper.md\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--manifest", str(manifest), "--bundled-dir", str(bundled_dir)],
    )
    assert result.exit_code == 0
    assert (bundled_dir / "pepper.md").read_text() == "KEEP THIS"
    assert "skipping" in result.stdout.lower()
