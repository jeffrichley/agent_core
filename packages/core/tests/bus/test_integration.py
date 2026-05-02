"""End-to-end integration test for Phase 1 — bus + stub endpoints + sweeps."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from agent_core.bus.envelope import TextMessagePayload
from agent_core.bus.runner import build_bus_from_config


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    config = {
        "bus": {
            "storage_path": str(tmp_path / "bus.sqlite"),
            "redelivery_timeout_seconds": 1,
            "max_delivery_attempts": 2,
            "ttl_sweep_seconds": 1,
            "redelivery_sweep_seconds": 1,
            "max_pending_per_endpoint": 1000,
        },
        "endpoints": [
            {
                "type": "builtin.stub",
                "name": "alice",
                "description": "First test endpoint.",
                "params": {},
            },
            {
                "type": "builtin.stub",
                "name": "bob",
                "description": "Second test endpoint.",
                "params": {},
            },
        ],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


class TestE2E:
    async def test_alice_sends_to_bob(self, cfg_path: Path):
        bus, _ = await build_bus_from_config(cfg_path)
        await bus.start()
        try:
            alice = bus._endpoints_by_name["alice"].endpoint
            bob = bus._endpoints_by_name["bob"].endpoint

            await alice.send(
                to="bob",
                kind="TextMessage",
                payload=TextMessagePayload(text="hello bob"),
            )
            assert len(bob.inbox) == 1
            assert bob.inbox[0].from_ == "alice"
            assert bob.inbox[0].payload.text == "hello bob"
        finally:
            await bus.stop()

    async def test_delivered_envelope_persists_to_disk(self, cfg_path: Path):
        # First run: queue an envelope to a (deliberately not-running) recipient.
        bus1, _ = await build_bus_from_config(cfg_path)
        await bus1.start()
        try:
            alice = bus1._endpoints_by_name["alice"].endpoint
            bob = bus1._endpoints_by_name["bob"].endpoint
            # Stop bob so deliveries queue.
            await bob.stop()
            del bus1._endpoints_by_name["bob"]
            await alice.send(
                to="alice",  # send to self instead — bob is no longer registered
                kind="TextMessage",
                payload=TextMessagePayload(text="for me"),
            )
        finally:
            await bus1.stop()

        # Second run: trace shows the envelope persisted.
        from agent_core.bus.persistence import Persistence

        cfg = yaml.safe_load(cfg_path.read_text())
        store = Persistence(Path(cfg["bus"]["storage_path"]))
        await store.connect()
        try:
            async with store._conn.execute("SELECT COUNT(*) FROM envelopes") as cur:
                count = (await cur.fetchone())[0]
            assert count >= 1
        finally:
            await store.close()

    async def test_ttl_expires_unrouted_message(self, cfg_path: Path):
        bus, _ = await build_bus_from_config(cfg_path)
        await bus.start()
        try:
            import uuid

            from agent_core.bus.envelope import Envelope

            past = datetime.now(UTC) - timedelta(hours=1)
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id="c",
                from_="alice",
                to="alice",
                kind="TextMessage",
                payload=TextMessagePayload(text="stale"),
                expires_at=past,
                created_at=datetime.now(UTC),
            )
            await bus._store.insert(env)
            await bus.run_ttl_sweep_once()
            assert (await bus._store.row(env.id))["state"] == "expired"
        finally:
            await bus.stop()

    async def test_pending_envelope_drains_on_restart(self, cfg_path: Path):
        """Durable mailbox survives restart: pending envelope is delivered on second boot."""
        import uuid

        from agent_core.bus.envelope import Envelope

        # First boot: seed a pending envelope addressed to bob directly into the
        # store, simulating "bob was unavailable so delivery was queued."
        bus1, _ = await build_bus_from_config(cfg_path)
        await bus1.start()
        try:
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id="restart-test",
                from_="alice",
                to="bob",
                kind="TextMessage",
                payload=TextMessagePayload(text="hello after restart"),
                created_at=datetime.now(UTC),
            )
            # Bypass dispatch so the envelope lands as "pending" (not auto-acked).
            await bus1._store.insert(env)
            assert (await bus1._store.row(env.id))["state"] == "pending"
        finally:
            await bus1.stop()

        # Second boot: a fresh bus from the same config. Bus.start calls
        # drain_for for each registered endpoint, which should pick up the
        # pending envelope and deliver it to bob's fresh inbox.
        bus2, _ = await build_bus_from_config(cfg_path)
        await bus2.start()
        try:
            bob = bus2._endpoints_by_name["bob"].endpoint
            assert len(bob.inbox) == 1
            assert bob.inbox[0].correlation_id == "restart-test"
            # The envelope must now be acked, proving the full loop ran:
            # drain → dispatch → deliver → auto-ack.
            row = await bus2._store.row(env.id)
            assert row["state"] == "acked"
        finally:
            await bus2.stop()
