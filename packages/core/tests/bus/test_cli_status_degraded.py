"""Tests for `bus status` degraded-endpoint display (issue #273).

When supervisor_state has quarantined endpoints, `bus status` should show
a "Degraded Endpoints" table. When all endpoints are healthy, the table
should be absent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_core.bus.persistence import Persistence
from agent_core.cli import app


def _write_config(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "type": "builtin.stub",
                "name": "stub-a",
                "description": "first",
                "params": {},
            },
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed_degraded(tmp_path: Path, name: str, last_error: str) -> None:
    """Seed the supervisor_state table with a quarantined entry."""

    async def _go() -> None:
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        await store.upsert_supervisor_state(name, last_error)
        await store.close()

    asyncio.run(_go())


class TestStatusDegraded:
    def test_status_shows_degraded_table_when_quarantined(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path)
        _seed_degraded(tmp_path, "stub-a", "start() raised RuntimeError: boom")
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "status", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Degraded" in result.output
        assert "stub-a" in result.output

    def test_status_no_degraded_table_when_healthy(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path)
        # No entries in supervisor_state — all healthy
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "status", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Degraded" not in result.output
