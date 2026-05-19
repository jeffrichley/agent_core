"""Tests for `agent-core bus run` — the long-running entry point."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agent_core.cli import app


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "type": "builtin.stub",
                "name": "stub",
                "description": "test",
                "params": {},
            }
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


class TestBusRunCLI:
    def test_run_help(self):
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "run", "--help"])
        assert result.exit_code == 0
        assert "config" in result.output.lower()

    def test_run_with_invalid_config(self, tmp_path: Path):
        p = tmp_path / "missing.yaml"
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "run", "--config", str(p)])
        assert result.exit_code != 0

    @pytest.mark.skipif(sys.platform == "win32", reason="signal handling differs on Windows")
    async def test_run_starts_and_stops_on_sigint(self, cfg_path: Path):
        """End-to-end: start the bus subprocess, send SIGINT, verify graceful exit."""
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_core.cli",
            "bus",
            "run",
            "--config",
            str(cfg_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        async def _wait_until_ready() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    raise AssertionError("bus exited before it became ready")
                if b"bus running" in line:
                    return

        # Wait for the readiness line rather than a fixed sleep: on slow CI a
        # fixed sleep can fire SIGINT before the signal handler is installed,
        # which default-terminates the process (returncode -2) instead of a
        # clean shutdown.
        await asyncio.wait_for(_wait_until_ready(), timeout=30.0)
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            pytest.fail("bus did not shut down on SIGINT")
        assert proc.returncode == 0
