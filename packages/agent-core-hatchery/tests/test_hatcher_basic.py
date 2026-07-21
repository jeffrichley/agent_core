"""Integration test: --config mode renders a complete vault into a tmpdir."""

import re
from pathlib import Path

import pytest
from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher, HatchError, VaultExistsError


def _noop_venv_builder(target: str) -> Path:
    return Path.home() / f".{target}" / ".venv"


def _noop_mcp_gen(**kwargs) -> Path:
    return Path.home() / ".fake" / ".mcp.json"


def test_hatch_renders_load_bearing_paths(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    hatcher = Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen)
    hatcher.hatch()

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
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()

    # Re-hatch must error
    with pytest.raises(VaultExistsError, match=re.escape(str(cfg.resolved_vault_root()))):
        Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()


def test_hatch_writes_daemon_fragments(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()
    assert (tmp_path / ".agent-core" / "endpoints.d" / "testbeing.yaml").is_file()
    assert (tmp_path / ".agent-core" / "jobs.d" / "testbeing.yaml").is_file()


def test_init_missing_top_up(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()

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


def test_hatch_copies_pepper_letter(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()

    pepper_letter = (
        tmp_path / ".testbeing" / "Memory" / "testbeing"
        / "letters" / "from-elder-beings" / "pepper.md"
    )
    assert pepper_letter.is_file()
    assert pepper_letter.stat().st_size > 0


def test_hatch_copies_universal_skills(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()

    skills = tmp_path / ".testbeing" / ".claude" / "skills"
    assert (skills / "skill-author" / "SKILL.md").is_file()
    assert (skills / "vault-lint" / "SKILL.md").is_file()
    assert (skills / "vault-lint" / "scripts" / "lint.py").is_file()
    assert (skills / "spawning-subagents" / "SKILL.md").is_file()


def test_no_gendered_pronouns_in_rendered_vault(tmp_path):
    """No she/her pronouns should appear in any rendered file in the vault.

    The hatchery templates were historically gendered (she/her). Issue #81 removes
    them. This test guards against regression: it greps every text file written by
    Hatcher.hatch() for the pattern ' she ' / ' her ' (word-bounded, case-insensitive)
    and fails loudly if any match is found.
    """
    import re

    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg, _venv_builder=_noop_venv_builder, _mcp_json_gen=_noop_mcp_gen).hatch()

    vault = cfg.resolved_vault_root()
    gendered_pattern = re.compile(r"\b(she|her)\b", re.IGNORECASE)
    violations: list[str] = []

    for path in sorted(vault.rglob("*")):
        # Elder letters are authored voice (Pepper uses she/her by choice) — exclude.
        if "from-elder-beings" in path.parts:
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if gendered_pattern.search(line):
                violations.append(f"{path.relative_to(vault)}:{lineno}: {line.rstrip()}")

    assert violations == [], (
        "Gendered pronoun(s) found in rendered vault (fix #81):\n"
        + "\n".join(violations[:20])
    )


def test_hatch_without_injected_mcp_gen_fails_clearly(tmp_path):
    """The real (non-injected) hatch path must fail with a clear HatchError.

    ``_generate_mcp_json`` lazy-imports agent_core.venv.mcp_config (C2-2, #316),
    which is not yet merged — so a real hatch cannot complete. Every other test
    injects ``_mcp_json_gen``, hiding this; this drives the real path and pins a
    clear, actionable error instead of a cryptic post-venv ImportError.
    Adversarial review finding #5.
    """
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    # Inject only the venv builder to isolate the mcp path; leave _mcp_json_gen
    # unset so the real generator import is exercised.
    hatcher = Hatcher(cfg, _venv_builder=_noop_venv_builder)
    with pytest.raises(HatchError, match="#316"):
        hatcher.hatch()
