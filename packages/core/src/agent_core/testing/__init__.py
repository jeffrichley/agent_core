"""Shared test-support utilities for agent_core and its sibling packages.

Shipped in ``core`` (which every package depends on) so any package's tests can
``from agent_core.testing import wait_until`` — mirroring the
``agent_core_discord.testing`` convention.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

__all__ = ["wait_until"]


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 10.0,
    interval: float = 0.05,
    message: str = "condition not met",
) -> None:
    """Await until ``predicate()`` is truthy, or fail once ``timeout`` elapses.

    The deterministic replacement for a blind ``asyncio.sleep(N)`` before an
    assertion: wait for the *actual* condition (a subscriber registered, a
    config reloaded, an event published) instead of betting a fixed duration is
    long enough. This does not race a slow/loaded CI runner the way a fixed
    sleep does.

    Args:
        predicate: A zero-arg callable checked each poll; polling stops as soon
            as it returns truthy.
        timeout: Maximum seconds to wait before failing. Defaults to 10.
        interval: Seconds between polls. Defaults to 0.05.
        message: Appended to the failure to say what was being waited for.

    Raises:
        AssertionError: If ``predicate`` is still falsy after ``timeout`` seconds.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"wait_until timed out after {timeout}s: {message}")
        await asyncio.sleep(interval)
