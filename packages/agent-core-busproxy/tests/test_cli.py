"""Smoke tests for the busproxy Typer CLI."""

from __future__ import annotations

from agent_core_busproxy.__main__ import app
from typer.testing import CliRunner


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "--agent" in result.output
    assert "--daemon-url" in result.output


def test_cli_requires_agent() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code != 0
    assert (
        "Missing option" in result.output
        or "required" in result.output.lower()
    )
