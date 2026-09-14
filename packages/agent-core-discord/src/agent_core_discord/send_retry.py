"""Retry policy for Discord ``channel.send`` (rate limits + transient HTTP errors).

discord.py surfaces failures as ``discord.HTTPException`` (and subclasses) with a
``.status`` code. We keep this module import-light: callers pass ``HTTPException``
from discord when available; retryability also keys off duck-typed ``.status``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
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
    # ``2**attempt`` types as Any (int**int may be float for negative exponents),
    # so pin the result to float via the annotated local before returning.
    delay: float = min(DISCORD_SEND_MAX_DELAY_S, DISCORD_SEND_BASE_DELAY_S * (2**attempt))
    return delay


def retry_delay_seconds(exc: BaseException, attempt: int) -> float:
    """Sleep duration before attempt ``attempt`` (0-based) after ``exc``."""
    ra = _retry_after_seconds(exc)
    if ra is not None and ra > 0:
        return min(DISCORD_SEND_MAX_DELAY_S, max(ra, _backoff_seconds(attempt)))
    return _backoff_seconds(attempt)


def _release(files: Sequence[Any] | None) -> None:
    """Close files this module built, swallowing every error.

    The sender normally closes them itself (``MultipartParameters.__exit__``
    runs on the failure path too), so this is almost always a no-op second
    close. It matters for the case where ``send`` raised *before* taking
    ownership: with a factory we build a fresh set per attempt, so without this
    a retry storm would leak one handle per attempt instead of one in total.

    Errors are swallowed deliberately. This runs while an exception is already
    in flight, and a failure to close must not replace the transport error the
    caller actually needs to see.
    """
    for f in files or ():
        closer = getattr(f, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # see docstring: a close error must not mask the real one
                log.debug("ignoring error closing a send attachment", exc_info=True)


async def channel_send_with_retries(
    channel: Any,
    content: str | None,
    *,
    max_attempts: int | None = None,
    files_factory: Callable[[], Sequence[Any]] | None = None,
    **send_kwargs: Any,
) -> Any:
    """Call ``channel.send`` with retries on transient / rate-limit errors.

    Attachments must be passed as ``files_factory``, not ``files``. A
    ``discord.File`` is single-use by the library's own contract, and
    ``MultipartParameters.__exit__`` closes it after **every** send including
    one that raised. So a retry that reuses the list from attempt 1 uploads
    handles that are already closed: the message posts, the send reports
    success, and the attachment silently does not arrive (agent_core#594).

    ``files_factory`` is called once per attempt and must return freshly built
    file objects. Passing both it and ``files`` is a ``TypeError`` rather than a
    precedence rule — a caller who supplies both has a pre-built list that
    cannot survive a retry, and silently preferring the factory would leave that
    hazard in place under a fix's name.

    ``files`` is still accepted for callers with a single-attempt path, but a
    retryable failure carrying pre-built ``files`` is **re-raised instead of
    retried**. That is deliberately worse service and better behaviour: the
    retry could not have delivered the attachment anyway, so its only outcomes
    were a crash or a message posted without its file. Raising the transport
    error that actually happened is the one outcome that tells the truth.

    Args:
        channel: Any object exposing an awaitable ``send(content, **kwargs)``.
        content: Message body, or ``None`` for attachment/embed-only sends.
        max_attempts: Attempt cap; ``None`` uses ``DISCORD_SEND_MAX_ATTEMPTS``.
        files_factory: Zero-arg callable returning fresh file objects per
            attempt. Required for any send that must survive a retry with its
            attachments intact.
        **send_kwargs: Forwarded to ``channel.send`` unchanged.

    Returns:
        Whatever ``channel.send`` returns on the first successful attempt.

    Raises:
        TypeError: If both ``files`` and ``files_factory`` are supplied.
    """
    if files_factory is not None and "files" in send_kwargs:
        raise TypeError(
            "channel_send_with_retries: pass files_factory OR files, not both. "
            "A pre-built file list cannot survive a retry (discord.File is single-use)."
        )
    cap = DISCORD_SEND_MAX_ATTEMPTS if max_attempts is None else max_attempts
    for attempt in range(cap):
        built: list[Any] | None = None
        if files_factory is not None:
            built = list(files_factory())
            send_kwargs["files"] = built
        try:
            return await channel.send(content, **send_kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _release(built)
            if attempt >= cap - 1 or not is_retryable_discord_send_error(exc):
                raise
            if files_factory is None and send_kwargs.get("files"):
                log.warning(
                    "discord send failed with pre-built files and is NOT being retried "
                    "(%s): the file handles are spent, so a retry would post the "
                    "message without its attachment. Pass files_factory to make this "
                    "send retryable.",
                    exc,
                )
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
