"""DiscordEndpoint applies urgency-red regex rule on inbound TextMessage envelopes."""

from __future__ import annotations

import json

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
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_urgent_keyword_promotes_to_red(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("URGENT please look at this")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_now_keyword_case_insensitive(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("can you reply Now please")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_substring_does_not_match(monkeypatch):
    """'now' inside 'snowfall' must not promote — \\b boundaries enforced."""
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("the snowfall is heavy today")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_custom_regex_via_access_config(monkeypatch, tmp_path):
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"urgencyRedRegex": r"(?i)\bfire\b"}), encoding="utf-8")
    ep, handle, fake = await _start(monkeypatch, access_path=str(access))
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("the server is on fire")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_disabled_regex_via_empty_string(monkeypatch, tmp_path):
    """Empty urgencyRedRegex disables the rule — all inbound is green."""
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"urgencyRedRegex": ""}), encoding="utf-8")
    ep, handle, fake = await _start(monkeypatch, access_path=str(access))
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("URGENT URGENT URGENT")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()
