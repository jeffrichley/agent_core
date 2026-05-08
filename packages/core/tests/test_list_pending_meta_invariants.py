"""Property tests: list_pending's meta and items must agree by construction.

The bug behind issue #33 was that wake-meta could disagree with list_pending.
The fix moves meta into list_pending's response, computed from the same
atomic read of self._pending. These tests assert that contract holds for
varied inbox states.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


def _envelope(
    idx: int,
    urgency: str = "green",
    from_: str = "alice",
    kind: str = "TextMessage",
) -> Envelope:
    return Envelope(
        id=f"env-{idx}-{uuid.uuid4().hex[:8]}",
        correlation_id=f"c-{idx}",
        from_=from_,
        to="agent",
        kind=kind,  # type: ignore[arg-type]
        payload=TextMessagePayload(text=f"msg{idx}"),
        urgency=urgency,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "envelopes, batch_window",
    [
        ([], 0),
        ([_envelope(0, "green")], 0),
        ([_envelope(0, "red"), _envelope(1, "yellow"), _envelope(2, "green")], 0),
        ([_envelope(0, "red"), _envelope(1, "red"), _envelope(2, "green")], 0),
        ([_envelope(i, "green", from_="alice") for i in range(3)], 30),
        (
            [
                _envelope(0, "yellow", from_="alice"),
                _envelope(1, "yellow", from_="alice"),
                _envelope(2, "green", from_="bob"),
            ],
            30,
        ),
    ],
)
async def test_list_pending_meta_matches_items(
    envelopes: list[Envelope], batch_window: int
) -> None:
    """meta.count, urgency_max, urgency_counts, by_sender all reconstruct from items."""
    endpoint = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    for env in envelopes:
        endpoint.queue_for_pickup(env)
    result = await endpoint._call_list_pending(batch_window_seconds=batch_window)

    assert set(result.keys()) == {"meta", "items"}
    meta = result["meta"]

    assert meta["count"] == len(envelopes)
    assert meta["endpoint"] == "agent"
    assert "fetched_at" in meta

    # urgency_max
    if not envelopes:
        assert meta["urgency_max"] == "green"
    else:
        order = {"red": 0, "yellow": 1, "green": 2}
        expected_max = min((e.urgency for e in envelopes), key=lambda u: order[u])
        assert meta["urgency_max"] == expected_max

    # urgency_counts
    counts = {"red": 0, "yellow": 0, "green": 0}
    for e in envelopes:
        counts[e.urgency] += 1
    assert meta["urgency_counts"] == counts

    # by_sender
    by_sender_index = {entry["from"]: entry for entry in meta["by_sender"]}
    sender_counts: dict[str, int] = {}
    for e in envelopes:
        sender_counts[e.from_] = sender_counts.get(e.from_, 0) + 1
    for sender, expected_count in sender_counts.items():
        assert by_sender_index[sender]["count"] == expected_count
