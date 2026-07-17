"""Tests for DiscordEndpoint inbound handlers (on_message, on_reaction_add)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import FakeChannel, FakeDiscordClient, FakeMessage, FakeUser

from agent_core.bus.envelope import EndpointInfo, Envelope, EventPayload, TextMessagePayload
from agent_core.testing import wait_until


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []

    def spawn(self, coro, *, name=None):
        import asyncio

        return asyncio.create_task(coro, name=name)


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
async def test_on_message_drops_messages_from_bots_not_in_allowlist(monkeypatch):
    """A bot author with empty allowedBotIds is gate-denied — same outcome
    as the historical "all bots blocked" default. The drop now happens at
    the gate layer (not the on_message pre-filter), so allowed_bot_ids
    can still grant specific other-bots through.
    """
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
async def test_on_message_allows_bots_in_allowed_bot_ids(monkeypatch, tmp_path):
    """foreman#143 regression guard: a bot author whose id IS in the
    access config's ``allowedBotIds`` must reach the bus inbox.

    The Pepper-Wren cross-bot rollout surfaced this 2026-06-07: PR #158
    added the ``allowed_bot_ids`` field to ``AccessConfig`` + the gate
    logic, but the on_message handler still pre-filtered ALL bots
    (``message.author.bot`` → return) before the gate could see them,
    AND constructed ``InboundContext`` with ``is_bot=False`` hardcoded —
    so even if the pre-filter were dropped, the gate would never route
    through the bot-allowlist branch. Both layers had to know about the
    rule for ``allowedBotIds`` to actually work.
    """
    import json

    pepper_bot_id = "1480938766246871050"
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps({"dmPolicy": "open", "allowedBotIds": [pepper_bot_id]}),
        encoding="utf-8",
    )
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(
        id="m-bot",
        channel_id="200",
        content="cross-bot smoke test",
        author_id=pepper_bot_id,
        is_bot=True,
    )
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1, (
            "allowedBotIds entry should let this bot's message through to the bus"
        )
        env = handle.published[0]
        assert env.payload.text == "cross-bot smoke test"
        assert env.metadata["discord"]["author_id"] == pepper_bot_id
        # is_bot must be piped through so downstream consumers can see who's a bot.
        assert env.metadata["discord"]["is_bot"] is True
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
        await wait_until(lambda: ch._typing_count >= 1, message="typing indicator started")
        assert ch._typing_count >= 1
        await ep._clear_pending_ack(ch, "m-typing")
        await wait_until(lambda: ch._typing_count == 0, message="typing indicator cleared")
        assert ch._typing_count == 0
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_attachments_metadata(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeAttachment

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    async def fake_download(url):
        return b"PDFDATA", "application/pdf"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    att = FakeAttachment(
        filename="file.pdf",
        url="https://example.com/file.pdf",
        content_type="application/pdf",
        size=1024,
    )
    msg = _msg(id="m-att", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        a0 = env.metadata["attachments"][0]
        assert a0["filename"] == "file.pdf"
        assert a0["url"] == "https://example.com/file.pdf"
        assert a0["content_type"] == "application/pdf"
        assert a0["size_bytes"] == 1024
        assert a0["local_path"] is not None
        assert Path(a0["local_path"]).read_bytes() == b"PDFDATA"
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


@pytest.mark.asyncio
async def test_on_message_records_inbound_in_recent_inbounds_cache(monkeypatch):
    """After on_message publishes, the envelope is recorded for auto-echo."""
    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    msg = _msg(id="m-cache", content="hi")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        # Cache contains the envelope keyed by its id.
        cached = ep._recent_inbounds.get(env.id)
        assert cached is not None
        assert (cached.metadata or {}).get("discord", {}).get("channel_id") == "200"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_to_outbound_routes_correctly_via_in_reply_to_only(monkeypatch):
    """
    Issue #83 named-symptom regression lock:
    1. Inbound arrives in channel A.
    2. Wake preview surfaces channel_id (A).
    3. Agent sends TextMessage with in_reply_to=<inbound>, no explicit channel_id.
    4. Outbound posts to channel A via auto-echo lookup.

    Note: this exercises the pre-existing _inbound_envelope_discord path in
    _deliver_text_message, not the new _resolve_channel_id. TextMessage handler
    unification with the new resolver is deferred (see spec doc "Followups").
    The named symptom is locked regardless of which cache resolved the routing.
    """
    ep, handle, fake = await _start_endpoint(monkeypatch)
    ch_a = FakeChannel(id="200", name="pepper-upgrade", guild_id="g1")
    fake.add_channel(ch_a)
    inbound_msg = _msg(id="m-in", channel_id="200", content="hi from #pepper-upgrade")
    inbound_msg.channel = fake.get_channel("200")
    ch_a._messages["m-in"] = inbound_msg
    try:
        # 1. Inbound arrives.
        await fake.fire("on_message", inbound_msg)
        env = handle.published[0]
        assert (env.metadata or {}).get("discord", {}).get("channel_id") == "200"
        # 2. (A) Preview-side surfacing covered by Phase 1 tests.
        # 3+4. Agent send with in_reply_to only.
        from datetime import UTC, datetime

        from agent_core.bus.envelope import Envelope, TextMessagePayload
        out = Envelope(
            id="agent-reply", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage", payload=TextMessagePayload(text="reply"),
            in_reply_to=env.id, metadata={},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(out)
        # 4. Verify outbound posted to channel 200.
        assert len(ch_a.sent) == 1
        assert ch_a.sent[0]["content"] == "reply"
        # No error ack.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert all(not a.payload.note.lower().startswith("error:") for a in acks)
    finally:
        await ep.stop()


# --- _persist_attachment ---


@pytest.mark.asyncio
async def test_persist_attachment_writes_file_and_returns_path(monkeypatch, tmp_path):
    from agent_core_discord.endpoint import DiscordEndpoint

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
    )

    async def fake_download(url):
        return b"PNGDATA", "image/png"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    path, nbytes = await ep._persist_attachment(
        url="https://cdn.discordapp.com/a/pic.png", subdir="env-abc"
    )
    assert isinstance(path, Path)
    assert path.read_bytes() == b"PNGDATA"
    assert nbytes == len(b"PNGDATA")
    assert path.parent == (tmp_path / "env-abc").resolve()
    assert path.name == "pic.png"


@pytest.mark.asyncio
async def test_persist_attachment_dedups_collision(monkeypatch, tmp_path):
    from agent_core_discord.endpoint import DiscordEndpoint

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
    )

    async def fake_download(url):
        return b"DATA", "application/octet-stream"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    p1, _ = await ep._persist_attachment(
        url="https://cdn.discordapp.com/a/f.bin", subdir="env-1"
    )
    p2, _ = await ep._persist_attachment(
        url="https://cdn.discordapp.com/b/f.bin", subdir="env-1"
    )
    assert p1 != p2
    assert p1.read_bytes() == b"DATA"
    assert p2.read_bytes() == b"DATA"


@pytest.mark.asyncio
async def test_persist_attachment_raises_on_download_failure(monkeypatch, tmp_path):
    from agent_core_discord.endpoint import DiscordEndpoint

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
    )

    async def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(ep, "_download_url", boom)

    with pytest.raises(RuntimeError):
        await ep._persist_attachment(
            url="https://cdn.discordapp.com/a/x.png", subdir="env-2"
        )


@pytest.mark.asyncio
async def test_inbound_autodownloads_and_enriches_metadata(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeAttachment, FakeChannel

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    async def fake_download(url):
        return b"IMGBYTES", "image/png"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    att = FakeAttachment(
        filename="pic.png",
        url="https://cdn.discordapp.com/a/pic.png?ex=deadbeef",
        content_type="image/png",
        size=8,
    )
    msg = _msg(id="m-att", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        a0 = env.metadata["attachments"][0]
        assert a0["filename"] == "pic.png"
        assert a0["url"] == "https://cdn.discordapp.com/a/pic.png?ex=deadbeef"
        assert a0["content_type"] == "image/png"
        assert a0["size_bytes"] == 8
        assert a0["local_path"] is not None
        assert "download_error" not in a0
        assert Path(a0["local_path"]).read_bytes() == b"IMGBYTES"
        assert env.id in a0["local_path"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_download_failure_degrades_url_only(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeAttachment, FakeChannel

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    async def boom(url):
        raise RuntimeError("timeout")

    monkeypatch.setattr(ep, "_download_url", boom)

    att = FakeAttachment(
        filename="clip.mov",
        url="https://cdn.discordapp.com/a/clip.mov",
        content_type="video/quicktime",
        size=999,
    )
    msg = _msg(id="m-fail", content="did you see it?", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        assert env.payload.text == "did you see it?"
        a0 = env.metadata["attachments"][0]
        assert a0["local_path"] is None
        assert "timeout" in a0["download_error"]
        assert a0["url"] == "https://cdn.discordapp.com/a/clip.mov"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_multi_attachment_distinct_paths(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeAttachment, FakeChannel

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    payloads = {
        "https://cdn.discordapp.com/a/one.png": b"ONE",
        "https://cdn.discordapp.com/a/two.png": b"TWO",
        "https://cdn.discordapp.com/a/three.png": b"THREE",
    }

    async def fake_download(url):
        return payloads[url], "image/png"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    atts = [
        FakeAttachment(filename=f"{n}.png", url=u, content_type="image/png", size=len(b))
        for (u, b), n in zip(payloads.items(), ["one", "two", "three"], strict=False)
    ]
    msg = _msg(id="m-multi", attachments=atts)
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        paths = [a["local_path"] for a in env.metadata["attachments"]]
        assert len(paths) == 3
        assert len(set(paths)) == 3
        assert Path(paths[0]).read_bytes() == b"ONE"
        assert Path(paths[2]).read_bytes() == b"THREE"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_download_error_redacts_signed_cdn_url(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeAttachment, FakeChannel

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    signed = "https://cdn.discordapp.com/a/x.png?ex=6641abcd&is=6640&hm=secretsig"

    async def boom(url):
        # Mirror httpx.raise_for_status() shape: signed URL inside the message.
        raise RuntimeError(f"Client error '403 Forbidden' for url '{signed}'")

    monkeypatch.setattr(ep, "_download_url", boom)

    att = FakeAttachment(filename="x.png", url=signed, content_type="image/png", size=1)
    msg = _msg(id="m-redact", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        a0 = handle.published[0].metadata["attachments"][0]
        err = a0["download_error"]
        assert "hm=secretsig" not in err
        assert "ex=6641abcd" not in err
        assert "<redacted>" in err
        assert "403 Forbidden" in err  # non-secret context preserved
        # The signed url is still preserved verbatim in the url field (by design).
        assert a0["url"] == signed
    finally:
        await ep.stop()


# --- Channel-allowlist gate for meta-event handlers (issue #208) ---
# Regression guard: reaction/edit/delete/poll-vote events from non-allowlisted
# channels must be dropped (not published). Observed leaking from #pepper-chat
# on 2026-06-22/23 despite message content being correctly blocked.


@pytest.mark.asyncio
async def test_on_reaction_add_drops_non_allowlisted_channel(monkeypatch, tmp_path):
    """Reaction from a guild channel NOT in the channels allowlist is dropped."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(FakeChannel(id="999"))  # non-allowlisted
    msg = FakeMessage(id="m", channel_id="999", content="")
    msg.author = FakeUser(id="100")
    msg.guild = type("G", (), {"id": "g"})()
    msg.channel = fake.get_channel("999")
    user = FakeUser(id="100")
    reaction = FakeReaction(emoji="👍", message=msg)
    try:
        await fake.fire("on_reaction_add", reaction, user)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_reaction_add_passes_allowlisted_channel(monkeypatch, tmp_path):
    """Reaction from an allowlisted channel still publishes."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_channel(FakeChannel(id="200"))
    msg = FakeMessage(id="m", channel_id="200", content="")
    msg.author = FakeUser(id="100")
    msg.guild = type("G", (), {"id": "g"})()
    msg.channel = fake.get_channel("200")
    user = FakeUser(id="100")
    reaction = FakeReaction(emoji="👍", message=msg)
    try:
        await fake.fire("on_reaction_add", reaction, user)
        assert len(handle.published) == 1
        assert handle.published[0].payload.type == "discord.reaction_add"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_message_edit_drops_non_allowlisted_channel(monkeypatch, tmp_path):
    """message_edit from a non-allowlisted channel is dropped."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    raw = FakeRawMessageUpdate(message_id=1, channel_id=999, guild_id=1000)
    try:
        await fake.fire("on_raw_message_edit", raw)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_message_edit_passes_allowlisted_channel(monkeypatch, tmp_path):
    """message_edit from an allowlisted channel still publishes."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    raw = FakeRawMessageUpdate(message_id=1, channel_id=200, guild_id=1000)
    try:
        await fake.fire("on_raw_message_edit", raw)
        assert len(handle.published) == 1
        assert handle.published[0].payload.type == "discord.message_edit"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_message_delete_drops_non_allowlisted_channel(monkeypatch, tmp_path):
    """message_delete from a non-allowlisted channel is dropped."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    raw = FakeRawMessageDelete(message_id=1, channel_id=999, guild_id=1000)
    try:
        await fake.fire("on_raw_message_delete", raw)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_message_delete_passes_allowlisted_channel(monkeypatch, tmp_path):
    """message_delete from an allowlisted channel still publishes."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    raw = FakeRawMessageDelete(message_id=1, channel_id=200, guild_id=1000)
    try:
        await fake.fire("on_raw_message_delete", raw)
        assert len(handle.published) == 1
        assert handle.published[0].payload.type == "discord.message_delete"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_drops_non_allowlisted_channel(monkeypatch, tmp_path):
    """poll_vote_add from a non-allowlisted channel is dropped."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    raw = FakeRawPollVote(
        message_id=1, channel_id=999, user_id=100, guild_id=1000, answer_id=1
    )
    try:
        await fake.fire("on_raw_poll_vote_add", raw)
        assert handle.published == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_raw_poll_vote_add_passes_allowlisted_channel(monkeypatch, tmp_path):
    """poll_vote_add from an allowlisted channel still publishes."""
    import json
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
    ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
    fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))
    raw = FakeRawPollVote(
        message_id=1, channel_id=200, user_id=100, guild_id=1000, answer_id=1
    )
    try:
        await fake.fire("on_raw_poll_vote_add", raw)
        assert len(handle.published) == 1
        assert handle.published[0].payload.type == "discord.poll_vote_add"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_on_message_sanitizes_lone_surrogate(monkeypatch):
    """Inbound Discord message content containing a lone UTF-16 surrogate must
    be delivered as a sanitized envelope whose payload.text is valid JSON and
    valid UTF-8 — no lone surrogate code units must propagate into the bus.

    Regression guard for issue #211: Pepper's session was bricked when a
    flag emoji relayed from Discord introduced a lone high surrogate into the
    conversation transcript, causing every subsequent API request (and
    /compact) to be rejected with a 400 JSON-parse error.
    """
    import json as _json

    ep, handle, fake = await _start_endpoint(monkeypatch)
    fake.add_channel(FakeChannel(id="200"))
    # Build a lone high surrogate programmatically (do not paste raw emoji).
    lone_surrogate = "\ud800"
    msg = _msg(id="m-surr", channel_id="200", content=f"hello {lone_surrogate} world")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        text = handle.published[0].payload.text
        # Must not contain any lone surrogate code unit.
        assert "\ud800" not in text
        # Must be JSON-serialisable without error.
        _json.dumps(text)
        # Must encode to UTF-8 without error.
        text.encode("utf-8")
    finally:
        await ep.stop()
