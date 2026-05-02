"""Session registry middleware tracks connected ServerSession refs."""

from __future__ import annotations

import pytest

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _FakeSession:
    """Stand-in object identity for register/unregister tests.

    The middleware loop (`on_message` → `_claim_session` → `start_soon`) is
    covered end-to-end by
    `test_session_registry_tracks_connected_sessions_after_mcp_message`
    in `test_claude_code_mcp.py`. These tests drive the sync register/
    unregister methods directly, so the fake only needs unique identity.
    """


@pytest.mark.asyncio
async def test_register_session_captures_reference():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    assert session in ep._sessions


@pytest.mark.asyncio
async def test_unregister_session_clears_reference():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    ep._unregister_session(session)
    assert session not in ep._sessions


@pytest.mark.asyncio
async def test_unregister_unknown_session_is_noop():
    """Unregistering a session that was never registered changes nothing."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._unregister_session(session_b)  # different session — should be a no-op
    assert session_a in ep._sessions
    assert len(ep._sessions) == 1


@pytest.mark.asyncio
async def test_register_second_session_keeps_both():
    """Multiple sessions can coexist in the registry."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._register_session(session_b)
    assert session_a in ep._sessions
    assert session_b in ep._sessions


@pytest.mark.asyncio
async def test_unregister_one_of_two_sessions_keeps_other():
    """A late finally from one session does not drop the other session."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._register_session(session_b)
    ep._unregister_session(session_a)
    assert session_a not in ep._sessions
    assert session_b in ep._sessions


@pytest.mark.asyncio
async def test_register_same_session_twice_is_idempotent():
    """Re-registering the SAME session ref is a no-op (same identity)."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    ep._register_session(session)  # no raise
    assert session in ep._sessions
    assert len(ep._sessions) == 1
