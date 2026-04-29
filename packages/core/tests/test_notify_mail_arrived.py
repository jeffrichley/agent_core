"""Tests for _notify_mail_arrived push behavior: debounce, summary shape, failures."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingSession:
    """Minimal stand-in for ServerSession that records send_message calls."""

    def __init__(self, fail_with: Exception | None = None):
        self.sent: list[Any] = []
        self._fail_with = fail_with

    async def send_message(self, message) -> None:
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
        created_at=datetime.now(timezone.utc),
    )


def _extract_method(message) -> str:
    """Pull the JSON-RPC method off the SessionMessage."""
    return message.message.root.method


def _extract_params(message) -> dict:
    return message.message.root.params


@pytest.mark.asyncio
async def test_notify_drops_silently_when_no_session():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    # No session registered.
    await ep._notify_mail_arrived("e1")
    # Drain any debounce task so we don't hold a pending coroutine.
    await asyncio.sleep(0.1)
    # No assertion to make — must not raise.


@pytest.mark.asyncio
async def test_notify_pushes_summary_when_session_active():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env("e1", urgency="green")]

    await ep._notify_mail_arrived("e1")
    await asyncio.sleep(0.1)  # let debounce fire

    assert len(session.sent) == 1
    assert _extract_method(session.sent[0]) == "notifications/claude/channel"
    params = _extract_params(session.sent[0])
    assert "content" in params
    assert "meta" in params
    assert params["meta"]["count"] == 1
    assert params["meta"]["endpoint"] == "a"
    assert params["meta"]["urgency_max"] == "green"
    assert params["meta"]["urgency_counts"] == {"red": 0, "yellow": 0, "green": 1}


@pytest.mark.asyncio
async def test_notify_debounces_burst_into_one_push():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [_env(f"e{i}") for i in range(3)]

    # Fire three arrivals back-to-back.
    await ep._notify_mail_arrived("e0")
    await ep._notify_mail_arrived("e1")
    await ep._notify_mail_arrived("e2")
    await asyncio.sleep(0.1)  # let debounce fire

    assert len(session.sent) == 1
    params = _extract_params(session.sent[0])
    assert params["meta"]["count"] == 3


@pytest.mark.asyncio
async def test_notify_summary_reports_max_urgency():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("e1", urgency="green"),
        _env("e2", urgency="yellow"),
        _env("e3", urgency="red"),
    ]
    await ep._notify_mail_arrived("e3")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    assert params["meta"]["urgency_max"] == "red"
    assert params["meta"]["urgency_counts"] == {"red": 1, "yellow": 1, "green": 1}


@pytest.mark.asyncio
async def test_notify_summary_groups_by_sender():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession()
    ep._register_session(session)
    ep._pending = [
        _env("e1", frm="alice"),
        _env("e2", frm="alice"),
        _env("e3", frm="bob"),
    ]
    await ep._notify_mail_arrived("e1")
    await asyncio.sleep(0.1)

    params = _extract_params(session.sent[0])
    by_sender = {entry["from"]: entry["count"] for entry in params["meta"]["by_sender"]}
    assert by_sender == {"alice": 2, "bob": 1}


@pytest.mark.asyncio
async def test_notify_clears_session_slot_on_send_failure():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _RecordingSession(fail_with=ConnectionError("stream closed"))
    ep._register_session(session)
    ep._pending = [_env("e1")]

    await ep._notify_mail_arrived("e1")
    await asyncio.sleep(0.1)

    # Slot must have been cleared so future deliveries fall back to polling.
    assert ep._active_session is None
    assert ep._session_active is False


@pytest.mark.asyncio
async def test_endpoint_instructions_describe_notifications():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    instructions = ep._mcp.instructions or ""
    # Must contain the notification namespace and what to do.
    assert "notifications/claude/channel" in instructions
    assert "list_pending" in instructions
