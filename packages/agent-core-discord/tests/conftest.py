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

    async def edit(self, *, content: str | None = None, embeds: list | None = None) -> None:
        self.edits.append({"content": content, "embeds": embeds})


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
        self._handlers: dict[str, Callable] = {}
        self._on_ready_event = asyncio.Event()

    def event(self, fn: Callable) -> Callable:
        """Decorator @client.event — registers the handler by function name."""
        self._handlers[fn.__name__] = fn
        return fn

    def get_channel(self, channel_id: int | str) -> _FakeChannel | None:
        return self._channels.get(str(channel_id))

    async def fetch_channel(self, channel_id: int | str) -> _FakeChannel | None:
        return self._channels.get(str(channel_id))

    @property
    def guilds(self):
        return list(self._guilds.values())

    async def start(self, token: str) -> None:
        # Set on_ready immediately for tests; tests can call client._fire('on_ready')
        # explicitly if they need to coordinate timing.
        self._on_ready_event.set()
        if "on_ready" in self._handlers:
            await self._handlers["on_ready"]()

    async def close(self) -> None:
        self._closed = True

    async def fire(self, event_name: str, *args) -> None:
        """Test helper: invoke a registered handler."""
        h = self._handlers.get(event_name)
        if h is not None:
            await h(*args)

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
