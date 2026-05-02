"""Hardening tests for batch C: URL files rejection, registry collision guard,
add_listener routing, args caps, embed total cap."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from agent_core_discord import endpoint as endpoint_mod
from agent_core_discord.args import _DownloadAttachmentsArgs, _FetchArgs
from agent_core_discord.endpoint import DiscordEndpoint, _check_embeds_within_caps
from pydantic import ValidationError

from agent_core.bus.envelope import EndpointInfo, Envelope, ToolInvocationPayload
from tests.conftest import _FakeChannel, _FakeDiscordClient


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


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


# --- #4: _send rejects URL strings in `files` ---


@pytest.mark.asyncio
async def test_send_rejects_http_url_in_files(monkeypatch):
    """`files` must be local paths; URLs return a clear error, not a confusing FileNotFoundError."""
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
    fake.add_channel(_FakeChannel(id="200"))
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {
                    "channel_id": "200",
                    "text": "with attachment",
                    "files": ["https://example.com/picture.png"],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "must be local paths" in ack.payload.note.lower()
        assert "url" in ack.payload.note.lower()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_send_rejects_https_url_in_files(monkeypatch):
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
    fake.add_channel(_FakeChannel(id="200"))
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {
                    "channel_id": "200",
                    "text": "x",
                    "files": ["http://example.com/x.png"],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "must be local paths" in ack.payload.note.lower()
    finally:
        await ep.stop()


# --- #6: _active_endpoints collision guard ---


@pytest.mark.asyncio
async def test_active_endpoints_collision_guard(monkeypatch):
    """Two live instances with the same name must not silently shadow each other."""
    monkeypatch.setenv("X_TOK", "tok")
    fake_a = _FakeDiscordClient()
    ep_a = DiscordEndpoint(
        name="dup",
        target="agent-a",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake_a,
    )
    await ep_a.start(_Recording())
    try:
        fake_b = _FakeDiscordClient()
        ep_b = DiscordEndpoint(
            name="dup",
            target="agent-b",
            token_env="X_TOK",
            _client_factory=lambda **kw: fake_b,
        )
        with pytest.raises(RuntimeError, match="already registered"):
            await ep_b.start(_Recording())
        # The original instance still owns the slot.
        assert endpoint_mod._active_endpoints["dup"] is ep_a
    finally:
        await ep_a.stop()


# --- #7: add_listener routes events correctly ---


@pytest.mark.asyncio
async def test_handlers_registered_via_add_listener(monkeypatch):
    """Verify listeners flow through the fake's add_listener, not just event()."""
    monkeypatch.setenv("X_TOK", "tok")
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(_Recording())
    try:
        # All three listeners should be registered under their event names.
        assert "on_message" in fake._handlers
        assert "on_reaction_add" in fake._handlers
        assert "on_ready" in fake._handlers
    finally:
        await ep.stop()


# --- #8: args caps ---


def test_fetch_args_rejects_limit_above_cap():
    with pytest.raises(ValidationError):
        _FetchArgs(channel_id="200", limit=10_000_000)


def test_fetch_args_rejects_negative_limit():
    with pytest.raises(ValidationError):
        _FetchArgs(channel_id="200", limit=0)


def test_fetch_args_default_limit_is_50():
    args = _FetchArgs(channel_id="200")
    assert args.limit == 50


def test_download_attachments_args_rejects_too_many_urls():
    with pytest.raises(ValidationError):
        _DownloadAttachmentsArgs(
            channel_id="200",
            message_id="m",
            attachment_urls=[f"http://x/{i}.png" for i in range(51)],
        )


# --- #11: embed total char cap ---


def test_check_embeds_within_caps_passes_under_limit():
    embeds = [
        {"title": "hi", "description": "world"},
        {"title": "second", "description": "embed"},
    ]
    # Should not raise.
    _check_embeds_within_caps(embeds)


def test_check_embeds_within_caps_raises_over_limit():
    """Total chars across embeds must not exceed Discord's 6000-char cap."""
    huge = "A" * 4000
    embeds = [
        {"title": "x", "description": huge},
        {"title": "y", "description": huge},
    ]
    from agent_core_discord.endpoint import _ToolError

    with pytest.raises(_ToolError, match="exceeds Discord cap"):
        _check_embeds_within_caps(embeds)


def test_check_embeds_counts_fields_and_footer():
    """Field name+value, footer.text, and author.name all count."""
    embeds = [
        {
            "title": "t",
            "description": "d",
            "fields": [
                {"name": "n1", "value": "v1"},
                {"name": "n2", "value": "v2"},
            ],
            "footer": {"text": "ftext"},
            "author": {"name": "aname"},
        }
    ]
    from agent_core_discord.endpoint import _embed_char_count

    n = _embed_char_count(embeds[0])
    # title 1 + desc 1 + n1 2 + v1 2 + n2 2 + v2 2 + ftext 5 + aname 5 = 20
    assert n == 20


@pytest.mark.asyncio
async def test_send_rejects_oversized_embeds(monkeypatch):
    """End-to-end: send with > 6000-char embeds returns an error ack."""
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
    fake.add_channel(_FakeChannel(id="200"))
    huge = "A" * 4000
    try:
        env = _envelope(
            "e",
            "agent-test",
            "discord-test",
            _toolcall(
                "send",
                {
                    "channel_id": "200",
                    "embeds": [
                        {"title": "a", "description": huge},
                        {"title": "b", "description": huge},
                    ],
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment"][0]
        assert "exceeds discord cap" in ack.payload.note.lower()
    finally:
        await ep.stop()


# --- #10: dotenv ImportError is non-fatal ---


@pytest.mark.asyncio
async def test_start_skips_env_file_when_dotenv_missing(monkeypatch, tmp_path):
    """If dotenv is not installed, start() should log + skip, not crash."""
    monkeypatch.setenv("X_TOK", "tok")
    env_file = tmp_path / ".env"
    env_file.write_text("X_OTHER=abc\n", encoding="utf-8")

    # Simulate dotenv import failure by stripping it from sys.modules and
    # blocking the import.
    import sys

    real_dotenv = sys.modules.pop("dotenv", None)

    class _Blocker:
        def find_module(self, name, path=None):
            if name == "dotenv":
                return self

        def load_module(self, name):
            raise ImportError("dotenv blocked for test")

    sys.meta_path.insert(0, _Blocker())
    try:
        fake = _FakeDiscordClient()
        ep = DiscordEndpoint(
            name="discord-test",
            target="agent-test",
            token_env="X_TOK",
            env_file=env_file,
            _client_factory=lambda **kw: fake,
        )
        # Token already set in environ by monkeypatch, so skipping dotenv must
        # not block start.
        await ep.start(_Recording())
        await ep.stop()
    finally:
        # Restore dotenv import path.
        sys.meta_path = [m for m in sys.meta_path if not isinstance(m, _Blocker)]
        if real_dotenv is not None:
            sys.modules["dotenv"] = real_dotenv
