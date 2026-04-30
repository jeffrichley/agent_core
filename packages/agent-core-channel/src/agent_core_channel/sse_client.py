"""Async SSE client: open /notify/<agent>, yield parsed JSON events.

On stream close or connection error, reconnects with exponential backoff
(2s -> 4s -> 8s -> cap 30s by default; configurable for tests). Backoff resets
on successful event reception.

The factory pattern (client_factory=) lets tests inject a fake httpx-like
client; production calls iter_notify_events without overriding it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx

log = logging.getLogger(__name__)


def _default_client_factory() -> httpx.AsyncClient:
    # No total timeout — SSE streams stay open indefinitely. We rely on the
    # underlying connection being closed by the server (or an OS-level
    # disconnect) to break the iteration.
    return httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0))


async def iter_notify_events(
    agent: str,
    daemon_url: str,
    *,
    client_factory: Callable[[], Any] = _default_client_factory,
    max_events: int | None = None,
    backoff_initial: float = 2.0,
    backoff_max: float = 30.0,
) -> AsyncIterator[dict]:
    """Yield JSON events from /notify/<agent> until cancelled.

    Reconnects forever on stream close / connection error, with exponential
    backoff. Successful event reception resets the backoff. ``max_events`` is
    a test hook — when set, the iterator stops after that many events.
    """
    url = f"{daemon_url.rstrip('/')}/notify/{agent}"
    backoff = backoff_initial
    emitted = 0

    while True:
        try:
            async with client_factory() as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload_text = line[len("data: ") :]
                        try:
                            event = json.loads(payload_text)
                        except json.JSONDecodeError:
                            log.warning(
                                "sse client: dropped malformed event for %s: %r",
                                agent,
                                payload_text,
                            )
                            continue
                        backoff = backoff_initial  # reset on successful event
                        yield event
                        emitted += 1
                        if max_events is not None and emitted >= max_events:
                            return
            # Stream ended cleanly; reconnect immediately (no backoff).
            log.debug("sse client: stream for %s closed; reconnecting", agent)
        except Exception as exc:
            log.warning(
                "sse client: connection error for %s: %s; retrying in %.1fs",
                agent,
                exc,
                backoff,
            )
            await anyio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
