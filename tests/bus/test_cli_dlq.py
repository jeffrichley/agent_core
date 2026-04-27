"""Tests for `agent-core bus dlq`, `replay`, and `dlq purge`."""

from datetime import datetime, timedelta, timezone
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
                "name": "stub",
                "params": {},
            }
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def _seed_dlq(tmp_path: Path):
    import asyncio

    async def _go():
        store = Persistence(tmp_path / "bus.sqlite")
        await store.connect()
        for i in range(3):
            await store.insert(
                Envelope(
                    id=f"d{i}",
                    correlation_id="c",
                    from_="discord",
                    to="stub",
                    kind="TextMessage",
                    payload=TextMessagePayload(text=f"failure {i}"),
                    created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
                )
            )
            await store.mark_dead_letter(f"d{i}", reason=f"reason-{i}")
        # One healthy envelope
        await store.insert(
            Envelope(
                id="ok",
                correlation_id="c2",
                from_="discord",
                to="stub",
                kind="TextMessage",
                payload=TextMessagePayload(text="ok"),
                created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        await store.close()

    asyncio.run(_go())


class TestDLQ:
    def test_dlq_lists_dead_letter_only(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        _seed_dlq(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "dlq", "--config", str(cfg)])
        assert result.exit_code == 0
        for eid in ("d0", "d1", "d2"):
            assert eid in result.output
        assert "ok" not in result.output

    def test_replay_resets_state(self, tmp_path: Path):
        import asyncio

        cfg = _write_config(tmp_path)
        _seed_dlq(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "replay", "d0", "--config", str(cfg)])
        assert result.exit_code == 0

        async def _check():
            store = Persistence(tmp_path / "bus.sqlite")
            await store.connect()
            row = await store.row("d0")
            await store.close()
            return row

        row = asyncio.run(_check())
        assert row["state"] == "pending"
        assert row["delivery_count"] == 0

    def test_replay_unknown_id(self, tmp_path: Path):
        cfg = _write_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["bus", "replay", "nope", "--config", str(cfg)])
        assert result.exit_code != 0

    def test_purge_older_than(self, tmp_path: Path):
        import asyncio

        cfg = _write_config(tmp_path)
        _seed_dlq(tmp_path)

        # Backdate one of the dead-letter rows.
        async def _backdate():
            store = Persistence(tmp_path / "bus.sqlite")
            await store.connect()
            past = (datetime(2026, 4, 27, tzinfo=timezone.utc) - timedelta(days=10)).isoformat()
            await store._conn.execute(
                "UPDATE envelopes SET last_attempted = ? WHERE id = 'd0'", (past,)
            )
            await store._conn.commit()
            await store.close()

        asyncio.run(_backdate())

        runner = CliRunner()
        result = runner.invoke(
            app, ["bus", "dlq", "purge", "--older-than", "7d", "--config", str(cfg)]
        )
        assert result.exit_code == 0

        async def _check():
            store = Persistence(tmp_path / "bus.sqlite")
            await store.connect()
            row = await store.row("d0")
            await store.close()
            return row

        # d0 was older than 7d → purged (row deleted)
        assert asyncio.run(_check()) is None
