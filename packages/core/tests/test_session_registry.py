"""Session registry middleware: captures and releases the active ServerSession."""

from __future__ import annotations

import pytest

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _FakeSession:
    """Stand-in object identity for register/unregister tests.

    The middleware loop (`on_message` → `_claim_session` → `start_soon`) is
    covered end-to-end by `test_session_active_flag_set_after_mcp_message`
    in `test_claude_code_mcp.py`. These tests drive the sync register/
    unregister methods directly, so the fake only needs unique identity.
    """


@pytest.mark.asyncio
async def test_register_session_captures_reference():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    assert ep._active_session is session
    assert ep._session_active is True


@pytest.mark.asyncio
async def test_unregister_session_clears_reference():
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    ep._unregister_session(session)
    assert ep._active_session is None
    assert ep._session_active is False


@pytest.mark.asyncio
async def test_unregister_session_only_clears_if_same_session():
    """Defensive: unregistering session A while session B is active does NOT clear B."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._unregister_session(session_b)  # different session — should be a no-op
    assert ep._active_session is session_a
    assert ep._session_active is True


@pytest.mark.asyncio
async def test_register_second_session_replaces_first():
    """Most-recent-wins: a new session replaces the held one."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._register_session(session_b)
    assert ep._active_session is session_b
    assert ep._session_active is True


@pytest.mark.asyncio
async def test_old_session_unregister_after_replacement_is_noop():
    """When the old session's `_claim_session.finally:` fires after it was
    replaced, the identity check in `_unregister_session` must NOT clear
    the slot now held by the new session.
    """
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session_a = _FakeSession()
    session_b = _FakeSession()
    ep._register_session(session_a)
    ep._register_session(session_b)  # replaces a
    ep._unregister_session(session_a)  # late finally on the old session
    assert ep._active_session is session_b
    assert ep._session_active is True


@pytest.mark.asyncio
async def test_register_same_session_twice_is_idempotent():
    """Re-registering the SAME session ref is a no-op (same identity)."""
    ep = ClaudeCodeMCPEndpoint(name="a", mount="/mcp/a")
    session = _FakeSession()
    ep._register_session(session)
    ep._register_session(session)  # no raise
    assert ep._active_session is session
