"""Tests for `agent-core bus trace <correlation_id>`."""

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
                "name": "a",
                "params": {},
            }
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed_thread(tmp_path: Path):
    import asyncio

    async def _go():
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        for i, eid in enumerate(["a1", "a2", "a3"]):
            await store.insert(
                Envelope(
                    id=eid,
                    correlation_id="thread-1",
                    from_="discord",
                    to="a",
                    kind="TextMessage",
                    payload=TextMessagePayload(text=f"msg {i}"),
                    created_at=datetime(2026, 4, 27, 12, i, 0, tzinfo=UTC),
                )
            )
        # Unrelated thread
        await store.insert(
            Envelope(
                id="other",
                correlation_id="thread-2",
                from_="discord",
                to="a",
                kind="TextMessage",
                payload=TextMessagePayload(text="unrelated"),
                created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
            )
        )
        await store.close()

    asyncio.run(_go())


class TestTrace:
    def test_trace_returns_thread(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed_thread(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "trace", "thread-1", "--config", str(cfg)])
        assert result.exit_code == 0
        for eid in ("a1", "a2", "a3"):
            assert eid in result.output
        assert "other" not in result.output

    def test_trace_unknown_correlation_id(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed_thread(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "trace", "does-not-exist", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "no envelopes found" in result.output
