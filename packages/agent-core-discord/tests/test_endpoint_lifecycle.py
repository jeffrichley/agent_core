"""Tests for DiscordEndpoint construction, start(), stop(), and registry."""

from __future__ import annotations

import os

import pytest

from agent_core.bus.protocol import Endpoint
from agent_core_discord.endpoint import DiscordEndpoint, _active_endpoints


class _FakeBusHandle:
    async def publish(self, *a, **kw): ...
    async def ack(self, *a, **kw): ...
    async def nack(self, *a, **kw): ...
    def endpoints(self):
        return []


def test_endpoint_satisfies_endpoint_protocol():
    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    assert isinstance(ep, Endpoint)


def test_endpoint_exposes_required_attrs():
    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    assert ep.name == "discord-test"
    assert ep.target == "agent-test"
    assert ep.token_env == "X"


def test_endpoint_default_attachments_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    assert "agent-core" in str(ep.attachments_dir)
    assert "discord-test" in str(ep.attachments_dir)


def test_endpoint_custom_attachments_dir(tmp_path):
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X",
        attachments_dir=str(tmp_path / "att"),
    )
    assert ep.attachments_dir == tmp_path / "att"


def test_endpoint_tilde_expansion_for_paths():
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X",
        env_file="~/.test/.env",
        access_config_path="~/.test/access.json",
    )
    assert "~" not in str(ep.env_file)
    assert "~" not in str(ep.access_config_path)


@pytest.mark.asyncio
async def test_start_raises_when_token_env_var_missing(monkeypatch):
    monkeypatch.delenv("DISCORD_TEST_TOKEN_MISSING", raising=False)
    ep = DiscordEndpoint(
        name="discord-test", target="agent-test", token_env="DISCORD_TEST_TOKEN_MISSING"
    )
    with pytest.raises(RuntimeError, match="DISCORD_TEST_TOKEN_MISSING"):
        await ep.start(_FakeBusHandle())


@pytest.mark.asyncio
async def test_start_loads_env_file_into_environ(tmp_path, monkeypatch):
    """If env_file is set, python-dotenv populates os.environ before token lookup."""
    from tests.conftest import _FakeDiscordClient

    monkeypatch.delenv("DISCORD_FROM_FILE_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("DISCORD_FROM_FILE_TOKEN=tok-from-file\n", encoding="utf-8")

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="DISCORD_FROM_FILE_TOKEN",
        env_file=str(env),
        _client_factory=lambda **kw: _FakeDiscordClient(**kw),
    )
    await ep.start(_FakeBusHandle())
    try:
        assert os.environ["DISCORD_FROM_FILE_TOKEN"] == "tok-from-file"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_start_registers_in_active_endpoints(monkeypatch):
    from tests.conftest import _FakeDiscordClient

    monkeypatch.setenv("X_TOK", "tok")
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: _FakeDiscordClient(**kw),
    )
    await ep.start(_FakeBusHandle())
    try:
        assert _active_endpoints["discord-test"] is ep
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_stop_deregisters_and_closes_client(monkeypatch):
    from tests.conftest import _FakeDiscordClient

    monkeypatch.setenv("X_TOK", "tok")
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: _FakeDiscordClient(**kw),
    )
    await ep.start(_FakeBusHandle())
    fake = ep._client
    await ep.stop()
    assert "discord-test" not in _active_endpoints
    assert fake._closed is True


@pytest.mark.asyncio
async def test_deliver_raises_when_not_started():
    import uuid
    from datetime import datetime, timezone

    from agent_core.bus.envelope import Envelope, ToolInvocationPayload
    from agent_core.bus.protocol import EndpointUnavailable

    ep = DiscordEndpoint(name="discord-test", target="agent-test", token_env="X")
    env = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to="discord-test",
        kind="ToolInvocation",
        payload=ToolInvocationPayload(tool="send", args={}),
        created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(EndpointUnavailable):
        await ep.deliver(env)
