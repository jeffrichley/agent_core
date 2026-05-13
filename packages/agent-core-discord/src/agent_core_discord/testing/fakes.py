"""Fake stand-ins for ``discord.py`` types used in tests.

The fakes mirror enough of ``discord.Client`` and its supporting types to
exercise lifecycle, inbound event dispatch, and outbound tool calls without
a network. They mirror real ``discord.py`` shapes strictly so production
code reading the wrong attribute fails against the fake the same way it
would fail against the real library.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


class FakePollAnswer:
    """Lightweight stand-in for ``discord.PollAnswer``.

    Real ``discord.Poll`` exposes answers as objects with ``id``, ``text``,
    ``emoji``, and (when results are loaded) ``vote_count``. The fake mirrors
    that shape so production code that reads ``answer.text`` etc. works
    without the real discord.py types.
    """

    def __init__(
        self,
        *,
        id: int,
        text: str,
        emoji: str | None = None,
        vote_count: int = 0,
    ) -> None:
        self.id = id
        self.text = text
        self.emoji = emoji
        self.vote_count = vote_count


class FakePoll:
    """Lightweight stand-in for ``discord.Poll``.

    Real ``discord.Poll`` exposes ``question`` as a flat ``str`` (it's a
    ``@property`` that returns ``self._question_media.text`` — see
    discord.py's ``poll.py``), ``answers`` (list of PollAnswer),
    ``multiselect`` (bool), ``duration`` (timedelta), ``expires_at``
    (datetime), and ``is_finalised()`` / ``total_votes``. The fake exposes
    enough to verify the fetch-side serializer reads the right attributes.
    """

    def __init__(
        self,
        *,
        question_text: str,
        answers: list[FakePollAnswer] | None = None,
        multiselect: bool = False,
        duration_seconds: int | None = None,
        expires_at: Any = None,
        is_finalised: bool = False,
        total_votes: int = 0,
    ) -> None:
        # Real ``discord.Poll.question`` is a flat ``str``, NOT a nested
        # object with ``.text``. Mirroring real strictly: a fake that
        # exposed ``question.text`` would let production code reading
        # the wrong shape green-test against fake data while hitting
        # ``""`` on real polls (the exact failure mode caught on testbot
        # 2026-05-05 Phase 6 verification).
        self.question = question_text
        # Internal mirror for any test that wants to model discord.py's
        # underlying ``_question_media`` shape; not used by the
        # serializer, which goes through the public ``.question`` only.
        self._question_media = SimpleNamespace(text=question_text)
        self.answers = list(answers or [])
        self.multiselect = multiselect
        # ``duration`` on real Poll is a ``datetime.timedelta``; tests can
        # pass an int seconds for convenience (the serializer converts).
        if duration_seconds is None:
            self.duration = None
        else:
            from datetime import timedelta as _td

            self.duration = _td(seconds=duration_seconds)
        self.expires_at = expires_at
        self._is_finalised = is_finalised
        self.total_votes = total_votes

    def is_finalised(self) -> bool:
        return self._is_finalised


class FakeAttachment:
    """Minimal stand-in for discord.Attachment on a fake message.

    Models only the fields tests assert on (`filename`, `url`); expand
    named per the test-fakes-mirror-real-strictly discipline.
    """

    def __init__(self, *, filename: str, url: str = ""):
        self.filename = filename
        self.url = url


class FakeMessage:
    def __init__(
        self,
        *,
        id: str,
        channel_id: str,
        content: str = "",
        author=None,
        poll: FakePoll | None = None,
        attachments: list[FakeAttachment] | None = None,
    ):
        self.id = id
        self.channel_id = channel_id
        self.content = content
        self.author = author
        self.poll = poll
        self.attachments = attachments or []
        self.reactions: list[str] = []
        self.edits: list[dict[str, Any]] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)

    async def remove_reaction(self, emoji: str, user: Any) -> None:
        if emoji in self.reactions:
            self.reactions.remove(emoji)

    async def edit(self, **kwargs: Any) -> None:
        # Mirror discord.py's stricter contract. Passing embeds=None raises
        # `object of type 'NoneType' has no len()` in real Message.edit;
        # the right call-site idiom is to omit the kwarg entirely. Catch this
        # at the fake so unit tests fail when production passes None.
        if "embeds" in kwargs and kwargs["embeds"] is None:
            raise TypeError("object of type 'NoneType' has no len()")
        self.edits.append(kwargs)


class FakeChannel:
    def __init__(
        self,
        *,
        id: str,
        name: str = "",
        channel_type: str = "text",
        guild_id: str | None = None,
    ):
        self.id = id
        self.name = name
        self.type = channel_type
        # Real discord.py text channels do NOT expose a flat ``.guild_id``
        # attribute — they expose ``.guild`` (a Guild object) with ``.id``.
        # Mirror that shape so production code reading ``ch.guild.id`` works
        # the same way real discord.py does. Tests may still pass
        # ``guild_id="..."`` to FakeChannel for ergonomic setup; we wire
        # it into ``self.guild.id`` here. ``self._guild_id`` is a private
        # backref kept for thread-creation propagation.
        self._guild_id = guild_id
        self.guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
        self.topic = ""
        self.nsfw = False
        self.sent: list[dict[str, Any]] = []
        self._messages: dict[str, FakeMessage] = {}
        self._typing_count = 0
        self.threads_created: list[dict[str, Any]] = []
        self._fake_client: FakeDiscordClient | None = None

    def typing(self):
        ch = self

        class _T:
            async def __aenter__(self):
                ch._typing_count += 1
                return None

            async def __aexit__(self, *exc):
                ch._typing_count -= 1
                return None

        return _T()

    async def send(
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: Any = None,
        files: list | None = None,
        poll: Any = None,
    ) -> FakeMessage:
        new_id = f"new-{len(self.sent) + 1}"
        attachments: list[FakeAttachment] = []
        for f in files or []:
            # discord.File.filename is the resolved upload filename
            # (basename of the path when not overridden).
            fn = getattr(f, "filename", "")
            attachments.append(FakeAttachment(filename=fn))
        msg = FakeMessage(
            id=new_id,
            channel_id=self.id,
            content=content or "",
            attachments=attachments,
        )
        self._messages[new_id] = msg
        self.sent.append(
            {
                "content": content,
                "embeds": embeds,
                "reference": reference,
                "files": files,
                "poll": poll,
                "message_id": new_id,
            }
        )
        return msg

    async def create_thread(
        self,
        *,
        name: str,
        message: Any = None,
        auto_archive_duration: Any = None,
        type: Any = None,
        reason: str | None = None,
        invitable: bool = True,
        slowmode_delay: int | None = None,
    ) -> FakeChannel:
        tid = f"th-{uuid.uuid4().hex[:10]}"
        th = FakeChannel(
            id=tid,
            name=name,
            channel_type="public_thread",
            guild_id=self._guild_id,
        )
        th._fake_client = self._fake_client
        if self._fake_client is not None:
            self._fake_client.add_channel(th)
        mid = str(getattr(message, "id", "") or "") or None
        self.threads_created.append({"name": name, "message_id": mid})
        return th

    async def fetch_message(self, message_id: str) -> FakeMessage | None:
        return self._messages.get(message_id)

    def history(self, limit: int = 50, before: Any = None):
        async def _gen():
            for m in list(self._messages.values())[:limit]:
                yield m

        return _gen()


class FakeGuild:
    def __init__(self, *, id: str, channels: list[FakeChannel]):
        self.id = id
        self.channels = channels
        self._scheduled: dict[str, FakeScheduledEvent] = {}
        self._sched_seq = 0

    async def create_scheduled_event(
        self,
        *,
        name: str,
        start_time: Any,
        entity_type: Any = None,
        privacy_level: Any = None,
        channel: Any = None,
        location: str = "",
        end_time: Any = None,
        description: str = "",
        image: bytes = b"",
        reason: str | None = None,
        **_: Any,
    ) -> FakeScheduledEvent:
        self._sched_seq += 1
        sid = f"se-{self._sched_seq}"
        et_name = getattr(entity_type, "name", str(entity_type))
        ev = FakeScheduledEvent(
            id=sid,
            name=name,
            guild=self,
            start_time=start_time,
            end_time=end_time,
            entity_type=et_name,
        )
        self._scheduled[sid] = ev
        return ev

    def get_scheduled_event(self, event_id: int | str) -> FakeScheduledEvent | None:
        key = str(event_id)
        return self._scheduled.get(key)

    async def fetch_scheduled_event(self, event_id: int | str) -> FakeScheduledEvent:
        ev = self.get_scheduled_event(event_id)
        if ev is None:
            raise LookupError(f"scheduled event {event_id!r} not found")
        return ev

    async def fetch_scheduled_events(self) -> list[FakeScheduledEvent]:
        return list(self._scheduled.values())


class FakeScheduledEvent:
    def __init__(
        self,
        *,
        id: str,
        name: str,
        guild: FakeGuild,
        start_time: Any = None,
        end_time: Any = None,
        entity_type: str = "external",
    ) -> None:
        self.id = id
        self.name = name
        self.guild = guild
        self.start_time = start_time
        self.end_time = end_time
        self.entity_type = entity_type
        self.status = "scheduled"
        self._cancelled = False

    async def cancel(self, *, reason: str | None = None) -> FakeScheduledEvent:
        self._cancelled = True
        self.status = "cancelled"
        self.guild._scheduled.pop(str(self.id), None)
        return self


class FakeUser:
    def __init__(
        self,
        *,
        id: str,
        name: str = "tester",
        bot: bool = False,
        display_name: str | None = None,
    ):
        self.id = id
        self.name = name
        self.bot = bot
        self.display_name = display_name or name


class FakeDiscordClient:
    """Lightweight stand-in for discord.Client.

    Tests construct a client, register channels/guilds, then drive event
    dispatch by calling the on_message / on_reaction_add hooks the endpoint
    registers via @client.event."""

    def __init__(self, *, intents: Any = None):
        self.user = FakeUser(id="bot-1", name="testbot", bot=True)
        self._channels: dict[str, FakeChannel] = {}
        self._guilds: dict[str, FakeGuild] = {}
        # Real ``discord.Client.get_user(user_id)`` returns the cached
        # ``User`` or ``None``. Tests pre-seed via ``add_user`` so handlers
        # that resolve display names from raw events have something to
        # find.
        self._users: dict[str, FakeUser] = {}
        # Real ``discord.Client.fetch_user(user_id)`` is the HTTP-backed
        # fallback. Crucially, fetch_user does NOT auto-cache into
        # ``_users`` (it just constructs a new ``User`` from the
        # response — see ``client.py:2679``); the bus-level adapter has
        # to maintain its own sticky cache. Tests pre-seed
        # via ``add_remote_user`` for the HTTP-only path.
        self._remote_users: dict[str, FakeUser] = {}
        # Counter so tests can assert cache hits avoided HTTP.
        self.fetch_user_call_count = 0
        self._closed = False
        self._logged_in = False
        self._handlers: dict[str, Callable] = {}
        self._on_ready_event = asyncio.Event()
        self._closed_event: asyncio.Event | None = None
        self._on_ready_task: asyncio.Task | None = None

    def event(self, fn: Callable) -> Callable:
        """Decorator @client.event — registers the handler by function name."""
        self._handlers[fn.__name__] = fn
        return fn

    def add_listener(self, fn: Callable, name: str | None = None) -> None:
        """Mirrors discord.Client.add_listener — register by explicit event name."""
        self._handlers[name or fn.__name__] = fn

    def get_channel(self, channel_id: int | str) -> FakeChannel | None:
        return self._channels.get(str(channel_id))

    def get_user(self, user_id: int | str) -> FakeUser | None:
        """Mirrors ``discord.Client.get_user`` — cache lookup, ``None`` if absent."""
        return self._users.get(str(user_id))

    def add_user(self, user: FakeUser) -> None:
        self._users[str(user.id)] = user

    async def fetch_user(self, user_id: int | str) -> FakeUser:
        """Mirrors ``discord.Client.fetch_user`` — async HTTP-style lookup.

        Raises a ``LookupError`` for missing users, mirroring real
        ``discord.NotFound`` (the adapter catches broad ``Exception`` so
        the specific class doesn't matter for the test surface).

        Crucially, this does NOT auto-cache — same as real discord.py.
        Adapters that want stickiness must maintain their own cache.
        """
        self.fetch_user_call_count += 1
        u = self._remote_users.get(str(user_id))
        if u is None:
            raise LookupError(f"user {user_id!r} not found")
        return u

    def add_remote_user(self, user: FakeUser) -> None:
        """Pre-seed a user reachable only via ``fetch_user``.

        Use this for tests that exercise the HTTP-fallback path. Users
        seeded here are NOT visible to ``get_user`` (mirrors real
        discord.py: HTTP-fetched users aren't auto-added to the cache).
        """
        self._remote_users[str(user.id)] = user

    def get_guild(self, guild_id: int | str) -> FakeGuild | None:
        return self._guilds.get(str(guild_id))

    async def fetch_channel(self, channel_id: int | str) -> FakeChannel | None:
        return self._channels.get(str(channel_id))

    @property
    def guilds(self):
        return list(self._guilds.values())

    async def login(self, token: str) -> None:
        """Mirrors discord.Client.login — returns once authenticated."""
        self._logged_in = True

    async def connect(self) -> None:
        """Mirrors discord.Client.connect — runs until close() is called.

        Real discord.py dispatches on_ready off the gateway loop, so connect()
        does not block on the handler. We mirror that by firing on_ready in a
        task, then blocking on the close event."""
        if "on_ready" in self._handlers:
            # Hold a reference so test failures inside on_ready don't disappear
            # into "Task exception was never retrieved" warnings.
            self._on_ready_task = asyncio.create_task(self._handlers["on_ready"]())
        if self._closed_event is None:
            self._closed_event = asyncio.Event()
        await self._closed_event.wait()

    async def close(self) -> None:
        self._closed = True
        self._on_ready_event.set()
        if self._closed_event is not None:
            self._closed_event.set()

    async def fire(self, event_name: str, *args) -> None:
        """Test helper: invoke a registered handler."""
        h = self._handlers.get(event_name)
        if h is not None:
            await h(*args)

    async def fire_ready(self) -> None:
        """Test helper: fire on_ready immediately (await the handler)."""
        if "on_ready" in self._handlers:
            await self._handlers["on_ready"]()

    def add_channel(self, ch: FakeChannel) -> None:
        ch._fake_client = self
        self._channels[ch.id] = ch

    def add_guild(self, g: FakeGuild) -> None:
        self._guilds[g.id] = g
        for ch in g.channels:
            ch._fake_client = self
            self._channels[ch.id] = ch


class FakeBusHandle:
    """Minimal BusHandle stub for endpoint lifecycle tests."""

    async def publish(self, *a, **kw): ...
    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
    def endpoints(self):
        return []
