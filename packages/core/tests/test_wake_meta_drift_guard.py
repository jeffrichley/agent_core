"""Drift-guard: wake notifications carry only endpoint + fired_at in meta.

If anyone re-introduces count, urgency_max, by_sender, or urgency_counts
to the wake notification's meta, this test fires. The fix for issue #33
relies on the wake being purely a 'go look' signal — re-adding queue-state
fields would re-introduce the original race.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint

ALLOWED_WAKE_META_KEYS = frozenset({"endpoint", "fired_at"})


def _envelope(idx: int, urgency: str = "green", from_: str = "alice") -> Envelope:
    return Envelope(
        id=f"env-{idx}-{uuid.uuid4().hex[:8]}",
        correlation_id=f"c-{idx}",
        from_=from_,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=f"msg{idx}"),
        urgency=urgency,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )


def test_wake_summary_meta_keys_are_minimal() -> None:
    """The wake-only summary builder emits exactly the allowed keys."""
    endpoint = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    summary = endpoint._build_wake_summary()
    assert set(summary["meta"].keys()) == ALLOWED_WAKE_META_KEYS


def test_wake_content_is_fixed_string() -> None:
    """Wake content is the fixed non-lying string — not a stale snapshot."""
    endpoint = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    # Even if pending is non-empty, content does not reference counts.
    endpoint.queue_for_pickup(_envelope(0, urgency="green", from_="x"))
    endpoint.queue_for_pickup(_envelope(1, urgency="red", from_="y"))
    summary = endpoint._build_wake_summary()
    assert summary["content"] == "INBOX: pending (agent)"


@pytest.mark.asyncio
async def test_published_wake_has_no_queue_state_meta() -> None:
    """Verify the actual debounce-fire path emits the minimal shape."""
    published: list[dict] = []

    class CaptureBroker:
        async def publish(self, agent: str, event: dict) -> None:
            published.append(event)

    endpoint = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    endpoint._notify_broker = CaptureBroker()  # type: ignore[assignment]
    endpoint.queue_for_pickup(_envelope(0, urgency="green"))
    await endpoint._notify_mail_arrived(urgency="green")
    # Wait long enough for the debounce to fire (green = 1.0s in defaults).
    await asyncio.sleep(1.2)
    assert published, "expected at least one published wake event"
    event = published[-1]
    assert set(event["meta"].keys()) == ALLOWED_WAKE_META_KEYS
