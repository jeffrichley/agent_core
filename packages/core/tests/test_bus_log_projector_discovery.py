"""Pluggy entry-point discovery for default projectors."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log import bootstrap_default_projectors, get_projector
from agent_core.bus_log.projectors import reset_registry


def _ts() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_registry():
    reset_registry()
    yield
    reset_registry()


def test_bootstrap_registers_text_message_projector():
    bootstrap_default_projectors()
    env = Envelope(
        id="e1", correlation_id="c1", from_="discord", to="pepper",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        created_at=_ts(),
    )
    p = get_projector(env)
    assert p is not None
    # Render to verify it's a real projector, not the fallback.
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert row["content"] == "hi"


def test_bootstrap_registers_handoff_ready_projector():
    bootstrap_default_projectors()
    env = Envelope(
        id="h1", correlation_id="ch1", from_="handoff-jobs", to="pepper",
        kind="Event",
        payload=EventPayload(type="HandoffReady", data={"handoff_path": "/x/h.md"}),
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert "continuity ready" in row["content"].lower()


def test_bootstrap_registers_handoff_failed_projector():
    bootstrap_default_projectors()
    env = Envelope(
        id="f1", correlation_id="cf1", from_="handoff-jobs", to="pepper",
        kind="Event",
        payload=EventPayload(type="HandoffFailed", data={"error": "boom"}),
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert "continuity failed" in row["content"].lower()


def test_bootstrap_registers_acknowledgment_skip_projector():
    from agent_core.bus.envelope import AcknowledgmentPayload
    bootstrap_default_projectors()
    env = Envelope(
        id="a1", correlation_id="ca1", from_="discord", to="pepper",
        kind="Acknowledgment", payload=AcknowledgmentPayload(of="other"),
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert row is None  # Acks are skipped


def test_bootstrap_uses_heartbeat_skip_for_text_messages():
    """The TextMessage slot is filled by SchedulerHeartbeatSkipProjector,
    which delegates to TextMessageProjector for non-heartbeat traffic
    and returns None for scheduler heartbeats."""
    bootstrap_default_projectors()
    env = Envelope(
        id="hb", correlation_id="chb", from_="scheduler", to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="tick"),
        metadata={"scheduler_job": "heartbeat"},
        created_at=_ts(),
    )
    p = get_projector(env)
    row = p.render(env, perspective="pepper", timezone="UTC")
    assert row is None
