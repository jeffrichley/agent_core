"""Issue #70: layered RelayConfig resolution — CLI > env > YAML > defaults."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_core_channel.config import RelayConfig, load_config


def test_defaults_when_nothing_set(tmp_path: Path) -> None:
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=tmp_path / "missing.yaml",
        cli_args=cli_args, env={},
    )
    assert config == RelayConfig()


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      mount: /mcp/pepper
      channel_relay:
        inline_mode: preview
        max_envelopes: 10
        max_bytes: 16384
        per_envelope_bytes: 8192
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args, env={},
    )
    assert config.inline_mode == "preview"
    assert config.max_envelopes == 10
    assert config.max_bytes == 16384
    assert config.per_envelope_bytes == 8192


def test_env_overrides_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      channel_relay:
        inline_mode: preview
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args,
        env={"AGENT_CORE_CHANNEL_INLINE_MODE": "disabled"},
    )
    assert config.inline_mode == "disabled"


def test_cli_overrides_env(tmp_path: Path) -> None:
    cli_args = SimpleNamespace(
        inline_mode="full", inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=tmp_path / "missing.yaml",
        cli_args=cli_args,
        env={"AGENT_CORE_CHANNEL_INLINE_MODE": "preview"},
    )
    assert config.inline_mode == "full"


def test_yaml_without_channel_relay_block_uses_defaults(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: pepper
    params:
      mount: /mcp/pepper
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args, env={},
    )
    assert config == RelayConfig()


def test_yaml_for_different_agent_does_not_match(tmp_path: Path) -> None:
    yaml_path = tmp_path / "agent_core.yaml"
    yaml_path.write_text(
        """
endpoints:
  - type: builtin.claude_code_mcp
    name: testbot
    params:
      channel_relay:
        inline_mode: disabled
"""
    )
    cli_args = SimpleNamespace(
        inline_mode=None, inline_max_envelopes=None,
        inline_max_bytes=None, inline_per_envelope_bytes=None,
    )
    config = load_config(
        agent="pepper", config_path=yaml_path, cli_args=cli_args, env={},
    )
    assert config == RelayConfig()  # testbot's config doesn't apply
