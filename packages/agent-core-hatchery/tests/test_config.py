"""Tests for HatchConfig pydantic model."""


import pytest
from agent_core_hatchery.config import HatchConfig
from pydantic import ValidationError


def test_minimal_config_loads():
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
    )
    assert cfg.being_name == "Deb"
    assert cfg.being_name_lower == "deb"
    assert cfg.endpoint_name == "deb"  # defaults to being_name_lower
    assert cfg.discord_token_env == "DISCORD_DEB_TOKEN"
    assert cfg.author_letter_in_editor is True


def test_being_name_required():
    with pytest.raises(ValidationError, match="being_name"):
        HatchConfig(primary_human_name="Cynthia")


def test_env_var_substitution_in_discord_token(monkeypatch):
    monkeypatch.setenv("MY_TEST_TOKEN", "real-token-value")
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        channels={"discord": {"enabled": True, "token": "${MY_TEST_TOKEN}"}},
    )
    assert cfg.channels.discord.resolved_token == "real-token-value"


def test_env_var_substitution_missing_var_raises():
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        channels={"discord": {"enabled": True, "token": "${UNDEFINED_VAR_XYZ}"}},
    )
    with pytest.raises(ValueError, match="UNDEFINED_VAR_XYZ"):
        _ = cfg.channels.discord.resolved_token
