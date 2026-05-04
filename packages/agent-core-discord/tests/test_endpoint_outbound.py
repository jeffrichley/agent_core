"""Tests for DiscordEndpoint outbound tool surface (8 tools)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from agent_core_discord import send_retry as send_retry_mod
from agent_core_discord.endpoint import DiscordEndpoint

from agent_core.bus.envelope import (
    EndpointInfo,
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from tests.conftest import (
    _FakeChannel,
    _FakeDiscordClient,
    _FakeGuild,
    _FakeMessage,
    _FakeUser,
)


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


class _HTTP429Like(Exception):
    """Minimal duck-typed stand-in for discord.HTTPException on rate limit."""

    def __init__(self, *, retry_after: float = 0.0) -> None:
        super().__init__("429")
        self.status = 429
        self.retry_after = retry_after


class _Permanent429FromSecondSend(_FakeChannel):
    """First ``send`` succeeds; every later ``send`` (including retries) raises 429."""

    def __init__(
        self,
        *,
        id: str,
        name: str = "",
        channel_type: str = "text",
        guild_id: str | None = None,
    ) -> None:
        super().__init__(id=id, name=name, channel_type=channel_type, guild_id=guild_id)
        self._send_calls = 0

    async def send(  # type: ignore[override]
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: object | None = None,
        files: list | None = None,
    ) -> _FakeMessage:
        self._send_calls += 1
        if self._send_calls >= 2:
            raise _HTTP429Like(retry_after=0.0)
        return await super().send(content, embeds=embeds, reference=reference, files=files)


class _SecondSendFailsOnceChannel(_FakeChannel):
    """The second ``send`` invocation fails once with 429; retries succeed."""

    def __init__(
        self,
        *,
        id: str,
        name: str = "",
        channel_type: str = "text",
        guild_id: str | None = None,
    ) -> None:
        super().__init__(id=id, name=name, channel_type=channel_type, guild_id=guild_id)
        self._send_calls = 0

    async def send(  # type: ignore[override]
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: object | None = None,
        files: list | None = None,
    ) -> _FakeMessage:
        self._send_calls += 1
        if self._send_calls == 2:
            raise _HTTP429Like(retry_after=0.0)
        return await super().send(content, embeds=embeds, reference=reference, files=files)


async def _no_sleep(_: float) -> None:
    return None


async def _started(monkeypatch) -> tuple[DiscordEndpoint, _Recording, _FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _toolcall(tool: str, args: dict) -> ToolInvocationPayload:
    return ToolInvocationPayload(tool=tool, args=args)


def _envelope(env_id: str, frm: str, to: str, payload) -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=uuid.uuid4().hex,
        from_=frm,
        to=to,
        kind="ToolInvocation",
        payload=payload,
        created_at=datetime.now(UTC),
    )


# --- send ---


@pytest.mark.asyncio
async def test_send_publishes_text_to_channel(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall("send", {"channel_id": "200", "text": "hello"}),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "hello"
        # Acknowledgment back.
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert "message_id" in result
        assert result["message_ids"] == [result["message_id"]]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_with_reply_to_attaches_reference(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    original = _FakeMessage(id="m-orig", channel_id="200", content="please reply")
    ch._messages["m-orig"] = original
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {"channel_id": "200", "text": "ack", "reply_to": "m-orig"},
            ),
        )
        await ep.deliver(env)
        assert ch.sent[0]["reference"] is not None
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_with_reply_to_clears_pending_ack(monkeypatch):
    """If the inbound message had a 👀 ack, send with reply_to removes it."""
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    original = _FakeMessage(id="m-orig", channel_id="200")
    ch._messages["m-orig"] = original
    fake.add_channel(ch)
    # Simulate prior on_message having added the ack.
    await original.add_reaction("👀")
    ep._track_pending_ack("m-orig", "👀", "200")
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("send", {"channel_id": "200", "text": "ack", "reply_to": "m-orig"}),
        )
        await ep.deliver(env)
        assert "👀" not in original.reactions
        assert "m-orig" not in ep._pending_acks
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_with_embeds_passes_list(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {
                    "channel_id": "200",
                    "embeds": [{"title": "hi", "description": "world"}],
                },
            ),
        )
        await ep.deliver(env)
        assert ch.sent[0]["embeds"] is not None
        assert len(ch.sent[0]["embeds"]) == 1
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_validation_error_returns_error_ack(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("send", {"text": "missing channel_id"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert ack.payload.note.lower().startswith("error:")
        assert ack.urgency == "yellow"
    finally:
        await ep.stop()


# --- edit ---


@pytest.mark.asyncio
async def test_edit_replaces_content(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    msg = _FakeMessage(id="m-x", channel_id="200", content="old")
    ch._messages["m-x"] = msg
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "edit",
                {"channel_id": "200", "message_id": "m-x", "text": "new"},
            ),
        )
        await ep.deliver(env)
        assert any(edit.get("content") == "new" for edit in msg.edits)
        # Regression: when the agent supplies only text, embeds must NOT be
        # passed to msg.edit at all (real discord.py crashes on embeds=None).
        for edit in msg.edits:
            assert "embeds" not in edit, "edit() must omit embeds when not supplied"
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "edited"
    finally:
        await ep.stop()


# --- react ---


@pytest.mark.asyncio
async def test_react_adds_emoji(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    msg = _FakeMessage(id="m-r", channel_id="200")
    ch._messages["m-r"] = msg
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "react",
                {"channel_id": "200", "message_id": "m-r", "emoji": "🎉"},
            ),
        )
        await ep.deliver(env)
        assert "🎉" in msg.reactions
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "reacted"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_react_clears_pending_ack(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    msg = _FakeMessage(id="m-r", channel_id="200")
    ch._messages["m-r"] = msg
    fake.add_channel(ch)
    await msg.add_reaction("👀")
    ep._track_pending_ack("m-r", "👀", "200")
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "react",
                {"channel_id": "200", "message_id": "m-r", "emoji": "🎉"},
            ),
        )
        await ep.deliver(env)
        assert "👀" not in msg.reactions
        assert "m-r" not in ep._pending_acks
    finally:
        await ep.stop()


# --- dispatcher ---


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("frobnicate", {}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "unknown tool" in ack.payload.note.lower()
        assert ack.urgency == "yellow"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_splits_long_text_into_multiple_messages(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    long_text = "x" * 2500
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall("send", {"channel_id": "200", "text": long_text}),
        )
        await ep.deliver(env)
        assert len(ch.sent) >= 2
        for row in ch.sent:
            c = row.get("content") or ""
            assert len(c) <= 2000
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert len(result["message_ids"]) == len(ch.sent)
        assert result["message_id"] == result["message_ids"][-1]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_multipart_partial_after_chunk_retries_exhausted(monkeypatch):
    """Second chunk keeps failing with retryable errors → partial, earlier chunks kept."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(send_retry_mod, "DISCORD_SEND_MAX_ATTEMPTS", 2)
    ep, handle, fake = await _started(monkeypatch)
    ch = _Permanent429FromSecondSend(id="200")
    fake.add_channel(ch)
    long_text = "x" * 2500
    try:
        env = _envelope(
            "e-partial",
            "agent-test",
            "discord-test",
            _toolcall("send", {"channel_id": "200", "text": long_text}),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert ack.urgency == "yellow"
        result = json.loads(ack.payload.note)
        assert result["status"] == "partial"
        assert result["message_ids"] == [ch.sent[0]["message_id"]]
        assert "error" in result
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_multipart_retries_transient_429_then_succeeds(monkeypatch):
    """One retryable failure on a later chunk, then success → full multi-message send."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    ep, handle, fake = await _started(monkeypatch)
    ch = _SecondSendFailsOnceChannel(id="200")
    fake.add_channel(ch)
    long_text = "x" * 2500
    try:
        env = _envelope(
            "e-retry",
            "agent-test",
            "discord-test",
            _toolcall("send", {"channel_id": "200", "text": long_text}),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 2
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert ack.urgency == "green"
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert len(result["message_ids"]) == 2
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_partial_with_reply_to_does_not_clear_pending_ack(monkeypatch):
    """Partial multi-chunk send must not drop 👀 / pending ack (reply not complete)."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(send_retry_mod, "DISCORD_SEND_MAX_ATTEMPTS", 2)
    ep, handle, fake = await _started(monkeypatch)
    ch = _Permanent429FromSecondSend(id="200")
    original = _FakeMessage(id="m-orig", channel_id="200")
    ch._messages["m-orig"] = original
    await original.add_reaction("👀")
    ep._track_pending_ack("m-orig", "👀", "200")
    fake.add_channel(ch)
    long_text = "x" * 2500
    try:
        env = _envelope(
            "e-part-ack",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {"channel_id": "200", "text": long_text, "reply_to": "m-orig"},
            ),
        )
        await ep.deliver(env)
        assert "👀" in original.reactions
        assert "m-orig" in ep._pending_acks
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "partial"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_long_payload_splits(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = Envelope(
            id="e",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="z" * 2200),
            metadata={"discord": {"channel_id": "200"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) >= 2
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert ack.urgency == "green"
        result = json.loads(ack.payload.note)
        assert len(result["message_ids"]) >= 2
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_with_metadata_embeds_calls_channel_send_with_embeds(monkeypatch):
    """``metadata.discord.embeds`` (list of dicts) becomes ``channel.send(embeds=...)``.

    Real discord.py converts each dict into a ``discord.Embed`` via
    ``Embed.from_dict``; the fake channel just records what we pass. This
    test checks the round-trip — title, color, fields, footer all land.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        embeds_meta = [
            {
                "title": "🌅 Morning",
                "color": 15548997,
                "fields": [
                    {"name": "Today", "value": "Sunny day", "inline": False},
                ],
            },
            {
                "title": "📅 Calendar",
                "color": 255,
                "fields": [{"name": "9am", "value": "Standup", "inline": True}],
                "footer": {"text": "morning_brief • 2026-05-04T07:00:00+00:00"},
            },
        ]
        env = Envelope(
            id="e-embed",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text=""),
            metadata={"discord": {"channel_id": "200", "embeds": embeds_meta}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        sent = ch.sent[0]
        # No content because payload.text was empty.
        assert sent["content"] is None
        # Embeds were converted into discord.Embed objects (real discord.py is
        # installed); each Embed exposes ``.title`` / ``.color`` / ``.fields``.
        assert sent["embeds"] is not None
        assert len(sent["embeds"]) == 2
        e1, e2 = sent["embeds"]
        assert e1.title == "🌅 Morning"
        # discord.Color wraps the int decimal; .value returns the int back.
        assert e1.color is not None and e1.color.value == 15548997
        assert len(e1.fields) == 1
        assert e1.fields[0].name == "Today"
        assert e1.fields[0].value == "Sunny day"
        assert e1.fields[0].inline is False
        assert e2.title == "📅 Calendar"
        assert e2.color is not None and e2.color.value == 255
        assert e2.fields[0].inline is True
        assert e2.footer.text == "morning_brief • 2026-05-04T07:00:00+00:00"
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_with_embeds_and_text_sends_both(monkeypatch):
    """When payload.text is non-empty, embeds ride alongside on the same send."""
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = Envelope(
            id="e-embed-text",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="Header line"),
            metadata={
                "discord": {
                    "channel_id": "200",
                    "embeds": [{"title": "T", "color": 1, "fields": []}],
                },
            },
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        sent = ch.sent[0]
        assert sent["content"] == "Header line"
        assert sent["embeds"] is not None
        assert len(sent["embeds"]) == 1
        assert sent["embeds"][0].title == "T"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_uses_discord_metadata_channel_and_replies(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    incoming = _FakeMessage(id="m-in", channel_id="200", content="hello")
    ch._messages["m-in"] = incoming
    fake.add_channel(ch)
    try:
        env = Envelope(
            id="e",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="hi"),
            metadata={"discord": {"channel_id": "200", "message_id": "m-in"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "hi"
        assert ch.sent[0]["reference"] is not None
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert "message_ids" in result
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_uses_default_outbound_channel(monkeypatch):
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        outbound_channel_id="200",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    try:
        env = Envelope(
            id="e",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="fallback send"),
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "fallback send"
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert "message_ids" in result
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_without_channel_returns_error(monkeypatch):
    ep, handle, _fake = await _started(monkeypatch)
    try:
        env = Envelope(
            id="e",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="hi"),
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert ack.payload.note.lower().startswith("error:")
        assert "channel_id" in ack.payload.note
        assert ack.urgency == "yellow"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_in_reply_to_clears_ack_without_discord_message_id(monkeypatch):
    """Bus replies often only set in_reply_to; map inbound envelope id → Discord message."""
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ch = _FakeChannel(id="200")
    fake.add_channel(ch)
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        outbound_channel_id="200",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    user_msg = _FakeMessage(id="m-user", channel_id="200", content="ping")
    user_msg.author = _FakeUser(id="u1", name="alice", bot=False)
    user_msg.guild = type("G", (), {"id": "g1"})()
    user_msg.channel = ch
    ch._messages[str(user_msg.id)] = user_msg
    try:
        await fake.fire("on_message", user_msg)
        assert "👀" in user_msg.reactions
        inbound_id = handle.published[0].id
        out = Envelope(
            id="out1",
            correlation_id=uuid.uuid4().hex,
            in_reply_to=inbound_id,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="pong"),
            metadata={},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(out)
        assert "👀" not in user_msg.reactions
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "pong"
        assert ch.sent[0]["reference"] is not None
    finally:
        await ep.stop()


# --- fetch ---


@pytest.mark.asyncio
async def test_fetch_returns_recent_messages(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200")
    for i in range(3):
        m = _FakeMessage(id=f"m{i}", channel_id="200", content=f"msg {i}")
        m.author = type(
            "A", (), {"id": "100", "name": "alice", "bot": False, "display_name": "Alice"}
        )()
        m.created_at = datetime.now(UTC)
        m.embeds = []
        m.attachments = []
        ch._messages[m.id] = m
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("fetch", {"channel_id": "200", "limit": 10}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert isinstance(result, list)
        assert len(result) == 3
        for entry in result:
            assert "id" in entry
            assert "channel_id" in entry
            assert "content" in entry
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_fetch_unknown_channel_returns_error(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("fetch", {"channel_id": "missing", "limit": 5}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "not found" in ack.payload.note.lower()
    finally:
        await ep.stop()


# --- download_attachments ---


@pytest.mark.asyncio
async def test_download_attachments_saves_files(monkeypatch, tmp_path):
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    # Install a fake httpx-style downloader to avoid network.
    async def _fake_download(url: str) -> bytes:
        return b"data:" + url.encode()

    ep._download_url = _fake_download  # type: ignore[attr-defined]

    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [
                        "https://example.com/a.pdf",
                        "https://example.com/b.png",
                    ],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert "saved" in result
        assert len(result["saved"]) == 2
        # Files must exist on disk:
        assert (tmp_path / "att" / "m-att" / "a.pdf").exists()
        assert (tmp_path / "att" / "m-att" / "b.png").exists()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_rejects_path_traversal(monkeypatch, tmp_path):
    """A URL crafted to escape the target dir gets sanitized — never writes outside."""
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> bytes:
        return b"x"

    ep._download_url = _fake_download  # type: ignore[attr-defined]
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [
                        # URL-encoded traversal attempt.
                        "https://x/y/..%2F..%2F..%2Fevil.txt",
                        # Backslash-flavored attempt for Windows.
                        "https://x/y/..%5C..%5Cevil.txt",
                    ],
                },
            ),
        )
        await ep.deliver(env)
        # Every saved file MUST live inside target_dir — the sanitizer should
        # have stripped the traversal segments so what lands on disk is just
        # the basename, contained.
        target_dir = (tmp_path / "att" / "m-att").resolve()
        for child in target_dir.rglob("*"):
            assert child.resolve().is_relative_to(target_dir)
        # And nothing leaked into the parent (att/) or grandparent (tmp_path).
        siblings_of_target = [p for p in (tmp_path / "att").iterdir() if p.is_file()]
        assert siblings_of_target == []
        outside_att = [p for p in tmp_path.iterdir() if p.is_file()]
        assert outside_att == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_caps_long_filenames(monkeypatch, tmp_path):
    """A 5000-char URL tail produces a filename ≤ 128 chars."""
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> bytes:
        return b"x"

    ep._download_url = _fake_download  # type: ignore[attr-defined]
    try:
        long_tail = "A" * 5000
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [f"https://x/y/{long_tail}"],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        saved_name = result["saved"][0]["filename"]
        assert len(saved_name) <= 128
        # File exists with that capped name.
        assert (tmp_path / "att" / "m-att" / saved_name).exists()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_dedups_same_filename(monkeypatch, tmp_path):
    """Two URLs ending in the same trailing filename produce two distinct files."""
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> bytes:
        return url.encode()  # different bytes per URL

    ep._download_url = _fake_download  # type: ignore[attr-defined]
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [
                        "https://server-a/path/file.png",
                        "https://server-b/path/file.png",
                    ],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        names = {entry["filename"] for entry in result["saved"]}
        assert len(names) == 2
        # Both files exist with their distinct names.
        for entry in result["saved"]:
            assert (tmp_path / "att" / "m-att" / entry["filename"]).exists()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_empty_url_uses_uuid_fallback(monkeypatch, tmp_path):
    """A URL ending in / produces an attach-<hex> fallback name."""
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> bytes:
        return b"y"

    ep._download_url = _fake_download  # type: ignore[attr-defined]
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": ["https://x/y/"],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        name = result["saved"][0]["filename"]
        assert name.startswith("attach-")
        assert (tmp_path / "att" / "m-att" / name).exists()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_empty_urls_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "download_attachments",
                {
                    "channel_id": "200",
                    "message_id": "m-att",
                    "attachment_urls": [],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["saved"] == []
    finally:
        await ep.stop()


# --- list_channels ---


@pytest.mark.asyncio
async def test_list_channels_returns_all_when_no_guild_filter(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch1 = _FakeChannel(id="200", name="general", channel_type="text", guild_id="g1")
    ch2 = _FakeChannel(id="201", name="random", channel_type="text", guild_id="g1")
    g = _FakeGuild(id="g1", channels=[ch1, ch2])
    fake.add_guild(g)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("list_channels", {}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert len(result) == 2
        names = {entry["name"] for entry in result}
        assert names == {"general", "random"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_list_channels_filters_by_guild_id(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch1 = _FakeChannel(id="200", name="general", guild_id="g1")
    ch2 = _FakeChannel(id="300", name="other", guild_id="g2")
    g1 = _FakeGuild(id="g1", channels=[ch1])
    g2 = _FakeGuild(id="g2", channels=[ch2])
    fake.add_guild(g1)
    fake.add_guild(g2)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("list_channels", {"guild_id": "g1"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        names = {entry["name"] for entry in result}
        assert names == {"general"}
    finally:
        await ep.stop()


# --- get_channel_info ---


@pytest.mark.asyncio
async def test_get_channel_info_returns_metadata(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = _FakeChannel(id="200", name="general", guild_id="g1")
    ch.topic = "the main channel"
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("get_channel_info", {"channel_id": "200"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["id"] == "200"
        assert result["name"] == "general"
        assert result["topic"] == "the main channel"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_get_channel_info_unknown_returns_error(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("get_channel_info", {"channel_id": "missing"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "not found" in ack.payload.note.lower()
    finally:
        await ep.stop()
