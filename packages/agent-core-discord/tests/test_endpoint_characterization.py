"""Characterization tests for DiscordEndpoint (endpoint.py).

Golden-master safety net for the F-B6 split (issue #406 / #439).
All tests must remain green against the unmodified endpoint.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from agent_core_discord.endpoint import (
    DiscordEndpoint,
    _TOOL_ALIASES,
    _canonical_tool,
)
from agent_core_discord.testing.fakes import (
    FakeChannel,
    FakeDiscordClient,
    FakeGuild,
    FakeMessage,
    FakeScheduledEvent,
    FakeUser,
)

from agent_core.bus.envelope import (
    EndpointInfo,
    Envelope,
    ToolInvocationPayload,
)
from agent_core.bus.protocol import EndpointUnavailable


# ---------------------------------------------------------------------------
# Shared helpers — mirrors the pattern in test_endpoint_outbound.py
# ---------------------------------------------------------------------------


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []
        self.acked: set[str] = set()

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None:
        self.acked.add(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []

    def spawn(self, coro, *, name=None):
        import asyncio
        return asyncio.create_task(coro, name=name)


def _toolcall(tool: str, args: dict) -> ToolInvocationPayload:
    return ToolInvocationPayload(tool=tool, args=args)


def _envelope(tool: str, args: dict, *, from_: str = "char-agent", to: str = "char-ep") -> Envelope:
    return Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        from_=from_,
        to=to,
        kind="ToolInvocation",
        payload=_toolcall(tool, args),
        created_at=datetime.now(UTC),
    )


async def _started_full(monkeypatch) -> tuple[DiscordEndpoint, _Recording, FakeDiscordClient]:
    """Start endpoint with a fully-equipped fake environment for routing tests.

    Pre-seeds:
    - FakeChannel "char-ch" with FakeMessage "char-msg"
    - FakeGuild "char-guild" with a FakeScheduledEvent "char-ev"
    - ep._download_url patched to a no-op (avoids real network in download_attachments)
    """
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = FakeDiscordClient()

    ch = FakeChannel(id="char-ch", name="general", guild_id="char-guild")
    msg = FakeMessage(id="char-msg", channel_id="char-ch", content="hi")
    msg.author = FakeUser(id="u1", name="tester")
    msg.guild = type("G", (), {"id": "char-guild"})()
    ch._messages["char-msg"] = msg

    guild = FakeGuild(id="char-guild", channels=[ch])
    ev = FakeScheduledEvent(id="char-ev", name="Char Event", guild=guild)
    guild._scheduled["char-ev"] = ev
    fake.add_guild(guild)

    ep = DiscordEndpoint(
        name="char-ep",
        target="char-agent",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _noop_download(url: str) -> tuple[bytes, str]:
        return b"data", ""

    ep._download_url = _noop_download  # type: ignore[attr-defined]
    return ep, handle, fake


# ---------------------------------------------------------------------------
# 1. _TOOL_ALIASES table — exact contents
# ---------------------------------------------------------------------------


def test_tool_aliases_exact_table():
    """Pin the exact keys and values of _TOOL_ALIASES as of the F-B6 baseline."""
    assert _TOOL_ALIASES == {
        "send_discord_message": "send",
        "discord_send": "discord_send",
        "edit_message": "edit",
        "add_reaction": "react",
        "fetch_messages": "fetch",
    }


# ---------------------------------------------------------------------------
# 2. _canonical_tool() — alias resolution and identity passthrough
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias,expected_canonical", [
    ("send_discord_message", "send"),
    ("discord_send", "discord_send"),
    ("edit_message", "edit"),
    ("add_reaction", "react"),
    ("fetch_messages", "fetch"),
])
def test_canonical_tool_maps_each_alias(alias, expected_canonical):
    assert _canonical_tool(alias) == expected_canonical


def test_canonical_tool_returns_unknown_key_unchanged():
    assert _canonical_tool("frobnicate") == "frobnicate"
    assert _canonical_tool("") == ""


# ---------------------------------------------------------------------------
# 3. Not-started shape — exact exception type and message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_not_started_raises_endpoint_unavailable_with_name_in_message():
    """deliver() on a non-started endpoint must raise EndpointUnavailable whose
    str contains f"discord '{ep.name}' not started"."""
    ep = DiscordEndpoint(name="my-bot", target="agent", token_env="X_TOK")
    env = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to="my-bot",
        kind="ToolInvocation",
        payload=_toolcall("send", {}),
        created_at=datetime.now(UTC),
    )
    with pytest.raises(EndpointUnavailable) as exc_info:
        await ep.deliver(env)
    assert "discord 'my-bot' not started" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Acknowledgment emission shape — all five fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ack_emission_shape_all_fields_match_inbound(monkeypatch):
    """Ack envelope must carry: kind, correlation_id, in_reply_to, to, payload.of
    all matching the inbound ToolInvocation envelope."""
    ep, handle, fake = await _started_full(monkeypatch)
    try:
        inbound = Envelope(
            id="shape-env-id",
            correlation_id="shape-corr-id",
            from_="shape-agent",
            to="char-ep",
            kind="ToolInvocation",
            payload=_toolcall("send", {"channel_id": "char-ch", "text": "hi"}),
            created_at=datetime.now(UTC),
        )
        await ep.deliver(inbound)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks, "deliver() must publish at least one Acknowledgment"
        ack = acks[0]
        assert ack.kind == "Acknowledgment"
        assert ack.correlation_id == "shape-corr-id"
        assert ack.in_reply_to == "shape-env-id"
        assert ack.to == "shape-agent"
        assert ack.payload.of == "shape-env-id"
    finally:
        await ep.stop()


# ---------------------------------------------------------------------------
# 5. Tool routing — all 15 dispatch keys (parametrized)
# ---------------------------------------------------------------------------
#
# For each dispatch key, this test:
#   (a) delivers a ToolInvocation envelope with minimal valid args, and
#   (b) asserts the Ack's JSON note contains the expected response key+value
#       (or that the result is a list, for tools that return lists).
#
# "expected_key" meanings:
#   "status"  → result["status"] == expected_value
#   "_list_"  → isinstance(result, list)
#   "saved"   → "saved" in result
#   "id"      → "id" in result


@pytest.mark.parametrize("tool_name,tool_args,expected_key,expected_value", [
    (
        "send",
        {"channel_id": "char-ch", "text": "hello"},
        "status", "sent",
    ),
    (
        "discord_send",
        {"channel_id": "char-ch", "text": "hello"},
        "status", "sent",
    ),
    (
        "edit",
        {"channel_id": "char-ch", "message_id": "char-msg", "text": "updated"},
        "status", "edited",
    ),
    (
        "react",
        {"channel_id": "char-ch", "message_id": "char-msg", "emoji": "👍"},
        "status", "reacted",
    ),
    (
        "fetch",
        {"channel_id": "char-ch", "limit": 5},
        "_list_", None,
    ),
    (
        "download_attachments",
        {"channel_id": "char-ch", "message_id": "char-msg", "attachment_urls": []},
        "saved", None,
    ),
    (
        "list_channels",
        {},
        "_list_", None,
    ),
    (
        "get_channel_info",
        {"channel_id": "char-ch"},
        "id", None,
    ),
    (
        "send_briefing",
        {
            "channel_id": "char-ch",
            "date_line": "2026-07-20",
            "focus": "pin",
            "calendar": "",
            "critical_items": [],
            "warning_items": [],
        },
        "status", "sent",
    ),
    (
        "create_poll",
        {"channel_id": "char-ch", "question": "Pick one?", "answers": ["A", "B"]},
        "status", "sent",
    ),
    (
        "create_scheduled_event",
        {
            "guild_id": "char-guild",
            "name": "Standup",
            "start_time": "2026-09-01T15:00:00+00:00",
            "end_time": "2026-09-01T16:00:00+00:00",
            "entity_type": "external",
            "location": "https://example.com/meet",
        },
        "status", "created",
    ),
    (
        "cancel_scheduled_event",
        {"guild_id": "char-guild", "event_id": "char-ev"},
        "status", "cancelled",
    ),
    (
        "list_scheduled_events",
        {"guild_id": "char-guild"},
        "_list_", None,
    ),
    (
        "create_thread",
        {"channel_id": "char-ch", "name": "side-topic"},
        "status", "created",
    ),
    (
        "send_typing",
        {"channel_id": "char-ch", "duration_seconds": 0.5},
        "status", "typing_started",
    ),
], ids=[
    "send", "discord_send", "edit", "react", "fetch",
    "download_attachments", "list_channels", "get_channel_info",
    "send_briefing", "create_poll", "create_scheduled_event",
    "cancel_scheduled_event", "list_scheduled_events",
    "create_thread", "send_typing",
])
@pytest.mark.asyncio
async def test_dispatch_key_routes_to_handler_and_returns_expected_response(
    monkeypatch, tool_name, tool_args, expected_key, expected_value
):
    ep, handle, fake = await _started_full(monkeypatch)
    try:
        env = _envelope(tool_name, tool_args)
        await ep.deliver(env)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks, f"tool={tool_name!r}: expected at least one Acknowledgment"
        ack = acks[-1]
        note = ack.payload.note
        assert not note.lower().startswith("error:"), (
            f"tool={tool_name!r}: unexpected error ack: {note!r}"
        )
        result = json.loads(note)
        if expected_key == "_list_":
            assert isinstance(result, list), (
                f"tool={tool_name!r}: expected list response, got {type(result)}"
            )
        elif expected_key == "saved":
            assert "saved" in result, (
                f"tool={tool_name!r}: expected 'saved' key in {result!r}"
            )
        elif expected_key == "id":
            assert "id" in result, (
                f"tool={tool_name!r}: expected 'id' key in {result!r}"
            )
        else:
            assert result.get("status") == expected_value, (
                f"tool={tool_name!r}: expected status={expected_value!r}, got {result!r}"
            )
    finally:
        await ep.stop()


# ---------------------------------------------------------------------------
# 6. Alias routing — 4 non-identity aliases route to the same handler
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias,canonical_tool,tool_args,expected_status", [
    (
        "send_discord_message",
        "send",
        {"channel_id": "char-ch", "text": "via alias"},
        "sent",
    ),
    (
        "edit_message",
        "edit",
        {"channel_id": "char-ch", "message_id": "char-msg", "text": "edited via alias"},
        "edited",
    ),
    (
        "add_reaction",
        "react",
        {"channel_id": "char-ch", "message_id": "char-msg", "emoji": "🎉"},
        "reacted",
    ),
    (
        "fetch_messages",
        "fetch",
        {"channel_id": "char-ch", "limit": 5},
        None,  # fetch returns a list, not a status dict
    ),
])
@pytest.mark.asyncio
async def test_alias_routing_reaches_same_handler_as_canonical(
    monkeypatch, alias, canonical_tool, tool_args, expected_status
):
    """Each alias routes to the canonical handler and returns the same response shape."""
    ep, handle, fake = await _started_full(monkeypatch)
    try:
        env = _envelope(alias, tool_args)
        await ep.deliver(env)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks, f"alias={alias!r}: expected at least one Acknowledgment"
        ack = acks[-1]
        note = ack.payload.note
        assert not note.lower().startswith("error:"), (
            f"alias={alias!r}: unexpected error ack: {note!r}"
        )
        result = json.loads(note)
        if expected_status is None:
            # fetch returns a list
            assert isinstance(result, list), (
                f"alias={alias!r}: expected list response, got {type(result)}"
            )
        else:
            assert result.get("status") == expected_status, (
                f"alias={alias!r}: expected status={expected_status!r}, got {result!r}"
            )
    finally:
        await ep.stop()


# ---------------------------------------------------------------------------
# 7. Urgency mapping — success → green, _ToolError → yellow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_dispatch_produces_green_urgency(monkeypatch):
    """A successful tool dispatch publishes an Acknowledgment with urgency='green'."""
    ep, handle, fake = await _started_full(monkeypatch)
    try:
        env = _envelope("send", {"channel_id": "char-ch", "text": "ok"})
        await ep.deliver(env)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks
        assert acks[-1].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_tool_error_dispatch_produces_yellow_urgency(monkeypatch):
    """An unknown tool name triggers _ToolError inside _dispatch, which produce
    an Acknowledgment with urgency='yellow'."""
    ep, handle, fake = await _started_full(monkeypatch)
    try:
        env = _envelope("frobnicate_does_not_exist", {})
        await ep.deliver(env)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks
        ack = acks[-1]
        assert ack.urgency == "yellow"
        assert ack.payload.note.lower().startswith("error:")
    finally:
        await ep.stop()
