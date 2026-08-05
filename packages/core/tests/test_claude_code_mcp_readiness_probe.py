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

import pytest

from agent_core.endpoints.claude_code_mcp._endpoint import ClaudeCodeMCPEndpoint


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


def test_readiness_gate_probes_a_handle_requiring_tool() -> None:
    """The gate must not regress to a tool that answers before startup.

    Guards the fixture itself: `consume` calls `_require_handle()` before doing
    anything, so it fails loudly while unstarted. `list_pending` does not.
    """
    from agent_core_qa import fixtures

    source = fixtures._qa_endpoint_ready.__doc__ or ""
    import inspect

    body = inspect.getsource(fixtures._qa_endpoint_ready)

    assert '"consume"' in body, (
        "the readiness gate must probe a tool that requires the started handle; "
        "list_pending answers 200 before the endpoint starts and gates nothing"
    )
    assert '"list_pending"' not in body.split('"""')[-1], (
        "list_pending must not be the probe — it does not read _handle"
    )
    assert "auto_ack" in body, "the probe must be a pure read (auto_ack=False)"
    assert source  # docstring explains the reasoning for the next reader
