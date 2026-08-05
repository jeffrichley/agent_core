"""The qa readiness gate's probe tool must actually depend on being started.

The agent-core-qa `source_daemon` fixture gates on the `qa` endpoint being
ready before any scenario runs, because the daemon binds its HTTP port before
its endpoints finish `start()`. That gate polled `list_pending` and accepted
any 200, documented as "a 200 from it means the endpoint is genuinely started
and serving."

That was false. `_call_list_pending` never reads `_handle` — it returns a
snapshot of the in-memory `_pending` list — so it answers 200 with an empty
mailbox whether or not the endpoint started. The gate passed on its first poll
every time and protected nothing, and `endpoint 'qa' is not started` kept
reaching the tests it existed to prevent (agent_core CI 2026-08-05, runs
31003976516 and 31015413254; six occurrences each).

These tests pin the distinction so a future edit cannot quietly swap the probe
back to a tool that answers before the endpoint is up.
"""

from __future__ import annotations

import time

import pytest

from agent_core.endpoints.claude_code_mcp._endpoint import ClaudeCodeMCPEndpoint


def _soon() -> float:
    """A deadline far enough out that a responsive gate always beats it."""
    return time.monotonic() + 5.0


def _unstarted() -> ClaudeCodeMCPEndpoint:
    """An endpoint that has been constructed but never started."""
    ep = ClaudeCodeMCPEndpoint(name="qa", mount="/qa")
    assert ep._handle is None
    return ep


@pytest.mark.asyncio
async def test_list_pending_answers_even_when_not_started() -> None:
    """Documents why list_pending is the WRONG readiness probe.

    This is not a defect in list_pending — a mailbox view that works before
    the bus attaches is reasonable. It is a defect in using it as a proxy for
    readiness.
    """
    result = await _unstarted()._call_list_pending()

    assert set(result) == {"meta", "items"}
    assert result["items"] == []


def test_handle_requiring_tools_refuse_when_not_started() -> None:
    """The readiness probe must call one of these, not list_pending."""
    with pytest.raises(RuntimeError, match="is not started"):
        _unstarted()._require_handle()


class _FakeResult:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _RecordingClient:
    """Captures which tool the readiness gate probes, and with what."""

    def __init__(self, url: str, statuses: list[int]) -> None:
        self.url = url
        self._statuses = list(statuses)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool: str, arguments: dict) -> _FakeResult:
        self.calls.append((tool, arguments))
        status = self._statuses.pop(0) if self._statuses else 200
        return _FakeResult(status)


@pytest.mark.asyncio
async def test_gate_probes_a_handle_requiring_tool_as_a_pure_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must need the handle, and must not mutate the mailbox.

    ``consume`` calls ``_require_handle()`` first, so it 500s while unstarted.
    ``auto_ack=False`` with ``max_items=0`` acks nothing and drops nothing, so
    the gate can poll it repeatedly without consuming a being's mail.
    """
    from agent_core_qa import fixtures

    client = _RecordingClient("http://x", [200])
    monkeypatch.setattr(fixtures, "DaemonClient", lambda url: client)

    assert await fixtures._qa_endpoint_ready("http://x", deadline=_soon()) is True

    tool, args = client.calls[0]
    assert tool == "consume", (
        "the gate must probe a tool that requires the started handle; "
        "list_pending answers 200 before the endpoint starts and gates nothing"
    )
    assert args["auto_ack"] is False, "probing must not ack the mailbox"
    assert args["max_items"] == 0, "probing must not drain the mailbox"


@pytest.mark.asyncio
async def test_gate_keeps_polling_while_the_endpoint_is_unstarted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 means not-started; the gate must wait, not sail through.

    This is the property the old list_pending probe lacked — it accepted the
    first 200 and returned immediately, every time.
    """
    from agent_core_qa import fixtures

    monkeypatch.setattr(fixtures, "_POLL_INTERVAL", 0.001)
    client = _RecordingClient("http://x", [500, 500, 200])
    monkeypatch.setattr(fixtures, "DaemonClient", lambda url: client)

    assert await fixtures._qa_endpoint_ready("http://x", deadline=_soon()) is True
    assert len(client.calls) == 3, "gate must keep polling until the endpoint answers"


@pytest.mark.asyncio
async def test_gate_reports_failure_when_the_endpoint_never_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deadline with a never-starting endpoint must return False, not True."""
    from agent_core_qa import fixtures

    monkeypatch.setattr(fixtures, "_POLL_INTERVAL", 0.001)
    client = _RecordingClient("http://x", [500] * 50)
    monkeypatch.setattr(fixtures, "DaemonClient", lambda url: client)

    result = await fixtures._qa_endpoint_ready("http://x", deadline=time.monotonic() + 0.05)

    assert result is False, "a never-started endpoint must fail the gate, not pass it"
