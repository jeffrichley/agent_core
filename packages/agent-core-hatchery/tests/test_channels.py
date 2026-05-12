"""Tests for channel scaffolding (Discord, webcam, GitHub backup)."""

import os

from agent_core_hatchery.channels.discord import scaffold_discord
from agent_core_hatchery.channels.github_backup import scaffold_github_backup
from agent_core_hatchery.channels.webcam import scaffold_webcam
from agent_core_hatchery.config import (
    DiscordChannelConfig,
    GitHubBackupConfig,
    HatchConfig,
    WebcamChannelConfig,
)


def _cfg(tmp_path, **channels):
    return HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        channels={
            "discord": channels.get("discord", DiscordChannelConfig()),
            "webcam": channels.get("webcam", WebcamChannelConfig()),
            "github_backup": channels.get("github_backup", GitHubBackupConfig()),
        },
    )


def test_discord_scaffold_writes_env_file_and_returns_endpoint_block(tmp_path):
    cfg = _cfg(
        tmp_path,
        discord=DiscordChannelConfig(enabled=True, token="secret-token-xxx",
                                     channel_allowlist=["111", "222"]),
    )
    block = scaffold_discord(cfg)
    assert block is not None
    assert block["type"] == "builtin.discord"
    assert block["name"] == "discord-deb"

    env_file = tmp_path / ".agent-core" / "discord-deb.env"
    assert env_file.is_file()
    assert "DISCORD_DEB_TOKEN=secret-token-xxx" in env_file.read_text()
    if os.name != "nt":
        assert (env_file.stat().st_mode & 0o777) == 0o600


def test_discord_scaffold_returns_none_when_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    assert scaffold_discord(cfg) is None


def test_webcam_scaffold_when_enabled_returns_block(tmp_path):
    cfg = _cfg(tmp_path, webcam=WebcamChannelConfig(enabled=True))
    block = scaffold_webcam(cfg)
    assert block is not None
    assert block["type"] == "builtin.webcam"
    assert block["name"] == "webcam-deb"


def test_github_backup_scaffold_writes_hook_and_returns_job(tmp_path):
    cfg = _cfg(
        tmp_path,
        github_backup=GitHubBackupConfig(
            enabled=True, repo_url="git@github.com:cynthia/deb-vault.git"
        ),
    )
    job = scaffold_github_backup(cfg)
    assert job is not None
    assert "deb-github_backup" in job

    hook_dest = tmp_path / ".deb" / "hooks" / "backup-to-github.sh"
    assert hook_dest.is_file()
    assert "git@github.com:cynthia/deb-vault.git" in hook_dest.read_text()
