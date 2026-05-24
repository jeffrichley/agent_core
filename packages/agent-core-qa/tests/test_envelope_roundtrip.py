"""Scenario 2: envelope round-trip via builtin.stub endpoint.

Most basic v0.3.0 surface. Failure means the bus is broken — nothing
else works.

Transport note:
    The daemon exposes NO /bus/publish or /bus/inbox HTTP route. The only
    HTTP surface is FastMCP Streamable HTTP at /mcp/<agent>/ for each
    ClaudeCodeMCPEndpoint. The builtin.stub endpoint has no HTTP surface.

    Round-trip path:
    1. Call the `send` MCP tool on the `qa` ClaudeCodeMCPEndpoint.
       The tool publishes a TextMessage to `stub` on the bus, stamping
       from_="qa". The bus dispatches in-process: stub.deliver() is called,
       stub records the envelope, auto_ack=True calls bus.ack() (marks
       handled in persistence). All of this happens before the `send` tool
       call returns.
    2. The `send` tool returns {"status": "published", "id": <envelope_id>}.
       This is the complete round-trip signal: the bus dispatched the
       envelope and the stub endpoint handled it synchronously.
    3. We also call `list_pending` on the `qa` endpoint to confirm nothing
       is queued there. Stub does NOT publish an Acknowledgment envelope
       back — it only calls bus.ack(). So count=0 is the expected steady
       state.

    Test passes if send succeeds and the queue is empty afterward.
    Test skips (via autouse daemon_liveness_required) when daemon is down.
"""

from __future__ import annotations

import uuid


async def test_envelope_send_receives_ack(client):
    """Send a TextMessage to builtin.stub; verify bus round-trip is intact.

    Connects to the `qa` ClaudeCodeMCPEndpoint via MCP tool call to
    publish a TextMessage to the `stub` endpoint. Asserts the send tool
    returns "published" (bus dispatch and stub delivery completed
    synchronously) and that the sender's pending queue is empty (stub
    auto-acks; no Acknowledgment envelope flows back).
    """
    correlation_id = uuid.uuid4().hex

    envelope = {
        "to": "stub",
        "kind": "TextMessage",
        "payload": {"kind": "TextMessage", "text": "qa-roundtrip-probe"},
        "correlation_id": correlation_id,
        "metadata": {},
        "urgency": "green",
    }

    send_result = await client.send_envelope(envelope)
    assert send_result.status_code == 200, (
        f"send_envelope failed: status={send_result.status_code} body={send_result.text[:300]}"
    )

    # Verify the tool returned {"status": "published", "id": <hex>}.
    # This is the round-trip signal: the bus dispatched the envelope and
    # the stub endpoint delivered+acked it synchronously before returning.
    result_data = send_result.json()
    assert isinstance(result_data, dict), (
        f"expected dict from send tool, got {type(result_data).__name__}: {result_data!r}"
    )
    assert result_data.get("status") == "published", (
        f"send tool did not return 'published': {result_data!r}"
    )
    published_id = result_data.get("id")
    assert published_id, f"send tool returned no 'id': {result_data!r}"

    # Confirm the sender's queue is empty: stub does NOT publish an
    # Acknowledgment envelope back; it only calls bus.ack() internally.
    # Nothing should be waiting in the `qa` endpoint's inbox.
    pending = await client.list_pending()
    count = pending.get("meta", {}).get("count", -1)
    assert count == 0, (
        f"expected 0 items in qa endpoint queue after stub round-trip, "
        f"got count={count}. Pending snapshot: {pending!r}"
    )
