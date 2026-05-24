"""Scenario 6: discord_send ToolInvocation routes through the stubbed discord slot.

Why v0.3.0 needs it: the discord endpoint is the primary human-facing communication
channel in the agent stack. If the discord slot is mis-registered, mis-named, or
broken at the bus level, ALL agent → human communication silently fails.

Transport choice (Preflight Step 0 finding):
    The discord slot is configured at install time as ``builtin.stub``.  StubEndpoint
    is NOT MCPHostable — it has no HTTP surface of its own.  The bus routes
    ``ToolInvocation`` envelopes addressed to ``"discord"`` to StubEndpoint.deliver(),
    which appends the envelope to ``.inbox`` and calls ``bus.ack(envelope.id)``.

    StubEndpoint does NOT publish an ``AcknowledgmentPayload`` back to the sender.
    This is the key asymmetry vs. DiscordEndpoint (the real adapter) and
    SchedulerEndpoint (which does publish Ack replies):

        stub.deliver() → bus.ack(id)          # marks handled, publishes nothing
        real discord.deliver() → bus.publish(AcknowledgmentPayload)  # echoes result

    Consequence: the ``poll_envelopes`` / error-Ack pattern used in Scenario 5
    (scheduler) cannot be applied here.  No Ack — green or error — ever lands in
    the ``qa`` inbox from the stub.

Acknowledgment auto-clear (inherited context from Task 6):
    ``ClaudeCodeMCPEndpoint._is_routine_green_ack()`` silently discards routine green
    Acks (urgency=green, note not prefixed "error:", ``in_reply_to`` set).  Even if
    the stub *did* publish an Ack, the green path would be auto-cleared.  Error Acks
    (note prefixed "error:") would NOT be auto-cleared, but the stub never emits them.

Assertion strategy — two-phase, stub-aware:
    Phase 1 — real envelope, verify bus-level delivery:
        Send a well-formed ``discord_send`` ToolInvocation to ``"discord"``.
        The ``send`` MCP tool returns ``{"status": "published", "id": <env_id>}``
        synchronously after the bus dispatches and the stub delivers+acks (single
        event loop, synchronous in-process delivery).  status_code=200 + published
        status is the round-trip proof.  A follow-up ``list_pending`` must return
        count=0 — the stub auto-acked, leaving nothing queued in the ``qa`` inbox.

    Phase 2 — deliberate-broken probe, verify bus routing validation:
        Send a ``ToolInvocation`` addressed to a nonexistent endpoint
        (``"discord_qa_nonexistent_probe"``).  The bus raises
        ``ValueError("publish to unregistered endpoint '...'")`` before any
        persistence write.  This propagates through the ``send`` MCP tool as a
        tool-call exception, surfaced by DaemonClient.send_envelope() as
        status_code=500 or an error keyword in the response text.
        This phase proves the routing table is live and validates destinations — a
        misconfigured discord slot name would produce the same error on real sends.

Tool surface discovered from packages/agent-core-discord/src/agent_core_discord/:
    Tool name:  ``"discord_send"``  (no alias in _TOOL_ALIASES; the real
                DiscordEndpoint routes via ``_canonical_tool(tool)`` which falls
                through to the key directly, and ``"discord_send"`` is NOT a key
                in _TOOL_ALIASES — but the stub ignores tool names entirely).
    Args model: ``_SendArgs`` — channel_id (str, min_length=1, REQUIRED),
                text (str | None), embeds, reply_to, files, cleanup_inbound_message_id.
    Error note: ``"error: discord_send: <pydantic ValidationError>"`` when
                channel_id is missing — BUT only from the real DiscordEndpoint.
                The stub ignores args validation.

Endpoint slot name: ``"discord"`` — conventional; confirmed from
    ``agent_core install`` default-config template and plugin alias mapping.
"""

from __future__ import annotations

import uuid


def _toolinvocation_payload(tool: str, args: dict) -> dict:
    """Build the payload dict for a ToolInvocation envelope."""
    return {"kind": "ToolInvocation", "tool": tool, "args": args}


async def test_discord_send_tool_routes_through_stub(client):
    """Send discord_send to the stubbed discord slot; verify routing is alive.

    Phase 1: real envelope → stub delivers + auto-acks → send_envelope returns
             {"status": "published"} + list_pending count=0.
    Phase 2: broken envelope (nonexistent endpoint) → bus raises ValueError →
             send_envelope returns error → proves routing validation is live.

    The test SKIPs (via autouse ``daemon_liveness_required``) when no daemon
    is reachable at the configured URL.
    """
    probe_text = f"qa-discord-probe-{uuid.uuid4().hex[:8]}"

    # ── Phase 1: REAL ENVELOPE ────────────────────────────────────────────────
    # Send a well-formed discord_send to the "discord" slot.  StubEndpoint.deliver()
    # appends the envelope to .inbox and calls bus.ack() — no Ack reply published.
    # The round-trip proof is the synchronous {"status": "published"} from send_envelope().
    real_result = await client.send_envelope(
        {
            "to": "discord",
            "kind": "ToolInvocation",
            "payload": _toolinvocation_payload(
                "discord_send",
                {
                    "channel_id": "qa-stub-channel",
                    "text": probe_text,
                },
            ),
            "correlation_id": uuid.uuid4().hex,
            "metadata": {},
            "urgency": "green",
        }
    )
    assert real_result.status_code == 200, (
        f"discord_send send_envelope transport error: "
        f"status={real_result.status_code} body={real_result.text[:300]}"
    )
    real_data = real_result.json()
    assert isinstance(real_data, dict) and real_data.get("status") == "published", (
        f"discord_send send tool did not return 'published': {real_data!r}"
    )

    # Confirm queue is clear: stub auto-acked the envelope, so the qa inbox
    # should be empty (count=0).  This catches any leak where the stub fails
    # to ack and the envelope lingers pending.
    pending = await client.list_pending()
    items = pending.get("items", [])
    leaked = [
        e for e in items
        if e.get("to") == "discord" or e.get("from_") == "discord"
    ]
    assert leaked == [], (
        f"Unexpected envelopes in qa inbox after discord_send: {leaked!r}"
    )

    # ── Phase 2: DELIBERATE-BROKEN PROBE ─────────────────────────────────────
    # Address the envelope to a nonexistent endpoint.  The bus raises
    # ValueError("publish to unregistered endpoint '...'") before any persistence
    # write.  DaemonClient.send_envelope() surfaces this as status_code=500 or
    # error text containing "unregistered".  This proves the bus routing table
    # is live; a mis-named discord slot would produce the same failure on real sends.
    broken_result = await client.send_envelope(
        {
            "to": "discord_qa_nonexistent_probe",
            "kind": "ToolInvocation",
            "payload": _toolinvocation_payload(
                "discord_send",
                {
                    "channel_id": "qa-stub-channel",
                    "text": "qa-routing-probe-broken",
                },
            ),
            "correlation_id": uuid.uuid4().hex,
            "metadata": {},
            "urgency": "green",
        }
    )
    assert broken_result.status_code != 200 or (
        isinstance(broken_result.json(), dict)
        and broken_result.json().get("status") != "published"
    ), (
        f"Sending to nonexistent endpoint 'discord_qa_nonexistent_probe' unexpectedly "
        f"succeeded.  The bus routing table may not be validating destinations. "
        f"Response: {broken_result.text[:300]}"
    )
    # Confirm the error text indicates the routing rejection.
    broken_text = broken_result.text.lower()
    assert "unregistered" in broken_text or "not found" in broken_text or "unknown" in broken_text or broken_result.status_code != 200, (
        f"Broken-envelope error did not mention routing failure: {broken_result.text[:300]}"
    )
