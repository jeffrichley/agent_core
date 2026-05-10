"""Integration test: --config mode renders a complete vault into a tmpdir."""

import re
from pathlib import Path

import pytest

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher, VaultExistsError


def test_hatch_renders_load_bearing_paths(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
    )
    hatcher = Hatcher(cfg)
    result = hatcher.hatch()

    vault = cfg.resolved_vault_root()
    assert vault.exists()
    assert (vault / "Memory" / "IDENTITY.md").is_file()
    assert (vault / "Memory" / "SOUL.md").is_file()
    assert (vault / "Memory" / "USER.md").is_file()
    assert (vault / "Memory" / "MEMORY.md").is_file()
    assert (vault / "Memory" / "OPERATIONS.md").is_file()
    assert (vault / "Memory" / "daily" / "summaries").is_dir()

    # Renamed _being_ → testbeing
    assert (vault / "Memory" / "testbeing").is_dir()
    assert (vault / "Memory" / "testbeing" / "diary.md").is_file()
    assert (vault / "Memory" / "testbeing" / "handoff.md").is_file()

    # Substitution worked
    assert "TestBeing" in (vault / "Memory" / "IDENTITY.md").read_text()
    assert "Tester" in (vault / "Memory" / "SOUL.md").read_text()


def test_hatch_refuses_if_vault_exists(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
    )
    Hatcher(cfg).hatch()

    # Re-hatch must error
    with pytest.raises(VaultExistsError, match=re.escape(str(cfg.resolved_vault_root()))):
        Hatcher(cfg).hatch()


def test_init_missing_top_up(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
    )
    Hatcher(cfg).hatch()

    # Delete one structural file
    vault = cfg.resolved_vault_root()
    (vault / "Memory" / "SOUL.md").unlink()
    (vault / "Memory" / "testbeing" / "diary.md").write_text("user-authored content")

    cfg_topup = cfg.model_copy(update={"init_missing": True})
    Hatcher(cfg_topup).hatch()

    # Restored
    assert (vault / "Memory" / "SOUL.md").is_file()
    # Preserved
    assert (vault / "Memory" / "testbeing" / "diary.md").read_text() == "user-authored content"
