"""Tests for `agent-core bus status` and `agent-core bus mailbox`."""

from datetime import UTC, datetime
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.cli import app


def _write_config(tmp_path: Path) -> Path:
    config = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-a",
                "description": "first",
                "params": {},
            },
            {
                "class": "agent_core.endpoints.stub.StubEndpoint",
                "name": "stub-b",
                "description": "second",
                "params": {},
            },
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed(tmp_path: Path):
    """Pre-populate the SQLite store with envelopes."""
    import asyncio

    async def _go():
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        for i in range(3):
            await store.insert(
                Envelope(
                    id=f"e{i}",
                    correlation_id="c1",
                    from_="stub-a",
                    to="stub-b",
                    kind="TextMessage",
                    payload=TextMessagePayload(text=f"msg {i}"),
                    created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
                )
            )
        await store.mark_dead_letter("e2", reason="test")
        await store.close()

    asyncio.run(_go())


class TestStatus:
    def test_status_lists_endpoints(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "status", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "stub-a" in result.output
        assert "stub-b" in result.output
        assert "dlq" in result.output.lower() or "dead" in result.output.lower()


class TestMailbox:
    def test_mailbox_lists_pending(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "mailbox", "stub-b", "--config", str(cfg)])
        assert result.exit_code == 0
        # e0 and e1 are pending; e2 is dead-letter, should not appear.
        assert "e0" in result.output
        assert "e1" in result.output
        assert "e2" not in result.output

    def test_mailbox_unknown_endpoint(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "mailbox", "nobody", "--config", str(cfg)])
        assert result.exit_code != 0
