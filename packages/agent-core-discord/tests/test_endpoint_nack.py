"""Tests for DiscordEndpoint.deliver() ack-vs-nack behaviour (issue #275).

Covers four paths through deliver():
  - 429 rate-limit  → EndpointUnavailable (transient, requeue)
  - 5xx server error → EndpointUnavailable (transient, requeue)
  - 403 terminal 4xx → acks with error Acknowledgment (agent-visible)
  - success          → acks with success Acknowledgment

Strict fakes carry the real duck-typed shape of discord.HTTPException
(``status`` int, ``retry_after`` float for 429) so they exercise the
same ``is_retryable_discord_send_error`` path that production code uses.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agent_core.bus.envelope import (
    EndpointInfo,
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core.bus.protocol import EndpointUnavailable
from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import (
    FakeChannel,
    FakeDiscordClient,
    FakeMessage,
)


# ---------------------------------------------------------------------------
# Strict fake HTTP-like exceptions.
# Each carries `.status` exactly as discord.HTTPException does.  ``_HTTP429Like``
# also carries `.retry_after` because ``is_retryable_discord_send_error`` reads
# it via ``_retry_after_seconds``.
# ---------------------------------------------------------------------------


class _HTTP429Like(Exception):
    """Minimal duck-typed stand-in for discord.HTTPException on rate limit."""

    def __init__(self, *, retry_after: float = 0.0) -> None:
        super().__init__("429 Too Many Requests")
        self.status = 429
        self.retry_after = retry_after


class _HTTP5xxLike(Exception):
    """Minimal duck-typed stand-in for discord.HTTPException on server error."""

    def __init__(self, status: int = 503) -> None:
        super().__init__(f"{status} Server Error")
        self.status = status


class _HTTP403Like(Exception):
    """Minimal duck-typed stand-in for discord.HTTPException on Forbidden."""

    def __init__(self) -> None:
        super().__init__("403 Forbidden")
        self.status = 403


# ---------------------------------------------------------------------------
# Channel fakes that always raise the specified error.
# ---------------------------------------------------------------------------


class _AlwaysRaises429Channel(FakeChannel):
    """Every ``send`` call raises _HTTP429Like (simulates persistent rate-limit)."""

    async def send(
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: object | None = None,
        files: list | None = None,
        poll: object | None = None,
    ) -> FakeMessage:
        raise _HTTP429Like(retry_after=0.0)


class _AlwaysRaises5xxChannel(FakeChannel):
    """Every ``send`` call raises _HTTP5xxLike (simulates persistent server error)."""

    async def send(
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: object | None = None,
        files: list | None = None,
        poll: object | None = None,
    ) -> FakeMessage:
        raise _HTTP5xxLike(503)


class _AlwaysRaises403Channel(FakeChannel):
    """Every ``send`` call raises _HTTP403Like (terminal 4xx — not retried)."""

    async def send(
        self,
        content: str | None = None,
        *,
        embeds: list | None = None,
        reference: object | None = None,
        files: list | None = None,
        poll: object | None = None,
    ) -> FakeMessage:
        raise _HTTP403Like()


# ---------------------------------------------------------------------------
# Recording BusHandle fake.
# ---------------------------------------------------------------------------


class _Recording:
    def __init__(self) -> None:
        self.published: list[Envelope] = []
        self.acked: set[str] = set()

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None:
        self.acked.add(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        pass

    def endpoints(self) -> list[EndpointInfo]:
        return []


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _tool_envelope(env_id: str, tool: str, args: dict) -> Envelope:
    return Envelope(
        id=env_id,
        correlation_id=uuid.uuid4().hex,
        from_="agent-test",
        to="discord-test",
        kind="ToolInvocation",
        payload=ToolInvocationPayload(tool=tool, args=args),
        created_at=datetime.now(UTC),
    )


async def _started(
    monkeypatch,
    channel: FakeChannel,
) -> tuple[DiscordEndpoint, _Recording]:
    """Create and start a DiscordEndpoint with the given channel pre-registered."""
    monkeypatch.setenv("X_TOK", "tok")
    # Skip asyncio.sleep so retry loops in channel_send_with_retries don't stall.
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    handle = _Recording()
    fake = FakeDiscordClient()
    fake.add_channel(channel)
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_429_raises_endpoint_unavailable(monkeypatch):
    """A 429 from Discord (after retries exhausted) must raise EndpointUnavailable.

    No ack may be issued and no error Acknowledgment published — the bus
    requeues the envelope for a later retry.
    """
    ch = _AlwaysRaises429Channel(id="ch-429")
    ep, handle = await _started(monkeypatch, ch)
    env = _tool_envelope("e-429", "send", {"channel_id": "ch-429", "text": "hello"})
    try:
        with pytest.raises(EndpointUnavailable):
            await ep.deliver(env)
        assert "e-429" not in handle.acked, "envelope must not be acked on 429"
        assert not any(
            e.kind == "Acknowledgment" for e in handle.published
        ), "no error Acknowledgment must be published on 429"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_5xx_raises_endpoint_unavailable(monkeypatch):
    """A 5xx server error from Discord (after retries exhausted) must raise EndpointUnavailable.

    No ack may be issued and no error Acknowledgment published — the bus
    requeues the envelope for a later retry.
    """
    ch = _AlwaysRaises5xxChannel(id="ch-5xx")
    ep, handle = await _started(monkeypatch, ch)
    env = _tool_envelope("e-5xx", "send", {"channel_id": "ch-5xx", "text": "hello"})
    try:
        with pytest.raises(EndpointUnavailable):
            await ep.deliver(env)
        assert "e-5xx" not in handle.acked, "envelope must not be acked on 5xx"
        assert not any(
            e.kind == "Acknowledgment" for e in handle.published
        ), "no error Acknowledgment must be published on 5xx"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_403_terminal_acks_with_error(monkeypatch):
    """A 403 Forbidden (terminal 4xx, not rate-limit) must ack with an error Acknowledgment.

    The agent receives an error Acknowledgment so it knows the send failed.
    The envelope is not requeued — 403 is a permanent failure.
    """
    ch = _AlwaysRaises403Channel(id="ch-403")
    ep, handle = await _started(monkeypatch, ch)
    env = _tool_envelope("e-403", "send", {"channel_id": "ch-403", "text": "hello"})
    try:
        await ep.deliver(env)
        assert "e-403" in handle.acked, "envelope must be acked on terminal 403"
        error_acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert len(error_acks) == 1, "one error Acknowledgment must be published on 403"
        assert error_acks[0].payload.note.lower().startswith("error:")
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_success_acks(monkeypatch):
    """A successful Discord send must ack and publish a success Acknowledgment."""
    ch = FakeChannel(id="ch-ok")
    ep, handle = await _started(monkeypatch, ch)
    env = _tool_envelope("e-ok", "send", {"channel_id": "ch-ok", "text": "hello"})
    try:
        await ep.deliver(env)
        assert "e-ok" in handle.acked, "envelope must be acked on success"
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert len(acks) == 1, "one Acknowledgment must be published on success"
        assert acks[0].urgency == "green"
    finally:
        await ep.stop()
