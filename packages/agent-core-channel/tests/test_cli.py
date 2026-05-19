"""Smoke tests for the Typer CLI entrypoint."""

from __future__ import annotations

from typer.testing import CliRunner

from agent_core_channel.__main__ import app


def test_cli_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "--agent" in result.output
    assert "--daemon-url" in result.output


def test_cli_requires_agent():
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "required" in result.output.lower()
