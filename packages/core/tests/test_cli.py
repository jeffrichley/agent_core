"""Tests for the agent-core CLI."""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_core.cli import app

runner = CliRunner()


def make_config(tmp_path: Path) -> Path:
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "tool": "agent_core.hooks.tools.time_injector.TimeInjector",
                    "params": {"format": "%Y-%m-%d"},
                }
            ],
        }
    }
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


class TestHooksRunCommand:
    def test_run_session_start(self, tmp_path: Path):
        config_path = make_config(tmp_path)
        hook_input = json.dumps({"session_id": "test-123"})
        result = runner.invoke(
            app,
            ["hooks", "run", "SessionStart", "--config", str(config_path)],
            input=hook_input,
        )
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "## Current Time" in output["hookSpecificOutput"]["additionalContext"]

    def test_run_unregistered_event(self, tmp_path: Path):
        config_path = make_config(tmp_path)
        hook_input = json.dumps({"session_id": "test-123"})
        result = runner.invoke(
            app,
            ["hooks", "run", "PostCompact", "--config", str(config_path)],
            input=hook_input,
        )
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["additionalContext"] == ""

    def test_run_missing_config(self, tmp_path: Path):
        hook_input = json.dumps({"session_id": "test-123"})
        result = runner.invoke(
            app,
            ["hooks", "run", "SessionStart", "--config", str(tmp_path / "missing.yaml")],
            input=hook_input,
        )
        assert result.exit_code != 0

    def test_run_with_empty_stdin(self, tmp_path: Path):
        config_path = make_config(tmp_path)
        result = runner.invoke(
            app,
            ["hooks", "run", "SessionStart", "--config", str(config_path)],
            input="",
        )
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
