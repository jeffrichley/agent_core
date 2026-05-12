"""Tests for DiscordEndpoint outbound tool surface."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from agent_core_discord import send_retry as send_retry_mod
from agent_core_discord.args import (
    _CreatePollArgs,
    _CreateScheduledEventArgs,
    _SendTypingArgs,
)
from agent_core_discord.briefing import (
    BRIEFING_COLOR_CRITICAL,
    BRIEFING_COLOR_WARNING,
    build_briefing_embeds,
)
from agent_core_discord.endpoint import DiscordEndpoint, _parse_iso_datetime
from agent_core_discord.testing.fakes import (
    FakeChannel,
    FakeDiscordClient,
    FakeGuild,
    FakeMessage,
    FakePoll,
    FakePollAnswer,
    FakeUser,
)

from agent_core.bus.envelope import (
    EndpointInfo,
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
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


class _Permanent429FromSecondSend(FakeChannel):
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
        poll: object | None = None,
    ) -> FakeMessage:
        self._send_calls += 1
        if self._send_calls >= 2:
            raise _HTTP429Like(retry_after=0.0)
        return await super().send(
            content, embeds=embeds, reference=reference, files=files, poll=poll
        )


class _SecondSendFailsOnceChannel(FakeChannel):
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
        poll: object | None = None,
    ) -> FakeMessage:
        self._send_calls += 1
        if self._send_calls == 2:
            raise _HTTP429Like(retry_after=0.0)
        return await super().send(
            content, embeds=embeds, reference=reference, files=files, poll=poll
        )


async def _no_sleep(_: float) -> None:
    return None


async def _started(monkeypatch) -> tuple[DiscordEndpoint, _Recording, FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = FakeDiscordClient()
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
    ch = FakeChannel(id="200")
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
    ch = FakeChannel(id="200")
    original = FakeMessage(id="m-orig", channel_id="200", content="please reply")
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
    ch = FakeChannel(id="200")
    original = FakeMessage(id="m-orig", channel_id="200")
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
    ch = FakeChannel(id="200")
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
    ch = FakeChannel(id="200")
    msg = FakeMessage(id="m-x", channel_id="200", content="old")
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
    ch = FakeChannel(id="200")
    msg = FakeMessage(id="m-r", channel_id="200")
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
    ch = FakeChannel(id="200")
    msg = FakeMessage(id="m-r", channel_id="200")
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
    ch = FakeChannel(id="200")
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
    original = FakeMessage(id="m-orig", channel_id="200")
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
    ch = FakeChannel(id="200")
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
    ch = FakeChannel(id="200")
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
    ch = FakeChannel(id="200")
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
async def test_textmessage_with_long_text_and_embeds_attaches_to_last_chunk(monkeypatch):
    """Long text + embeds: chunked text, embeds ride only on the LAST chunk.

    Regression coverage for the chunked-with-embeds path. Discord's
    content cap is 2000 chars, so 4500 chars of text forces multiple
    chunks. The pre-existing ``_send`` logic only attaches embeds to
    the final chunk — verifying that holds when the embeds come in
    via ``metadata.discord.embeds`` rather than via the ``send`` tool.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    long_text = "x" * 4500  # forces multiple chunks at the 1900 chunk-target
    try:
        env = Envelope(
            id="e-long-embed",
            correlation_id=uuid.uuid4().hex,
            from_="agent-test",
            to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text=long_text),
            metadata={
                "discord": {
                    "channel_id": "200",
                    "embeds": [{"title": "T", "color": 1, "fields": []}],
                },
            },
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) >= 2
        # All non-final chunks must NOT carry embeds (None or empty list).
        for row in ch.sent[:-1]:
            assert not row["embeds"], (
                f"non-final chunk should not carry embeds, got {row['embeds']!r}"
            )
        # Final chunk carries the embed.
        last = ch.sent[-1]
        assert last["embeds"] is not None
        assert len(last["embeds"]) == 1
        assert last["embeds"][0].title == "T"
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["status"] == "sent"
        assert len(result["message_ids"]) == len(ch.sent)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_textmessage_uses_discord_metadata_channel_and_replies(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    incoming = FakeMessage(id="m-in", channel_id="200", content="hello")
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
    fake = FakeDiscordClient()
    ch = FakeChannel(id="200")
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
    fake = FakeDiscordClient()
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        outbound_channel_id="200",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    user_msg = FakeMessage(id="m-user", channel_id="200", content="ping")
    user_msg.author = FakeUser(id="u1", name="alice", bot=False)
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
    ch = FakeChannel(id="200")
    for i in range(3):
        m = FakeMessage(id=f"m{i}", channel_id="200", content=f"msg {i}")
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
async def test_fetch_surfaces_poll_content(monkeypatch):
    """``message.poll`` is a first-class Discord attribute. ``fetch_messages``
    must serialize it into the returned message dict, otherwise agents
    reading channel state see polls as empty messages (caught on testbot
    2026-05-05 Phase 6 verb-parity smoke).
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200", guild_id="g1")
    poll = FakePoll(
        question_text="Lunch?",
        answers=[
            FakePollAnswer(id=1, text="Tacos", emoji="🌮", vote_count=3),
            FakePollAnswer(id=2, text="Pizza", emoji="🍕", vote_count=5),
        ],
        multiselect=False,
        duration_seconds=3600,
        is_finalised=False,
        total_votes=8,
    )
    m = FakeMessage(id="m-poll", channel_id="200", content="", poll=poll)
    m.author = type(
        "A", (), {"id": "100", "name": "alice", "bot": False, "display_name": "Alice"}
    )()
    m.created_at = datetime.now(UTC)
    m.embeds = []
    m.attachments = []
    ch._messages[m.id] = m

    # And a sibling non-poll message so we lock in that ``poll: None`` is
    # the right value for messages without polls.
    plain = FakeMessage(id="m-plain", channel_id="200", content="hi")
    plain.author = m.author
    plain.created_at = datetime.now(UTC)
    plain.embeds = []
    plain.attachments = []
    ch._messages[plain.id] = plain

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
        by_id = {entry["id"]: entry for entry in result}

        # Poll message: poll dict surfaced with the right shape.
        poll_entry = by_id["m-poll"]
        assert poll_entry["poll"] is not None
        assert poll_entry["poll"]["question"] == "Lunch?"
        assert poll_entry["poll"]["multiselect"] is False
        assert poll_entry["poll"]["duration_seconds"] == 3600
        assert poll_entry["poll"]["is_finalised"] is False
        assert poll_entry["poll"]["total_votes"] == 8
        answers = poll_entry["poll"]["answers"]
        assert {a["id"] for a in answers} == {1, 2}
        by_id_a = {a["id"]: a for a in answers}
        assert by_id_a[1]["text"] == "Tacos"
        assert by_id_a[1]["emoji"] == "🌮"
        assert by_id_a[1]["vote_count"] == 3
        assert by_id_a[2]["text"] == "Pizza"
        assert by_id_a[2]["vote_count"] == 5

        # Plain message: poll is None (NOT missing — explicit absence).
        assert by_id["m-plain"]["poll"] is None
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
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    # Install a fake httpx-style downloader to avoid network.
    async def _fake_download(url: str) -> tuple[bytes, str]:
        return b"data:" + url.encode(), ""

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
async def test_download_attachments_records_content_type_from_response(
    monkeypatch, tmp_path
):
    """The Content-Type header from the download response must land on the
    saved record. Caught on testbot 2026-05-05 Phase 6 verb-parity smoke —
    old code hard-coded ``content_type: ""`` even though the header was
    available, forcing callers into a redundant ``fetch_messages`` round
    trip just to learn what they downloaded.
    """
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    # Per-URL content type so we verify the value is plumbed through, not
    # uniformly hardcoded.
    types_by_url = {
        "https://example.com/a.pdf": "application/pdf",
        "https://example.com/b.png": "image/png",
    }

    async def _fake_download(url: str) -> tuple[bytes, str]:
        return b"data:" + url.encode(), types_by_url[url]

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
                    "message_id": "m-mime",
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
        saved = {item["filename"]: item for item in result["saved"]}
        assert saved["a.pdf"]["content_type"] == "application/pdf"
        assert saved["b.png"]["content_type"] == "image/png"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_download_attachments_rejects_path_traversal(monkeypatch, tmp_path):
    """A URL crafted to escape the target dir gets sanitized — never writes outside."""
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> tuple[bytes, str]:
        return b"x", ""

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
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> tuple[bytes, str]:
        return b"x", ""

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
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> tuple[bytes, str]:
        return url.encode(), ""  # different bytes per URL

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
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        attachments_dir=str(tmp_path / "att"),
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)

    async def _fake_download(url: str) -> tuple[bytes, str]:
        return b"y", ""

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
    fake = FakeDiscordClient()
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
    ch1 = FakeChannel(id="200", name="general", channel_type="text", guild_id="g1")
    ch2 = FakeChannel(id="201", name="random", channel_type="text", guild_id="g1")
    g = FakeGuild(id="g1", channels=[ch1, ch2])
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
    ch1 = FakeChannel(id="200", name="general", guild_id="g1")
    ch2 = FakeChannel(id="300", name="other", guild_id="g2")
    g1 = FakeGuild(id="g1", channels=[ch1])
    g2 = FakeGuild(id="g2", channels=[ch2])
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
    ch = FakeChannel(id="200", name="general", guild_id="g1")
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
        # guild_id must come from ``ch.guild.id`` (real discord.py shape)
        # not a fabricated flat ``ch.guild_id`` attribute. Caught on testbot
        # 2026-05-05 Phase 6 verb-parity smoke — old code returned "" here.
        assert result["guild_id"] == "g1"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_get_channel_info_dm_channel_returns_empty_guild_id(monkeypatch):
    """DM channels have ``ch.guild is None`` and should return empty
    guild_id (not crash, not return some sentinel). Locks the
    ``if guild is not None`` branch in the production fix."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="dm", name="", guild_id=None)
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall("get_channel_info", {"channel_id": "dm"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        result = json.loads(ack.payload.note)
        assert result["id"] == "dm"
        assert result["guild_id"] == ""
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


# --- cutover #03: Pepper verb aliases + extra tools ---


@pytest.mark.asyncio
async def test_send_discord_message_alias(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall("send_discord_message", {"channel_id": "200", "text": "hi"}),
        )
        await ep.deliver(env)
        assert ch.sent[0]["content"] == "hi"
    finally:
        await ep.stop()


def test_build_briefing_embeds_order_and_colors():
    embeds = build_briefing_embeds(
        date_line="2026-05-02",
        focus="Ship the cutover",
        calendar="1:1 with Jeff",
        critical_items=["Prod is down"],
        warning_items=["Flaky test"],
    )
    assert embeds[0]["title"] == "Daily briefing"
    names = [f["name"] for f in embeds[0]["fields"]]
    assert names == ["Focus", "Calendar"]
    assert embeds[1]["color"] == BRIEFING_COLOR_CRITICAL
    assert embeds[2]["color"] == BRIEFING_COLOR_WARNING


@pytest.mark.asyncio
async def test_send_briefing_sends_embeds(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall(
                "send_briefing",
                {
                    "channel_id": "200",
                    "date_line": "Saturday — test",
                    "focus": "A",
                    "calendar": "B",
                    "critical_items": ["c1"],
                    "warning_items": ["w1"],
                },
            ),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        emb = ch.sent[0]["embeds"]
        assert emb is not None and len(emb) == 3
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_create_poll_passes_poll_to_send(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall(
                "create_poll",
                {
                    "channel_id": "200",
                    "question": "Pick one?",
                    "answers": ["A", "B"],
                    "duration_hours": 24,
                },
            ),
        )
        await ep.deliver(env)
        assert ch.sent[0]["poll"] is not None
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_create_thread_invokes_channel(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200", guild_id="g1")
    fake.add_channel(ch)
    m = FakeMessage(id="m1", channel_id="200", content="root")
    ch._messages["m1"] = m
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall(
                "create_thread",
                {"channel_id": "200", "name": "side-topic", "message_id": "m1"},
            ),
        )
        await ep.deliver(env)
        assert len(ch.threads_created) == 1
        assert ch.threads_created[0]["name"] == "side-topic"
        assert ch.threads_created[0]["message_id"] == "m1"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_typing_ack(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall("send_typing", {"channel_id": "200", "duration_seconds": 0.5}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        body = json.loads(ack.payload.note)
        assert body["status"] == "typing_started"
        assert body["duration_seconds"] == 0.5
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_scheduled_event_create_list_cancel(monkeypatch):
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200", guild_id="g1")
    g = FakeGuild(id="g1", channels=[ch])
    fake.add_guild(g)
    try:
        start = "2026-06-01T15:00:00+00:00"
        end = "2026-06-01T16:00:00+00:00"
        env = _envelope(
            "e1",
            "agent-test",
            "discord-test",
            _toolcall(
                "create_scheduled_event",
                {
                    "guild_id": "g1",
                    "name": "Standup",
                    "start_time": start,
                    "end_time": end,
                    "entity_type": "external",
                    "location": "https://example.com/meet",
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        created = json.loads(ack.payload.note)
        eid = created["event_id"]

        env2 = _envelope(
            "e2",
            "agent-test",
            "discord-test",
            _toolcall("list_scheduled_events", {"guild_id": "g1"}),
        )
        await ep.deliver(env2)
        ack2 = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        listed = json.loads(ack2.payload.note)
        assert len(listed) == 1
        assert listed[0]["id"] == eid

        env3 = _envelope(
            "e3",
            "agent-test",
            "discord-test",
            _toolcall("cancel_scheduled_event", {"guild_id": "g1", "event_id": eid}),
        )
        await ep.deliver(env3)

        env4 = _envelope(
            "e4",
            "agent-test",
            "discord-test",
            _toolcall("list_scheduled_events", {"guild_id": "g1"}),
        )
        await ep.deliver(env4)
        ack4 = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        assert json.loads(ack4.payload.note) == []
    finally:
        await ep.stop()


# --- review-feedback hardening: trailing-Z parse + channel-type guard + validation ---


def test_parse_iso_datetime_trailing_z_parses():
    """Trailing 'Z' is treated as UTC."""
    dt = _parse_iso_datetime("start_time", "2026-05-04T07:00:00Z")
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 4
    assert dt.hour == 7
    assert dt.tzinfo is not None
    assert dt.utcoffset() == datetime(1970, 1, 1, tzinfo=UTC).utcoffset()


def test_parse_iso_datetime_does_not_mangle_embedded_z():
    """Embedded 'Z' (e.g. inside a fragment) must not get globally replaced.

    Before the fix, value="2026-05-04T07:00:00Z#anchorZ" became
    "2026-05-04T07:00:00+00:00#anchor+00:00" — a parse failure with a
    misleading "invalid datetime" message rooted in the rewrite, not the
    user's input. Now only the trailing 'Z' is rewritten, so the embedded
    'Z' survives intact and the input correctly fails as malformed (the
    point: the failure now reflects the real input, not our mangling).
    """
    from agent_core_discord.endpoint import _ToolError

    with pytest.raises(_ToolError) as exc:
        _parse_iso_datetime("start_time", "2026-05-04T07:00:00Z#anchorZ")
    # Surface the original value, unmangled.
    assert "2026-05-04T07:00:00Z#anchorZ" in str(exc.value)
    assert "anchor+00:00" not in str(exc.value)


@pytest.mark.asyncio
async def test_create_scheduled_event_stage_requires_stage_channel(monkeypatch):
    """entity_type='stage' against a text channel must surface a clear error."""
    from agent_core_discord.endpoint import _ToolError

    ep, handle, fake = await _started(monkeypatch)
    text_ch = FakeChannel(id="200", channel_type="text", guild_id="g1")
    g = FakeGuild(id="g1", channels=[text_ch])
    fake.add_guild(g)
    try:
        with pytest.raises(_ToolError) as exc:
            await ep._create_scheduled_event(
                _CreateScheduledEventArgs(
                    guild_id="g1",
                    name="StageTalk",
                    start_time="2026-06-01T15:00:00+00:00",
                    entity_type="stage",
                    channel_id="200",
                )
            )
        msg = str(exc.value)
        assert "stage" in msg
        assert "stage_voice" in msg
        assert "text" in msg
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_create_scheduled_event_voice_requires_voice_channel(monkeypatch):
    """entity_type='voice' against a text channel must surface a clear error."""
    from agent_core_discord.endpoint import _ToolError

    ep, handle, fake = await _started(monkeypatch)
    text_ch = FakeChannel(id="201", channel_type="text", guild_id="g1")
    g = FakeGuild(id="g1", channels=[text_ch])
    fake.add_guild(g)
    try:
        with pytest.raises(_ToolError) as exc:
            await ep._create_scheduled_event(
                _CreateScheduledEventArgs(
                    guild_id="g1",
                    name="VoiceTalk",
                    start_time="2026-06-01T15:00:00+00:00",
                    entity_type="voice",
                    channel_id="201",
                )
            )
        msg = str(exc.value)
        assert "voice" in msg
        assert "text" in msg
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_create_scheduled_event_stage_accepts_stage_voice_channel(monkeypatch):
    """entity_type='stage' against a stage_voice channel goes through."""
    ep, handle, fake = await _started(monkeypatch)
    stage_ch = FakeChannel(id="300", channel_type="stage_voice", guild_id="g2")
    g = FakeGuild(id="g2", channels=[stage_ch])
    fake.add_guild(g)
    try:
        result = await ep._create_scheduled_event(
            _CreateScheduledEventArgs(
                guild_id="g2",
                name="StageTalk",
                start_time="2026-06-01T15:00:00+00:00",
                entity_type="stage",
                channel_id="300",
            )
        )
        assert result["status"] == "created"
    finally:
        await ep.stop()


# --- I-2 / I-4: cross-field validator surface (rejection at the _v step) ---


def test_create_scheduled_event_validator_rejects_external_without_location():
    """external entity_type with empty location/end_time triggers ValidationError.

    The _v lambda translates ValidationError to _ToolError. We assert on
    pydantic's ValidationError directly here to pin the args contract — the
    _v translation path is exercised by test_send_validation_error_returns_error_ack
    and the dispatch tests below.
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError) as exc:
        _CreateScheduledEventArgs(
            guild_id="g1",
            name="Standup",
            start_time="2026-06-01T15:00:00+00:00",
            entity_type="external",
            location="",
            end_time="",
        )
    msg = str(exc.value)
    assert "external" in msg.lower()


def test_create_scheduled_event_validator_rejects_stage_without_channel_id():
    """stage entity_type with no channel_id triggers ValidationError."""
    import pydantic

    with pytest.raises(pydantic.ValidationError) as exc:
        _CreateScheduledEventArgs(
            guild_id="g1",
            name="StageTalk",
            start_time="2026-06-01T15:00:00+00:00",
            entity_type="stage",
            channel_id=None,
        )
    msg = str(exc.value)
    assert "channel_id" in msg


def test_create_poll_rejects_min_length_two_answers():
    """answers list must have >=2 entries — pydantic Field min_length=2."""
    import pydantic

    with pytest.raises(pydantic.ValidationError) as exc:
        _CreatePollArgs(
            channel_id="200",
            question="Pick one?",
            answers=["only one"],
        )
    msg = str(exc.value)
    # pydantic surfaces min_length / list info on the failure.
    assert "answers" in msg


def test_send_typing_rejects_duration_over_ten_seconds():
    """duration_seconds must be <= 10.0 — pydantic Field le=10.0."""
    import pydantic

    with pytest.raises(pydantic.ValidationError) as exc:
        _SendTypingArgs(channel_id="200", duration_seconds=99.0)
    msg = str(exc.value)
    assert "duration_seconds" in msg


@pytest.mark.asyncio
async def test_create_poll_dispatch_translates_validation_to_error_ack(monkeypatch):
    """Dispatch via _dispatch on bad args → error: ack (the _v translation path)."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "create_poll",
                {
                    "channel_id": "200",
                    "question": "Pick one?",
                    "answers": ["only one"],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        assert ack.payload.note.lower().startswith("error:")
        assert ack.urgency == "yellow"
    finally:
        await ep.stop()


@pytest.mark.parametrize("verb_name,args_extra", [
    ("send", {"text": "hi"}),
    ("edit", {"message_id": "m-edit", "text": "new"}),
    ("react", {"message_id": "m-react", "emoji": "👍"}),
    ("send_typing", {"duration_seconds": 0.5}),
    ("send_briefing", {
        "date_line": "test", "focus": "A", "calendar": "B",
        "critical_items": [], "warning_items": [],
    }),
])
@pytest.mark.asyncio
async def test_auto_echo_resolves_channel_via_in_reply_to_across_verbs(
    monkeypatch, verb_name, args_extra
):
    """Every verb that needs a channel routes via _resolve_channel_id."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    # Seed cache with an inbound pointing at channel 200.
    inbound = Envelope(
        id="inbound-1", correlation_id="c", from_="discord-test", to="agent-test",
        kind="TextMessage", payload=TextMessagePayload(text="hi"),
        metadata={"discord": {"channel_id": "200"}},
        created_at=datetime.now(UTC),
    )
    ep._record_inbound(inbound)
    # Pre-create message for verbs that need one.
    if verb_name in ("edit", "react"):
        ch._messages[args_extra["message_id"]] = FakeMessage(
            id=args_extra["message_id"], channel_id="200",
        )
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall(verb_name, {**args_extra}),  # NO channel_id
        )
        env.in_reply_to = "inbound-1"
        await ep.deliver(env)
        # The verb routed to channel 200 via the cache hit (no error ack).
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][-1]
        assert not ack.payload.note.lower().startswith("error:"), \
            f"verb={verb_name} should have resolved channel via in_reply_to"
    finally:
        await ep.stop()


# --- regression locks for pre-#83 routing behavior ---


@pytest.mark.asyncio
async def test_explicit_channel_id_still_routes_correctly_unchanged(monkeypatch):
    """The pre-#83 explicit-channel_id path keeps working."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="999")
    fake.add_channel(ch)
    try:
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall("send", {"channel_id": "999", "text": "explicit"}),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "explicit"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_reply_tool_inheritance_path_unchanged(monkeypatch):
    """reply()-style outbounds (channel_id pre-set via reply inheritance) work."""
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="888")
    fake.add_channel(ch)
    try:
        out = Envelope(
            id="reply-1", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage", payload=TextMessagePayload(text="via reply"),
            metadata={"discord": {"channel_id": "888"}},  # pre-set by reply()
            created_at=datetime.now(UTC),
        )
        await ep.deliver(out)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "via reply"
    finally:
        await ep.stop()


@pytest.mark.parametrize("verb_name,args_extra", [
    ("send", {"text": "hi"}),
    ("edit", {"message_id": "m-edit", "text": "new"}),
    ("react", {"message_id": "m-react", "emoji": "👍"}),
    ("send_typing", {"duration_seconds": 0.5}),
    ("send_briefing", {
        "date_line": "test", "focus": "A", "calendar": "B",
        "critical_items": [], "warning_items": [],
    }),
])
@pytest.mark.asyncio
async def test_auto_echo_hard_errors_uniformly_across_verbs(
    monkeypatch, verb_name, args_extra
):
    """When _resolve_channel_id raises _ToolError, every channel-requiring verb
    produces a yellow Acknowledgment with a unified error message substring.

    Complements test_resolve_error_message_is_unified_across_sub_causes (which
    locks the resolver itself); this test locks the verb-level Ack shape so no
    verb can silently swallow or re-format the error.
    """
    ep, handle, fake = await _started(monkeypatch)
    try:
        # No channel_id in args, no in_reply_to on envelope — _resolve_channel_id must fail.
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall(verb_name, {**args_extra}),
        )
        # Deliberately no env.in_reply_to set (remains None).
        await ep.deliver(env)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks, f"verb={verb_name}: expected at least one Acknowledgment"
        ack = acks[-1]
        note = (ack.payload.note or "").lower()
        assert ack.urgency == "yellow", (
            f"verb={verb_name}: expected urgency='yellow', got {ack.urgency!r}"
        )
        assert note.startswith("error:"), (
            f"verb={verb_name}: note should start with 'error:', got {note!r}"
        )
        assert "cannot determine channel" in note, (
            f"verb={verb_name}: note should contain 'cannot determine channel', got {note!r}"
        )
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_text_message_with_single_attachment_uploads_to_discord(monkeypatch, tmp_path):
    """Issue #64 named-symptom regression lock: payload.attachments=[{path: ...}]
    reaches Discord via channel.send(files=[discord.File(...)]).
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    pdf = tmp_path / "briefing.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    from datetime import UTC, datetime

    from agent_core.bus.envelope import Envelope, FileAttachment, TextMessagePayload
    try:
        env = Envelope(
            id="msg-attach", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="briefing attached",
                attachments=[FileAttachment(path=str(pdf))],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        # Outbound landed.
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "briefing attached"
        # files arg was populated on the channel.send call.
        sent_files = ch.sent[0]["files"]
        assert sent_files is not None and len(sent_files) == 1
        # FakeMessage exposes the attachment with basename filename.
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert len(msg.attachments) == 1
        assert msg.attachments[0].filename == "briefing.pdf"
        # No error ack published.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert all(not a.payload.note.lower().startswith("error:") for a in acks)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_text_message_with_multiple_attachments_uploads_all(monkeypatch, tmp_path):
    """Multi-file delivery preserves input order on the way to Discord."""
    import os

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    paths = []
    for name in ("alpha.pdf", "beta.pdf", "gamma.pdf"):
        p = tmp_path / name
        p.write_bytes(b"data")
        paths.append(str(p))
    from agent_core.bus.envelope import FileAttachment
    try:
        env = Envelope(
            id="msg-multi", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="three files",
                attachments=[FileAttachment(path=p) for p in paths],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert len(msg.attachments) == 3
        # Order preserved end-to-end.
        assert [a.filename for a in msg.attachments] == [
            os.path.basename(p) for p in paths
        ]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_text_message_attachment_uses_basename_as_filename(monkeypatch, tmp_path):
    """discord.File(path) defaults filename to os.path.basename(path).
    Lock this default so a future refactor that strips file extension or
    rewrites the filename is loud, not silent.
    """
    import os
    from datetime import UTC, datetime

    from agent_core.bus.envelope import Envelope, FileAttachment, TextMessagePayload

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    deep_path = tmp_path / "deeply" / "nested" / "weird name.pdf"
    deep_path.parent.mkdir(parents=True)
    deep_path.write_bytes(b"x")
    try:
        env = Envelope(
            id="msg-basename", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="basename check",
                attachments=[FileAttachment(path=str(deep_path))],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert msg.attachments[0].filename == os.path.basename(str(deep_path))
        assert msg.attachments[0].filename == "weird name.pdf"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_text_message_attachment_file_not_found_yields_yellow_ack_error(monkeypatch):
    """Nonexistent path → discord.File raises FileNotFoundError →
    existing _send exception path catches → yellow Ack with note prefix
    'error:'. No Discord message published.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    from datetime import UTC, datetime

    from agent_core.bus.envelope import Envelope, FileAttachment, TextMessagePayload
    try:
        env = Envelope(
            id="msg-missing", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="missing file",
                attachments=[FileAttachment(path="/this/path/does/not/exist.pdf")],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        # No Discord message published (the send never happened).
        assert len(ch.sent) == 0
        # Yellow Ack with error prefix.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert len(acks) == 1
        assert acks[0].urgency == "yellow"
        assert acks[0].payload.note.lower().startswith("error:")
    finally:
        await ep.stop()
