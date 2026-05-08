"""Tests for _notify_mail_arrived push behavior: debounce, summary shape, failures."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.shared.session import SessionMessage

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingSession:
    """Minimal stand-in for ServerSession that records send_message calls.

    Refuses anything that isn't a SessionMessage so an accidental
    unwrapped JSONRPCNotification would fail the test instead of passing
    silently — the real ServerSession would refuse it too.
    """

    def __init__(self, fail_with: Exception | None = None):
        self.sent: list[Any] = []
        self._fail_with = fail_with

    async def send_message(self, message) -> None:
        assert isinstance(message, SessionMessage), (
            f"send_message requires SessionMessage, got {type(message).__name__}"
        )
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append(message)


def _env(eid: str, frm: str = "src", urgency: str = "green") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to="agent",
        kind="TextMessage",
        payload=TextMessagePayload(text=eid),
        urgency=urgency,
        created_at=datetime.now(UTC),
    )


def _extract_method(message) -> str:
    """Pull the JSON-RPC method off the SessionMessage."""
    return message.message.root.method


def _extract_params(message) -> dict:
    return message.message.root.params


def _speed_up_debounce(ep: ClaudeCodeMCPEndpoint) -> None:
    ep._notify_debounce_seconds_by_urgency = {
        "red": 0.01,
        "yellow": 0.03,
        "green": 0.05,
    }


@pytest.mark.asyncio
async def test_notify_drops_silently_when_no_session():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    # No session registered.
    await ep._notify_mail_arrived("green")
    # Drain any debounce task so we don't hold a pending coroutine.
    await asyncio.sleep(0.1)
    # No assertion to make — must not raise.


@pytest.mark.asyncio
async def test_notify_pushes_summary_when_session_active():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("e1", urgency="green")]

    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.1)  # let debounce fire

    assert len(session.sent) == 1
    assert _extract_method(session.sent[0]) == "notifications/claude/channel"
    params = _extract_params(session.sent[0])
    assert "INBOX: 1 pending" in params["content"]
    assert params["meta"]["count"] == "1"
    assert params["meta"]["endpoint"] == "a"
    assert params["meta"]["urgency_max"] == "green"
    assert json.loads(params["meta"]["urgency_counts"]) == {"red": 0, "yellow": 0, "green": 1}


@pytest.mark.asyncio
async def test_notify_push_fans_out_to_all_registered_sessions():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session_a = _RecordingSession()
    session_b = _RecordingSession()
    ep._register_session(session_a)
    ep._register_session(session_b)
    ep._pending = [_env("e1", urgency="green")]

    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.1)

    assert len(session_a.sent) == 1
    assert len(session_b.sent) == 1
    assert _extract_method(session_a.sent[0]) == "notifications/claude/channel"
    assert _extract_method(session_b.sent[0]) == "notifications/claude/channel"


@pytest.mark.asyncio
async def test_notify_channel_meta_values_are_strings_on_http_push_path():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("e1", urgency="red")]

    await ep._notify_mail_arrived("red")
    await asyncio.sleep(0.1)

    meta = _extract_params(session.sent[0])["meta"]
    assert meta["count"] == "1"
    assert meta["urgency_counts"] == '{"red": 1, "yellow": 0, "green": 0}'
    assert all(isinstance(v, str) for v in meta.values())


@pytest.mark.asyncio
async def test_notify_debounces_burst_into_one_push():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env(f"e{i}") for i in range(3)]

    # Fire three arrivals back-to-back.
    await ep._notify_mail_arrived("green")
    await ep._notify_mail_arrived("green")
    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.1)  # let debounce fire

    assert len(session.sent) == 1
    params = _extract_params(session.sent[0])
    assert params["meta"]["count"] == "3"


@pytest.mark.asyncio
async def test_notify_summary_reports_max_urgency():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("e1", urgency="green"),
        _env("e2", urgency="yellow"),
        _env("e3", urgency="red"),
    ]
    await ep._notify_mail_arrived("red")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    assert params["meta"]["urgency_max"] == "red"
    assert json.loads(params["meta"]["urgency_counts"]) == {"red": 1, "yellow": 1, "green": 1}


@pytest.mark.asyncio
async def test_notify_summary_uses_yellow_floor_for_empty_inbox_timeout_wake():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = []

    # Missing-ack timeout wake calls _notify_mail_arrived("yellow").
    await ep._notify_mail_arrived("yellow")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    assert params["meta"]["count"] == "0"
    assert params["meta"]["urgency_max"] == "yellow"
    assert json.loads(params["meta"]["urgency_counts"]) == {"red": 0, "yellow": 0, "green": 0}


@pytest.mark.asyncio
async def test_notify_summary_keeps_red_when_yellow_floor_and_red_pending():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("r1", urgency="red")]

    # Even if wake floor is yellow, red pending should remain red.
    await ep._notify_mail_arrived("yellow")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    assert params["meta"]["urgency_max"] == "red"


@pytest.mark.asyncio
async def test_notify_summary_groups_by_sender():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("e1", frm="alice"),
        _env("e2", frm="alice"),
        _env("e3", frm="bob"),
    ]
    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    by_sender = {entry["from"]: entry["count"] for entry in json.loads(params["meta"]["by_sender"])}
    assert by_sender == {"alice": 2, "bob": 1}


@pytest.mark.asyncio
async def test_notify_unregisters_failed_session_on_send_failure():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession(fail_with=ConnectionError("stream closed"))
    ep._register_session(session)
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.1)

    assert session not in ep._sessions


def test_notify_debounce_defaults_are_urgency_aware():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    assert ep._notify_debounce_seconds_by_urgency == {
        "red": 0.05,
        "yellow": 0.5,
        "green": 1.0,
    }


@pytest.mark.asyncio
async def test_red_arrival_shortens_pending_green_debounce():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("g1", urgency="green"), _env("r1", urgency="red")]

    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.02)
    assert session.sent == []

    await ep._notify_mail_arrived("red")
    await asyncio.sleep(0.02)

    assert len(session.sent) == 1
    assert _extract_params(session.sent[0])["meta"]["urgency_max"] == "red"


@pytest.mark.asyncio
async def test_green_arrival_does_not_delay_pending_red_debounce():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("r1", urgency="red"), _env("g1", urgency="green")]

    await ep._notify_mail_arrived("red")
    await asyncio.sleep(0.005)
    await ep._notify_mail_arrived("green")
    await asyncio.sleep(0.02)

    assert len(session.sent) == 1
    assert _extract_params(session.sent[0])["meta"]["urgency_max"] == "red"


@pytest.mark.asyncio
async def test_endpoint_instructions_describe_notifications():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    instructions = ep._mcp.instructions or ""
    # Must contain the notification namespace and what to do.
    assert "notifications/claude/channel" in instructions
    assert "list_pending" in instructions


# --- Cutover #08: Event-kind envelope perception ---------------------------------
#
# The notification surface contract: every bus event the agent receives —
# TextMessage chats, HandoffReady completions from the daemon, scheduler
# triggers, notify-broker fan-outs — must land in a place the running agent can
# perceive. The shared perception path is:
#
#     bus.publish() -> ClaudeCodeMCPEndpoint.deliver() -> _pending queue
#                   -> _notify_mail_arrived(urgency)
#                   -> notifications/claude/channel JSON-RPC notification
#
# These tests lock in that the path is kind-agnostic — Event envelopes (the
# shape Cutover #02 publishes for HandoffReady / HandoffFailed) flow through
# unchanged and surface their EventPayload type/data via list_pending.


def _event_env(
    eid: str,
    *,
    event_type: str,
    data: dict | None = None,
    urgency: str = "yellow",
) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="handoff-jobs",
        to="agent",
        kind="Event",
        payload=EventPayload(type=event_type, data=data or {}),
        urgency=urgency,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_deliver_kind_agnostic_pushes_for_handoff_ready_event_envelope():
    """Cutover #08: ``deliver()`` itself is envelope-kind-agnostic.

    Production publishes ``HandoffReady`` as ``kind="Event"`` — this test goes
    through the real ``deliver()`` entry point (not just the post-queue
    notify path) so that any future regression where ``deliver()`` rejects or
    diverges on non-``TextMessage`` envelopes would fail the test.

    A HandoffReady Event envelope must queue and produce the same
    ``notifications/claude/channel`` push that a TextMessage would. This is
    what closes Cutover #02 scenario (b): the daemon-published completion
    notification arrives on a surface the running agent can see.

    Note: ``HandoffJobsEndpoint._publish_result`` does not set ``urgency``,
    so HandoffReady arrives at the default ``green`` urgency in production.
    """
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)

    env = _event_env(
        "h1",
        event_type="HandoffReady",
        data={"job_id": "j-1", "session_id": "s-1", "handoff_path": "/x/handoff.md"},
        urgency="green",  # mirrors production default — _publish_result doesn't override
    )
    await ep.deliver(env)
    # The envelope was queued for pickup as part of deliver().
    assert ep._pending == [env]
    # And the notify_mail_arrived debounce task fired the push.
    await asyncio.sleep(0.15)

    assert len(session.sent) == 1
    assert _extract_method(session.sent[0]) == "notifications/claude/channel"
    params = _extract_params(session.sent[0])
    # The summary is generic — the agent then calls list_pending to see specifics.
    assert "INBOX: 1 pending" in params["content"]
    assert params["meta"]["count"] == "1"
    assert params["meta"]["urgency_max"] == "green"


@pytest.mark.asyncio
async def test_list_pending_surfaces_event_payload_type_and_data():
    """The agent must be able to see HandoffReady (and any Event type) details
    from list_pending so it knows which event arrived, not just that mail
    landed. This relies on _envelope_to_dict round-tripping EventPayload via
    pydantic ``model_dump`` — the JSON-RPC payload must include both ``type``
    and ``data``."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    ep._pending = [
        _event_env(
            "h1",
            event_type="HandoffReady",
            data={"job_id": "j-1", "handoff_path": "/x/handoff.md", "content_sha256": "abc"},
        ),
        _event_env(
            "f1",
            event_type="HandoffFailed",
            data={"job_id": "j-2", "error": "boom"},
            urgency="red",
        ),
    ]

    listing = await ep._call_list_pending()
    assert listing["meta"]["count"] == 2
    assert len(listing["items"]) == 2

    by_type = {entry["payload"]["type"]: entry for entry in listing["items"]}
    assert {"HandoffReady", "HandoffFailed"} <= by_type.keys()

    ready = by_type["HandoffReady"]
    assert ready["kind"] == "Event"
    # Full EventPayload shape round-trips: kind, type, schema_version, data.
    assert ready["payload"]["kind"] == "Event"
    assert ready["payload"]["schema_version"] == "1"
    assert ready["payload"]["data"]["handoff_path"] == "/x/handoff.md"
    assert ready["payload"]["data"]["content_sha256"] == "abc"

    failed = by_type["HandoffFailed"]
    assert failed["kind"] == "Event"
    assert failed["payload"]["data"]["error"] == "boom"
    assert failed["urgency"] == "red"


@pytest.mark.asyncio
async def test_mixed_event_and_text_envelopes_surface_together():
    """Discord traffic and bus Events coexist on the same perception surface.
    The agent sees one notification covering both, with grouped sender counts."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    _speed_up_debounce(ep)
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("dm1", frm="discord", urgency="green"),
        _event_env(
            "h1",
            event_type="HandoffReady",
            data={"job_id": "j-1"},
            urgency="yellow",
        ),
    ]

    await ep._notify_mail_arrived("yellow")
    await asyncio.sleep(0.1)

    assert len(session.sent) == 1
    params = _extract_params(session.sent[0])
    assert params["meta"]["count"] == "2"
    assert params["meta"]["urgency_max"] == "yellow"
    by_sender = {entry["from"]: entry["count"] for entry in json.loads(params["meta"]["by_sender"])}
    assert by_sender == {"discord": 1, "handoff-jobs": 1}
