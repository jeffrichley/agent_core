"""Tests for DiscordEndpoint inbound handlers (on_message, on_reaction_add)."""

from __future__ import annotations

import asyncio

import pytest
from agent_core_discord.endpoint import DiscordEndpoint

from agent_core.bus.envelope import EndpointInfo, Envelope, EventPayload, TextMessagePayload
from agent_core_discord.testing.fakes import FakeChannel, FakeDiscordClient, FakeMessage, FakeUser


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


async def _start_endpoint(
    monkeypatch, *, access_path=None
) -> tuple[DiscordEndpoint, _Recording, FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        access_config_path=access_path,
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _msg(
    *, id="m1", channel_id="200", content="hi", author_id="100", is_bot=False, attachments=None
):
    msg = FakeMessage(id=id, channel_id=channel_id, content=content)
    msg.author = FakeUser(id=author_id, name="user", bot=is_bot, display_name="User")
    msg.guild = type("G", (), {"id": "guild-1"})() if channel_id != "dm" else None
    msg.channel = FakeChannel(id=channel_id)
    msg.attachments = attachments or []
    return msg


@pytest.mark.asyncio
async def test_on_message_publishes_text_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(id="m1", content="hello world")
    msg.channel = fake.get_channel("200")  # use registered channel
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.to == "agent-test"
        assert env.kind == "TextMessage"
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "hello world"
        assert env.metadata["discord"]["channel_id"] == "200"
        assert env.metadata["discord"]["message_id"] == "m1"
        assert env.metadata["discord"]["author_id"] == "100"
        assert env.metadata["discord"]["is_dm"] is False
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_drops_messages_from_bots(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(content="hi", is_bot=True)
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_drops_messages_from_self(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(content="hi", author_id=fake.user.id)
    # Author must be the bot itself.
    msg.author = fake.user
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_adds_ack_reaction(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(id="m-ack")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert "👀" in msg.reactions
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_holds_typing_until_ack_cleared(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    ch = FakeChannel(id="200")
    fake.add_channel(ch)
    msg = _msg(id="m-typing")
    msg.channel = ch
    try:
        await fake.fire("on_message", msg)
        await asyncio.sleep(0.08)
        assert ch._typing_count >= 1
        await ep._clear_pending_ack(ch, "m-typing")
        await asyncio.sleep(0.35)
        assert ch._typing_count == 0
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_attachments_metadata(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    att = type("A", (), {})()
    att.filename = "file.pdf"
    att.url = "https://example.com/file.pdf"
    att.content_type = "application/pdf"
    att.size = 1024
    msg = _msg(id="m-att", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        assert env.metadata["attachments"] == [
            {
                "filename": "file.pdf",
                "url": "https://example.com/file.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
            }
        ]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_respects_access_gate_dm_deny(monkeypatch, tmp_path):
    import json

    access = tmp_path / "access.json"
    access.write_text(json.dumps({"dmPolicy": "deny"}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(FakeChannel(id="dm"))
    msg = _msg(id="d1", channel_id="dm", content="hello via DM")
    msg.guild = None
    msg.channel = fake.get_channel("dm")
    try:
        await fake.fire("on_message", msg)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_respects_channel_allowlist(monkeypatch, tmp_path):
    import json

    access = tmp_path / "access.json"
    access.write_text(json.dumps({"dmPolicy": "open", "channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(FakeChannel(id="200"))
    fake.add_channel(FakeChannel(id="999"))

    msg_in = _msg(id="m-in", channel_id="200")
    msg_in.channel = fake.get_channel("200")
    msg_out = _msg(id="m-out", channel_id="999")
    msg_out.channel = fake.get_channel("999")
    try:
        await fake.fire("on_message", msg_in)
        await fake.fire("on_message", msg_out)
        ids = {e.metadata["discord"]["message_id"] for e in handle.published}
        assert ids == {"m-in"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_dm_inbound_is_marked(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="dm"))
    msg = _msg(id="d-1", channel_id="dm")
    msg.guild = None
    msg.channel = fake.get_channel("dm")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        assert env.metadata["discord"]["is_dm"] is True
        assert env.metadata["discord"]["guild_id"] == ""
    finally:
        await ep.stop()


class FakeReaction:
    def __init__(self, *, emoji: str, message: FakeMessage):
        self.emoji = emoji
        self.message = message


@pytest.mark.asyncio
async def test_on_reaction_add_publishes_event_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    bot_msg = FakeMessage(id="bot-msg-1", channel_id="200", content="hello from bot")
    bot_msg.author = fake.user
    bot_msg.guild = type("G", (), {"id": "guild-1"})()
    bot_msg.channel = fake.get_channel("200")
    fake._channels["200"]._messages["bot-msg-1"] = bot_msg

    user = FakeUser(id="100", name="alice", display_name="Alice")
    reaction = FakeReaction(emoji="👍", message=bot_msg)
    try:
        await fake.fire("on_reaction_add", reaction, user)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.kind == "Event"
        assert isinstance(env.payload, EventPayload)
        assert env.payload.type == "discord.reaction_add"
        assert env.payload.data["emoji"] == "👍"
        assert env.payload.data["message_id"] == "bot-msg-1"
        assert env.payload.data["channel_id"] == "200"
        assert env.payload.data["user_id"] == "100"
        assert env.payload.data["user_display_name"] == "Alice"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_drops_self_reactions(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    bot_msg = FakeMessage(id="bm", channel_id="200")
    bot_msg.author = fake.user
    bot_msg.guild = type("G", (), {"id": "g"})()
    bot_msg.channel = fake.get_channel("200")

    reaction = FakeReaction(emoji="👍", message=bot_msg)
    # The reaction is from the bot itself.
    try:
        await fake.fire("on_reaction_add", reaction, fake.user)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_drops_other_bots(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = FakeMessage(id="m", channel_id="200")
    msg.author = fake.user
    msg.guild = type("G", (), {"id": "g"})()
    msg.channel = fake.get_channel("200")

    other_bot = FakeUser(id="999", name="other-bot", bot=True)
    reaction = FakeReaction(emoji="👍", message=msg)
    try:
        await fake.fire("on_reaction_add", reaction, other_bot)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_drops_ack_emoji(monkeypatch):
    """The bot's own 👀 ack reaction should never bounce back as an event."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = FakeMessage(id="m", channel_id="200")
    msg.author = fake.user
    msg.guild = type("G", (), {"id": "g"})()
    msg.channel = fake.get_channel("200")

    user = FakeUser(id="100")
    reaction = FakeReaction(emoji="👀", message=msg)  # the ack emoji
    try:
        await fake.fire("on_reaction_add", reaction, user)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_dm_context(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="dm"))
    msg = FakeMessage(id="m", channel_id="dm")
    msg.author = fake.user
    msg.guild = None  # DM
    msg.channel = fake.get_channel("dm")

    user = FakeUser(id="100", name="alice", display_name="Alice")
    reaction = FakeReaction(emoji="🔥", message=msg)
    try:
        await fake.fire("on_reaction_add", reaction, user)
        env = handle.published[0]
        assert env.payload.data["guild_id"] == ""
        assert env.payload.data["channel_id"] == "dm"
    finally:
        await ep.stop()


# Engagement-event listeners (poll votes, message edits/deletes). Wired
# against discord.py's *raw* dispatch points so the agent gets notified
# even after the underlying message has been evicted from the client's
# message cache — which is the common case for long-running agents.
# Caught on testbot 2026-05-05 Phase 6 verification: a vote on a
# bot-posted poll never reached the agent because no listener was wired.

class FakeRawPollVote:
    """Mirrors ``discord.RawPollVoteActionEvent`` shape (raw_models.py:528).

    Attributes match real discord.py exactly — only IDs, no resolved
    objects. ``guild_id`` is ``Optional[int]`` (None for DMs).
    """

    def __init__(
        self,
        *,
        message_id: int,
        channel_id: int,
        user_id: int,
        guild_id: int | None,
        answer_id: int,
    ) -> None:
        self.message_id = message_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.guild_id = guild_id
        self.answer_id = answer_id


class FakeRawMessageDelete:
    """Mirrors ``discord.RawMessageDeleteEvent`` shape (raw_models.py:85)."""

    def __init__(
        self, *, message_id: int, channel_id: int, guild_id: int | None
    ) -> None:
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id


class FakeRawMessageUpdate:
    """Mirrors ``discord.RawMessageUpdateEvent`` shape (raw_models.py:140).

    Real discord.py also exposes ``data`` (raw gateway dict) and
    ``cached_message`` (Optional[Message]); the adapter only reads IDs.
    """

    def __init__(
        self, *, message_id: int, channel_id: int, guild_id: int | None
    ) -> None:
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_publishes_event_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))
    raw = FakeRawPollVote(
        message_id=1501308103499452467,
        channel_id=1499028901257805874,
        user_id=100,
        guild_id=1229523821820772392,
        answer_id=1,
    )
    try:
        await fake.fire("on_raw_poll_vote_add", raw)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.kind == "Event"
        assert isinstance(env.payload, EventPayload)
        assert env.payload.type == "discord.poll_vote_add"
        assert env.payload.data["message_id"] == "1501308103499452467"
        assert env.payload.data["channel_id"] == "1499028901257805874"
        assert env.payload.data["user_id"] == "100"
        assert env.payload.data["guild_id"] == "1229523821820772392"
        assert env.payload.data["answer_id"] == 1
        # Symmetry with discord.reaction_add: poll-vote events also
        # surface the voter's display name when the User is in the
        # client's cache. testbot 2026-05-05 round-2 verification
        # surfaced this asymmetry as Obs 1.
        assert env.payload.data["user_display_name"] == "Alice"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_falls_back_to_fetch_user_on_cache_miss(
    monkeypatch,
):
    """When ``get_user`` misses, the handler falls through to
    ``fetch_user`` (HTTP). discord.py's ``_users`` is a
    ``WeakValueDictionary`` and ``fetch_user`` doesn't auto-cache, so
    raw poll vote events would otherwise return empty display names for
    every voter who hasn't recently messaged or reacted (the common
    case caught on testbot 2026-05-05 round-3 verification)."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    # User is reachable ONLY via fetch_user (HTTP). Not in get_user cache.
    fake.add_remote_user(FakeUser(id="100", name="alice", display_name="Alice"))
    raw = FakeRawPollVote(
        message_id=1, channel_id=2, user_id=100, guild_id=3, answer_id=1
    )
    try:
        await fake.fire("on_raw_poll_vote_add", raw)
        env = handle.published[0]
        assert env.payload.data["user_id"] == "100"
        assert env.payload.data["user_display_name"] == "Alice"
        assert fake.fetch_user_call_count == 1
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_uses_local_cache_to_avoid_repeated_fetch_user(
    monkeypatch,
):
    """Once a user has been resolved (via either ``get_user`` or
    ``fetch_user``), the adapter must remember them — Jeff's exact ask:
    \"a cache where it only needs to see the user once and it saves it\".
    A user firing 100 votes should hit the HTTP path exactly once."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_remote_user(FakeUser(id="100", name="alice", display_name="Alice"))
    raw = FakeRawPollVote(
        message_id=1, channel_id=2, user_id=100, guild_id=3, answer_id=1
    )
    try:
        # First vote: cache miss → get_user None → fetch_user HTTP.
        await fake.fire("on_raw_poll_vote_add", raw)
        assert fake.fetch_user_call_count == 1
        assert handle.published[0].payload.data["user_display_name"] == "Alice"

        # Second + third votes from same user: should hit local cache.
        # No new HTTP round-trip, display name still resolves.
        await fake.fire("on_raw_poll_vote_remove", raw)
        await fake.fire("on_raw_poll_vote_add", raw)
        assert fake.fetch_user_call_count == 1, (
            "fetch_user must not be called for users already in local cache"
        )
        assert handle.published[1].payload.data["user_display_name"] == "Alice"
        assert handle.published[2].payload.data["user_display_name"] == "Alice"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_returns_empty_when_fetch_user_raises(
    monkeypatch,
):
    """If neither ``get_user`` nor ``fetch_user`` resolves (deleted account,
    network error, etc.), the Event still publishes — just with an empty
    ``user_display_name``. Failures are NOT cached, so a transient HTTP
    error doesn't lock the user at empty forever."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    # Deliberately seed nowhere — both get_user AND fetch_user will miss.
    raw = FakeRawPollVote(
        message_id=1, channel_id=2, user_id=999, guild_id=3, answer_id=1
    )
    try:
        await fake.fire("on_raw_poll_vote_add", raw)
        env = handle.published[0]
        assert env.payload.data["user_id"] == "999"
        assert env.payload.data["user_display_name"] == ""
        assert fake.fetch_user_call_count == 1, "fetch_user attempted on get_user miss"

        # Recovery: a later fix (user re-seeded) should resolve cleanly,
        # NOT be locked at empty by the failure cache.
        fake.add_remote_user(FakeUser(id="999", name="late", display_name="LateBoot"))
        await fake.fire("on_raw_poll_vote_add", raw)
        assert handle.published[1].payload.data["user_display_name"] == "LateBoot"
        assert fake.fetch_user_call_count == 2
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_dm_context(monkeypatch):
    """``guild_id`` is ``None`` on real discord.py raw events for DMs;
    the envelope normalizes that to ``""`` for consistency with the
    rest of the adapter's surface."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    raw = FakeRawPollVote(
        message_id=1, channel_id=2, user_id=3, guild_id=None, answer_id=1
    )
    try:
        await fake.fire("on_raw_poll_vote_add", raw)
        env = handle.published[0]
        assert env.payload.data["guild_id"] == ""
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_remove_publishes_event_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_user(FakeUser(id="30", name="bob", display_name="Bob"))
    raw = FakeRawPollVote(
        message_id=10, channel_id=20, user_id=30, guild_id=40, answer_id=2
    )
    try:
        await fake.fire("on_raw_poll_vote_remove", raw)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.payload.type == "discord.poll_vote_remove"
        assert env.payload.data["message_id"] == "10"
        assert env.payload.data["answer_id"] == 2
        # Display name resolution applies symmetrically to the remove path.
        assert env.payload.data["user_display_name"] == "Bob"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_message_edit_publishes_event_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    raw = FakeRawMessageUpdate(message_id=100, channel_id=200, guild_id=300)
    try:
        await fake.fire("on_raw_message_edit", raw)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.kind == "Event"
        assert env.payload.type == "discord.message_edit"
        assert env.payload.data["message_id"] == "100"
        assert env.payload.data["channel_id"] == "200"
        assert env.payload.data["guild_id"] == "300"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_message_delete_publishes_event_envelope(monkeypatch):
    ep, handle, fake = await _start_endpoint(monkeypatch)
    raw = FakeRawMessageDelete(message_id=100, channel_id=200, guild_id=None)
    try:
        await fake.fire("on_raw_message_delete", raw)
        env = handle.published[0]
        assert env.payload.type == "discord.message_delete"
        assert env.payload.data["message_id"] == "100"
        assert env.payload.data["guild_id"] == ""
    finally:
        await ep.stop()
