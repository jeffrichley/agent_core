"""Tests for `agent-core daemon` CLI."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.daemon.cli import app as daemon_app
from agent_core.daemon.supervisor import is_alive, read_pid


runner = CliRunner()


def test_status_when_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()


def test_stop_when_not_running_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["stop"])
    assert result.exit_code == 0


def test_start_refuses_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["start"])
    assert result.exit_code == 1
    assert "agent_core.yaml" in result.stdout


def test_status_with_stale_pid_reports_not_running_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("999999")  # very unlikely to be alive
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()
    assert not pid_file.exists()  # stale file cleaned


def test_start_writes_pid_file_and_stop_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: write a config, daemon start, daemon stop. Real subprocess."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
""",
        encoding="utf-8",
    )

    start_res = runner.invoke(daemon_app, ["start"])
    assert start_res.exit_code == 0
    pid_file = tmp_path / "daemon.pid"
    assert pid_file.exists()
    pid = read_pid(pid_file)
    assert pid is not None

    # Give the daemon a moment to come up.
    for _ in range(40):
        if is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is True

    # Second start refuses.
    again = runner.invoke(daemon_app, ["start"])
    assert again.exit_code == 1
    assert str(pid) in again.stdout

    # Stop kills it cleanly.
    stop_res = runner.invoke(daemon_app, ["stop"])
    assert stop_res.exit_code == 0
    for _ in range(40):
        if not is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is False
    assert not pid_file.exists()
