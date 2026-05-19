"""Smoke tests for the busproxy Typer CLI."""

from __future__ import annotations

import re

from agent_core_busproxy.__main__ import app
from typer.testing import CliRunner

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _norm(text: str) -> str:
    """Strip ANSI escape codes and collapse whitespace.

    Typer/Rich splits option tokens with ANSI style spans under CI
    (no COLUMNS, non-tty), so raw substring checks are unreliable.
    """
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", text))


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--agent" in _norm(result.output)
    assert "--daemon-url" in _norm(result.output)


def test_cli_requires_agent() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code != 0
    assert (
        "Missing option" in result.output
        or "required" in result.output.lower()
    )
