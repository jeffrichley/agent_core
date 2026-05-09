"""Issue #37: emit notifications/tools/list_changed after deferred mounters drain.

The endpoint drains ``deferred_tool_mounters`` during ``start()`` (each mounter
typically registers tools onto the FastMCP server). Connected MCP sessions
cache ``tools/list`` from session-init and won't see the new tools without
the standard MCP ``notifications/tools/list_changed`` push.

Pepper confirmed (2026-05-09) that her Claude Code session does receive and
act on this notification — invalidating the original won't-fix reasoning
that closed this issue on 2026-05-08. Reopened, fixing.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP
from mcp.shared.session import SessionMessage

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


class _RecordingSession:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[Any] = []
        self._fail_with = fail_with

    async def send_message(self, message) -> None:
        assert isinstance(message, SessionMessage)
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append(message)


class _StubHandle:
    async def ack(self, envelope_id: str) -> None: ...

    async def publish(self, envelope, to=None) -> None: ...

    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self) -> list:
        return []


def _is_tools_list_changed(message: SessionMessage) -> bool:
    notif = getattr(message.message.root, "method", None)
    return notif == "notifications/tools/list_changed"


@pytest.mark.asyncio
async def test_start_emits_tools_list_changed_after_deferred_mounters_drain() -> None:
    """A session connected before start() must receive a single
    notifications/tools/list_changed after the deferred mounters drain.

    Also pins the wire shape: jsonrpc="2.0", method=
    notifications/tools/list_changed, params={}. Pepper's Claude Code
    session was verified against this shape on 2026-05-09 — changing the
    shape risks breaking already-verified clients."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    session = _RecordingSession()
    ep._register_session(session)

    mounter_calls: list[FastMCP] = []

    def _mounter(bus) -> None:
        mounter_calls.append(bus)

    ep.deferred_tool_mounters.append(_mounter)

    await ep.start(_StubHandle())  # type: ignore[arg-type]

    assert len(mounter_calls) == 1
    matching = [m for m in session.sent if _is_tools_list_changed(m)]
    assert len(matching) == 1, (
        f"expected exactly one tools/list_changed; got {len(matching)} of {len(session.sent)} total"
    )

    notif = matching[0].message.root
    assert notif.jsonrpc == "2.0"
    assert notif.method == "notifications/tools/list_changed"
    assert notif.params == {}


@pytest.mark.asyncio
async def test_start_does_not_emit_tools_list_changed_with_no_deferred_mounters() -> None:
    """Static registry — no deferred mounters means no spurious notification."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    session = _RecordingSession()
    ep._register_session(session)
    assert ep.deferred_tool_mounters == []

    await ep.start(_StubHandle())  # type: ignore[arg-type]

    matching = [m for m in session.sent if _is_tools_list_changed(m)]
    assert matching == []


@pytest.mark.asyncio
async def test_tools_list_changed_fires_once_for_multiple_mounters() -> None:
    """User preference: batched-once-after-all rather than per-mounter."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    session = _RecordingSession()
    ep._register_session(session)

    fired: list[str] = []

    def _make_mounter(label: str):
        def _m(bus) -> None:
            fired.append(label)

        return _m

    ep.deferred_tool_mounters.extend(
        [_make_mounter("a"), _make_mounter("b"), _make_mounter("c")]
    )

    await ep.start(_StubHandle())  # type: ignore[arg-type]

    assert fired == ["a", "b", "c"]
    matching = [m for m in session.sent if _is_tools_list_changed(m)]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_tools_list_changed_fans_out_to_all_sessions() -> None:
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    s1 = _RecordingSession()
    s2 = _RecordingSession()
    ep._register_session(s1)
    ep._register_session(s2)

    ep.deferred_tool_mounters.append(lambda bus: None)
    await ep.start(_StubHandle())  # type: ignore[arg-type]

    assert any(_is_tools_list_changed(m) for m in s1.sent)
    assert any(_is_tools_list_changed(m) for m in s2.sent)


@pytest.mark.asyncio
async def test_tools_list_changed_unregisters_failing_session() -> None:
    """Mirrors the channel-push pattern: a session that raises during the
    push gets unregistered so it doesn't accumulate dead state."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    bad = _RecordingSession(fail_with=RuntimeError("session is dead"))
    good = _RecordingSession()
    ep._register_session(bad)
    ep._register_session(good)

    ep.deferred_tool_mounters.append(lambda bus: None)
    await ep.start(_StubHandle())  # type: ignore[arg-type]

    assert bad not in ep._sessions
    assert good in ep._sessions
    assert any(_is_tools_list_changed(m) for m in good.sent)


@pytest.mark.asyncio
async def test_tools_list_changed_no_audience_is_safe() -> None:
    """No connected sessions at drain time → no error, no crash."""
    ep = ClaudeCodeMCPEndpoint(name="agent", mount="/mcp/agent")
    assert ep._sessions == set()

    ep.deferred_tool_mounters.append(lambda bus: None)
    await ep.start(_StubHandle())  # type: ignore[arg-type]
    # If we got here without raising, the no-audience path is safe.
