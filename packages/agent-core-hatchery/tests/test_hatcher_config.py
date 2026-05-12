"""Tests for the config-template rendering in Hatcher._render_config_tree."""

import json
from pathlib import Path

import pytest
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher


@pytest.fixture
def hatched(tmp_path: Path) -> tuple[HatchConfig, Path]:
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()
    return cfg, cfg.resolved_vault_root()


def test_agent_core_yaml_rendered_at_vault_root(hatched):
    cfg, vault = hatched
    path = vault / "agent_core.yaml"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "{{" not in text and "}}" not in text
    parsed = yaml.safe_load(text)
    assert "pipelines" in parsed
    assert "SessionStart" in parsed["pipelines"]
    assert "PreCompact" in parsed["pipelines"]


def test_claude_settings_rendered_at_dot_claude(hatched):
    cfg, vault = hatched
    path = vault / ".claude" / "settings.json"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "{{" not in text and "}}" not in text
    parsed = json.loads(text)
    assert "hooks" in parsed
    for event in ("SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd"):
        assert event in parsed["hooks"]


def test_claude_md_rendered_at_vault_root(hatched):
    cfg, vault = hatched
    path = vault / "CLAUDE.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "{{" not in text and "}}" not in text
    assert "TestBeing" in text
    assert "Tester" in text


def test_claude_settings_hook_commands_carry_absolute_config_path(hatched):
    """Hook commands must include an absolute --config path so they work
    regardless of the CWD that Claude Code fires the hook from."""
    cfg, vault = hatched
    settings = json.loads((vault / ".claude" / "settings.json").read_text())
    expected_substring = f"--config {vault.as_posix()}/agent_core.yaml"
    for event in ("SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd"):
        for matcher_block in settings["hooks"][event]:
            for hook in matcher_block["hooks"]:
                assert expected_substring in hook["command"], (
                    f"Hook command for {event} missing absolute --config path: "
                    f"{hook['command']!r}"
                )


def test_claude_settings_uses_forward_slashes_only(hatched):
    """No backslashes in hook command paths — they get eaten by the shell that
    runs the hook, producing 'Config file not found: C:Usersjeffr.pepper...'."""
    cfg, vault = hatched
    settings_text = (vault / ".claude" / "settings.json").read_text()
    parsed = json.loads(settings_text)
    for event_blocks in parsed["hooks"].values():
        for block in event_blocks:
            for hook in block["hooks"]:
                cmd = hook["command"]
                assert "\\" not in cmd, (
                    f"Hook command contains backslash (will be shell-escaped away): {cmd!r}"
                )


def test_agent_core_yaml_substitutes_vault_paths(hatched):
    cfg, vault = hatched
    text = (vault / "agent_core.yaml").read_text()
    parsed = yaml.safe_load(text)
    soul_step = parsed["pipelines"]["SessionStart"][1]
    assert soul_step["type"] == "builtin.identity_injector"
    assert soul_step["params"]["base_path"] == vault.as_posix() + "/Memory"


def test_init_missing_preserves_user_edits_to_config(tmp_path: Path):
    """If the user has hand-tuned their settings.json or CLAUDE.md, --init-missing
    must NOT overwrite — the top-up flow only fills in what's missing."""
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()
    vault = cfg.resolved_vault_root()

    # User edits CLAUDE.md
    (vault / "CLAUDE.md").write_text("user-edited content")
    # User adds an extra hook in settings.json
    settings_path = vault / ".claude" / "settings.json"
    settings_path.write_text("user-customized settings")

    cfg_topup = cfg.model_copy(update={"init_missing": True})
    Hatcher(cfg_topup).hatch()

    assert (vault / "CLAUDE.md").read_text() == "user-edited content"
    assert settings_path.read_text() == "user-customized settings"


def test_init_missing_restores_deleted_config(tmp_path: Path):
    """--init-missing should re-create config files the user deleted."""
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()
    vault = cfg.resolved_vault_root()

    (vault / "agent_core.yaml").unlink()
    cfg_topup = cfg.model_copy(update={"init_missing": True})
    Hatcher(cfg_topup).hatch()

    assert (vault / "agent_core.yaml").is_file()
