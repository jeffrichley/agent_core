"""Tests for PersistenceReader — the read-only audit/tail query layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.bus_tail.reader import PersistenceReader


def _make_envelope(
    *,
    id_: str,
    from_: str = "alice",
    to: str = "bob",
    kind: str = "TextMessage",
    text: str = "hi",
    urgency: str = "green",
    correlation_id: str | None = None,
    in_reply_to: str | None = None,
    created_at: datetime | None = None,
) -> Envelope:
    return Envelope(
        id=id_,
        correlation_id=correlation_id or id_,
        in_reply_to=in_reply_to,
        from_=from_,
        to=to,
        kind=kind,
        payload=TextMessagePayload(text=text),
        urgency=urgency,
        created_at=created_at or datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def persistence(tmp_path: Path):
    p = Persistence(tmp_path / "bus.sqlite")
    await p.connect()
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_tail_returns_newest_first(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        await persistence.insert(
            _make_envelope(id_=f"e{i}", created_at=base + timedelta(minutes=i))
        )
    reader = PersistenceReader(persistence)
    rows = await reader.tail(limit=10)
    assert [r.id for r in rows] == ["e4", "e3", "e2", "e1", "e0"]


@pytest.mark.asyncio
async def test_tail_limit_clamps_to_max(persistence: Persistence):
    for i in range(5):
        await persistence.insert(_make_envelope(id_=f"e{i}"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(limit=10_000)
    assert len(rows) == 5  # max clamp doesn't synthesize rows; just caps at MAX_LIMIT.


@pytest.mark.asyncio
async def test_tail_limit_clamps_to_min(persistence: Persistence):
    for i in range(3):
        await persistence.insert(_make_envelope(id_=f"e{i}"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(limit=0)
    assert len(rows) == 1  # clamps to 1


@pytest.mark.asyncio
async def test_tail_filter_by_from_endpoint(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="a", from_="alice"))
    await persistence.insert(_make_envelope(id_="b", from_="bob"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(from_endpoint="alice")
    assert [r.id for r in rows] == ["a"]


@pytest.mark.asyncio
async def test_tail_filter_by_to_endpoint(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="a", to="alice"))
    await persistence.insert(_make_envelope(id_="b", to="bob"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(to_endpoint="bob")
    assert [r.id for r in rows] == ["b"]


@pytest.mark.asyncio
async def test_tail_filter_by_kind(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="a", kind="TextMessage"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(kind="TextMessage")
    assert [r.id for r in rows] == ["a"]
    rows_other = await reader.tail(kind="Event")
    assert rows_other == []


@pytest.mark.asyncio
async def test_tail_filter_by_urgency(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="g", urgency="green"))
    await persistence.insert(_make_envelope(id_="r", urgency="red"))
    reader = PersistenceReader(persistence)
    rows = await reader.tail(urgency="red")
    assert [r.id for r in rows] == ["r"]


@pytest.mark.asyncio
async def test_tail_filter_by_state(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="p"))  # pending by default
    await persistence.insert(_make_envelope(id_="a"))
    await persistence.mark_acked("a")
    reader = PersistenceReader(persistence)
    rows = await reader.tail(state="acked")
    assert [r.id for r in rows] == ["a"]


@pytest.mark.asyncio
async def test_tail_since_is_inclusive(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    await persistence.insert(_make_envelope(id_="e0", created_at=base))
    await persistence.insert(
        _make_envelope(id_="e1", created_at=base + timedelta(minutes=1))
    )
    reader = PersistenceReader(persistence)
    rows = await reader.tail(since=base)
    assert {r.id for r in rows} == {"e0", "e1"}


@pytest.mark.asyncio
async def test_tail_before_is_exclusive(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    await persistence.insert(_make_envelope(id_="e0", created_at=base))
    await persistence.insert(
        _make_envelope(id_="e1", created_at=base + timedelta(minutes=1))
    )
    reader = PersistenceReader(persistence)
    rows = await reader.tail(before=base + timedelta(minutes=1))
    assert {r.id for r in rows} == {"e0"}


@pytest.mark.asyncio
async def test_get_envelope_returns_envelope_or_none(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="e0"))
    reader = PersistenceReader(persistence)
    found = await reader.get_envelope("e0")
    assert found is not None
    assert found.id == "e0"
    missing = await reader.get_envelope("nope")
    assert missing is None


@pytest.mark.asyncio
async def test_list_by_correlation_returns_chain_oldest_first(persistence: Persistence):
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    cid = "corr-1"
    await persistence.insert(
        _make_envelope(id_="root", correlation_id=cid, created_at=base)
    )
    await persistence.insert(
        _make_envelope(
            id_="reply",
            correlation_id=cid,
            in_reply_to="root",
            created_at=base + timedelta(seconds=1),
        )
    )
    reader = PersistenceReader(persistence)
    rows = await reader.list_by_correlation(cid)
    assert [r.id for r in rows] == ["root", "reply"]


@pytest.mark.asyncio
async def test_list_by_correlation_unknown_returns_empty(persistence: Persistence):
    reader = PersistenceReader(persistence)
    rows = await reader.list_by_correlation("missing")
    assert rows == []


@pytest.mark.asyncio
async def test_metrics_snapshot_counts_by_kind_and_state(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="t1", kind="TextMessage"))
    await persistence.insert(_make_envelope(id_="t2", kind="TextMessage"))
    await persistence.insert(_make_envelope(id_="a1", kind="TextMessage"))
    await persistence.mark_acked("a1")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    assert snap["window"] == "last_24h"
    assert snap["counts_by_kind"]["TextMessage"] == 3
    assert snap["counts_by_state"]["pending"] == 2
    assert snap["counts_by_state"]["acked"] == 1


@pytest.mark.asyncio
async def test_metrics_window_excludes_old_envelopes(persistence: Persistence):
    long_ago = datetime.now(UTC) - timedelta(hours=48)
    recent = datetime.now(UTC) - timedelta(minutes=10)
    await persistence.insert(_make_envelope(id_="old", created_at=long_ago))
    await persistence.insert(_make_envelope(id_="new", created_at=recent))
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot(window_hours=24)
    assert snap["total_envelopes"] == 1


@pytest.mark.asyncio
async def test_metrics_queue_depth_counts_pending_only(persistence: Persistence):
    await persistence.insert(_make_envelope(id_="p1", to="alice"))
    await persistence.insert(_make_envelope(id_="p2", to="alice"))
    await persistence.insert(_make_envelope(id_="a1", to="bob"))
    await persistence.mark_acked("a1")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    assert snap["queue_depth_by_endpoint"]["alice"] == 2
    assert snap["queue_depth_by_endpoint"].get("bob", 0) == 0


@pytest.mark.asyncio
async def test_metrics_ack_latency_null_below_sample_threshold(persistence: Persistence):
    # Insert + ack 5 envelopes (below threshold of 10).
    base = datetime.now(UTC) - timedelta(minutes=10)
    for i in range(5):
        await persistence.insert(_make_envelope(id_=f"e{i}", created_at=base))
        await persistence.mark_in_flight(f"e{i}", base + timedelta(seconds=5))
        await persistence.mark_acked(f"e{i}")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    assert snap["ack_latency_ms"] is None


@pytest.mark.asyncio
async def test_metrics_ack_latency_percentiles_above_sample_threshold(
    persistence: Persistence,
):
    # Persistence.mark_in_flight always sets last_attempted=now(); we vary
    # created_at so each envelope's (last_attempted - created_at) latency
    # lands in [1s, 15s]. Loop jitter adds a few ms but stays well under
    # the assertion ceiling.
    base = datetime.now(UTC)
    for i in range(15):
        created = base - timedelta(seconds=i + 1)
        await persistence.insert(_make_envelope(id_=f"e{i}", created_at=created))
        await persistence.mark_in_flight(f"e{i}", base + timedelta(seconds=30))
        await persistence.mark_acked(f"e{i}")
    reader = PersistenceReader(persistence)
    snap = await reader.metrics_snapshot()
    latency = snap["ack_latency_ms"]
    assert latency is not None
    assert {"p50", "p95", "p99"}.issubset(latency.keys())
    # Latencies are approximately 1s..15s plus a few ms of loop jitter.
    for v in latency.values():
        assert 0 < v <= 16_000
