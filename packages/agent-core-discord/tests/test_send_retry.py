"""Unit tests for ``send_retry`` helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from agent_core_discord.send_retry import (
    DISCORD_SEND_MAX_ATTEMPTS,
    is_retryable_discord_send_error,
    retry_delay_seconds,
)


class _Exc(Exception):
    def __init__(self, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(str(status))
        if status is not None:
            self.status = status
        if retry_after is not None:
            self.retry_after = retry_after


def test_retryable_429_and_503() -> None:
    assert is_retryable_discord_send_error(_Exc(429)) is True
    assert is_retryable_discord_send_error(_Exc(503)) is True
    assert is_retryable_discord_send_error(_Exc(408)) is True


def test_not_retryable_4xx_client() -> None:
    assert is_retryable_discord_send_error(_Exc(400)) is False
    assert is_retryable_discord_send_error(_Exc(404)) is False


def test_retry_delay_uses_retry_after_when_present() -> None:
    d = retry_delay_seconds(_Exc(429, retry_after=2.5), attempt=0)
    assert d >= 2.5


@pytest.mark.asyncio
async def test_channel_send_with_retries_exhausts(monkeypatch):
    from agent_core_discord.send_retry import channel_send_with_retries

    calls = 0

    class _Ch:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            nonlocal calls
            calls += 1
            raise _Exc(429)

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(_Exc):
        await channel_send_with_retries(_Ch(), "hi", max_attempts=3)
    assert calls == 3


@pytest.mark.asyncio
async def test_channel_send_with_retries_succeeds_second_try(monkeypatch):
    from agent_core_discord.send_retry import channel_send_with_retries

    calls = 0

    class _Ch:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _Exc(429)
            return "ok"

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    out = await channel_send_with_retries(_Ch(), "hi", max_attempts=DISCORD_SEND_MAX_ATTEMPTS)
    assert out == "ok"
    assert calls == 2
