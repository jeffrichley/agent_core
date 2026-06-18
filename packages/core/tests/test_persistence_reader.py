"""Tests for PersistenceReader — the read-only audit/tail query layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.persistence import Persistence
from agent_core.bus_tail.reader import PersistenceReader, compute_latency_percentiles
from agent_core.clock import FakeClock


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


class TestComputeLatencyPercentiles:
    """Pure-function tests for the percentile math. No DB, no clock."""

    def test_linear_interpolation_at_15_samples(self) -> None:
        # Arrange - 15 samples spaced 1s apart: 1000, 2000, ..., 15000 ms.
        samples = [float(i * 1000) for i in range(1, 16)]
        # Act
        result = compute_latency_percentiles(samples, (50, 95, 99))
        # Assert - Linear interpolation against n=15: rank = p/100 * (n-1).
        # p50: rank=7.0  -> samples[7] = 8000
        # p95: rank=13.3 -> 14000 + 1000*0.3 = 14300
        # p99: rank=13.86-> 14000 + 1000*0.86 = 14860
        assert result == {"p50": 8000, "p95": 14300, "p99": 14860}

    def test_single_sample(self) -> None:
        # Arrange - Use a whole-number sample to avoid banker's-rounding noise.
        samples = [1234.0]
        # Act
        result = compute_latency_percentiles(samples, (50, 95, 99))
        # Assert - Every percentile collapses to the single value.
        assert result == {"p50": 1234, "p95": 1234, "p99": 1234}

    def test_monotonic_ordering(self) -> None:
        # Arrange - Random-order samples must give the same result as sorted.
        samples = [5000.0, 1000.0, 3000.0, 2000.0, 4000.0]
        # Act
        result = compute_latency_percentiles(samples, (50, 95, 99))
        # Assert - p50 <= p95 <= p99 invariant must hold.
        assert result["p50"] <= result["p95"] <= result["p99"]

    def test_empty_pcts_returns_empty_dict(self) -> None:
        # Arrange
        samples = [1000.0, 2000.0]
        # Act
        result = compute_latency_percentiles(samples, ())
        # Assert - No percentiles requested -> empty mapping.
        assert result == {}


@pytest.mark.asyncio
async def test_metrics_ack_latency_percentiles_above_sample_threshold(
    tmp_path: Path,
):
    # Arrange - Use FakeClock so mark_in_flight stamps a deterministic
    # last_attempted. By advancing the clock between insert and
    # mark_in_flight, the synthetic ack latencies are EXACT. The earlier
    # version relied on Persistence calling datetime.now() and asserted
    # "<= 16s with hope," which flaked on slow Windows runners (16.038s).
    # The percentile math itself is covered by TestComputeLatencyPercentiles
    # above; here we just verify the insert -> reader -> snapshot wiring
    # delivers exact synthetic samples through to the same expected output.
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(start=start)
    p = Persistence(tmp_path / "bus.sqlite", clock=clock)
    await p.connect()
    try:
        for i in range(15):
            created = clock.now()
            await p.insert(_make_envelope(id_=f"e{i}", created_at=created))
            # Advance the clock so last_attempted = created + (i+1) seconds.
            clock.advance(i + 1)
            await p.mark_in_flight(f"e{i}", clock.now() + timedelta(seconds=30))
            await p.mark_acked(f"e{i}")
            # Reset clock so the next envelope's created_at is also `start`.
            # Latencies become {1000, 2000, ..., 15000} ms exactly.
            clock._now = start
        # Act
        reader = PersistenceReader(p, clock=clock)
        snap = await reader.metrics_snapshot()
        # Assert - Same expected output as the pure unit test above, proving
        # the wiring carries exact samples through. No tolerance, no jitter.
        assert snap["ack_latency_ms"] == {"p50": 8000, "p95": 14300, "p99": 14860}
    finally:
        await p.close()
