"""DiscordEndpoint applies sigil-prefix urgency rule on inbound TextMessage envelopes.

Sigil rules: "!" -> red, "?" -> yellow, anything else -> green. See
docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md.
"""

from __future__ import annotations

import pytest
from agent_core_discord.endpoint import DiscordEndpoint

from agent_core.bus.envelope import EndpointInfo, Envelope
from tests.conftest import _FakeChannel, _FakeDiscordClient, _FakeMessage, _FakeUser


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


async def _start(
    monkeypatch, access_path=None
) -> tuple[DiscordEndpoint, _Recording, _FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="d",
        target="agent",
        token_env="X_TOK",
        access_config_path=access_path,
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _msg(content: str) -> _FakeMessage:
    msg = _FakeMessage(id="m1", channel_id="200", content=content)
    msg.author = _FakeUser(id="100", name="user", display_name="User")
    msg.guild = type("G", (), {"id": "guild-1"})()
    msg.channel = _FakeChannel(id="200")
    msg.attachments = []
    return msg


@pytest.mark.asyncio
async def test_inbound_default_urgency_is_green(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("hello world")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
        assert handle.published[0].payload.text == "hello world"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_red_sigil_promotes_to_red_and_strips_prefix(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("!server is on fire")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
        # The sigil and the optional space after it are stripped from the payload.
        assert handle.published[0].payload.text == "server is on fire"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_yellow_sigil_promotes_to_yellow_and_strips_prefix(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("?status check")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "yellow"
        assert handle.published[0].payload.text == "status check"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_lancaster_regression_now_in_natural_text_is_not_red(monkeypatch):
    """Issue #38 regression: 'right now we are looking at...' must not fire red.

    The original regex (urgent|now|stop) matched on the substring 'now'. The
    sigil-only rule has no such failure mode — 'right now' is plain text,
    no leading sigil, urgency stays green.
    """
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("ok, more on the ideas for the lancaster trip. right now we are looking at...")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
        # Payload text is unchanged for green messages — no sigil to strip.
        assert handle.published[0].payload.text.startswith("ok, more on the ideas")
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_urgent_keyword_in_plain_text_is_not_red(monkeypatch):
    """Plain 'URGENT' without a sigil is now green — no regex on free text."""
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("URGENT please look at this")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_red_sigil_with_leading_whitespace(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("  !page on-call now")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
        assert handle.published[0].payload.text == "page on-call now"
    finally:
        await ep.stop()
