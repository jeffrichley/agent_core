"""Shared test fixtures + a fake Discord client for the unit tests.

The fake mimics enough of discord.Client to exercise lifecycle, inbound
event dispatch, and outbound tool calls without a network."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class _FakeMessage:
    def __init__(self, *, id: str, channel_id: str, content: str = "", author=None):
        self.id = id
        self.channel_id = channel_id
        self.content = content
        self.author = author
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


class _FakeChannel:
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
        self.guild_id = guild_id
        self.topic = ""
        self.nsfw = False
        self.sent: list[dict[str, Any]] = []
        self._messages: dict[str, _FakeMessage] = {}
        self._typing_count = 0

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
    ) -> _FakeMessage:
        new_id = f"new-{len(self.sent) + 1}"
        msg = _FakeMessage(id=new_id, channel_id=self.id, content=content or "")
        self._messages[new_id] = msg
        self.sent.append(
            {
                "content": content,
                "embeds": embeds,
                "reference": reference,
                "files": files,
                "message_id": new_id,
            }
        )
        return msg

    async def fetch_message(self, message_id: str) -> _FakeMessage | None:
        return self._messages.get(message_id)

    def history(self, limit: int = 50, before: Any = None):
        async def _gen():
            for m in list(self._messages.values())[:limit]:
                yield m

        return _gen()


class _FakeGuild:
    def __init__(self, *, id: str, channels: list[_FakeChannel]):
        self.id = id
        self.channels = channels


class _FakeUser:
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


class _FakeDiscordClient:
    """Lightweight stand-in for discord.Client.

    Tests construct a client, register channels/guilds, then drive event
    dispatch by calling the on_message / on_reaction_add hooks the endpoint
    registers via @client.event."""

    def __init__(self, *, intents: Any = None):
        self.user = _FakeUser(id="bot-1", name="testbot", bot=True)
        self._channels: dict[str, _FakeChannel] = {}
        self._guilds: dict[str, _FakeGuild] = {}
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

    def get_channel(self, channel_id: int | str) -> _FakeChannel | None:
        return self._channels.get(str(channel_id))

    async def fetch_channel(self, channel_id: int | str) -> _FakeChannel | None:
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

    def add_channel(self, ch: _FakeChannel) -> None:
        self._channels[ch.id] = ch

    def add_guild(self, g: _FakeGuild) -> None:
        self._guilds[g.id] = g
        for ch in g.channels:
            self._channels[ch.id] = ch


class _FakeBusHandle:
    """Minimal BusHandle stub for endpoint lifecycle tests."""

    async def publish(self, *a, **kw): ...
    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
    def endpoints(self):
        return []
