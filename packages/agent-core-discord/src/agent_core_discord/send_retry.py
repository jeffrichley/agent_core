"""Retry policy for Discord ``channel.send`` (rate limits + transient HTTP errors).

discord.py surfaces failures as ``discord.HTTPException`` (and subclasses) with a
``.status`` code. We keep this module import-light: callers pass ``HTTPException``
from discord when available; retryability also keys off duck-typed ``.status``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

DISCORD_SEND_MAX_ATTEMPTS = 5
DISCORD_SEND_BASE_DELAY_S = 0.5
DISCORD_SEND_MAX_DELAY_S = 30.0


def _http_status(exc: BaseException) -> int | None:
    st = getattr(exc, "status", None)
    return int(st) if isinstance(st, int) else None


def is_retryable_discord_send_error(exc: BaseException) -> bool:
    """Return True if a send failure is plausibly transient (worth sleeping + retry)."""
    status = _http_status(exc)
    if status == 429:
        return True
    if status is not None and 500 <= status <= 599:
        return True
    if status == 408:
        return True
    # discord.py may wrap network issues
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        import errno

        if exc.errno in (errno.ECONNRESET, errno.ETIMEDOUT, errno.EPIPE):
            return True
    return False


def _retry_after_seconds(exc: BaseException) -> float | None:
    ra = getattr(exc, "retry_after", None)
    if isinstance(ra, (int, float)) and ra >= 0:
        return float(ra)
    return None


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff, capped."""
    return min(DISCORD_SEND_MAX_DELAY_S, DISCORD_SEND_BASE_DELAY_S * (2**attempt))


def retry_delay_seconds(exc: BaseException, attempt: int) -> float:
    """Sleep duration before attempt ``attempt`` (0-based) after ``exc``."""
    ra = _retry_after_seconds(exc)
    if ra is not None and ra > 0:
        return min(DISCORD_SEND_MAX_DELAY_S, max(ra, _backoff_seconds(attempt)))
    return _backoff_seconds(attempt)


async def channel_send_with_retries(
    channel: Any,
    content: str | None,
    *,
    max_attempts: int | None = None,
    **send_kwargs: Any,
) -> Any:
    """Call ``channel.send`` with retries on transient / rate-limit errors."""
    cap = DISCORD_SEND_MAX_ATTEMPTS if max_attempts is None else max_attempts
    for attempt in range(cap):
        try:
            return await channel.send(content, **send_kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt >= cap - 1 or not is_retryable_discord_send_error(exc):
                raise
            delay = retry_delay_seconds(exc, attempt)
            log.warning(
                "discord send retry %s/%s after %s: sleeping %.2fs",
                attempt + 1,
                cap,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("channel_send_with_retries: unreachable")
