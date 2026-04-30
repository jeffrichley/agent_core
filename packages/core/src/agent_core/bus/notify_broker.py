"""Per-agent fan-out broker for notification subscribers.

Used by the /notify/<agent> SSE endpoint to deliver pushed envelope summaries
to a stdio channel relay (or any subscriber). Each subscriber gets its own
bounded queue; publish() fans out a copy of the event to all subscribers for
the agent. Slow consumers drop events with a WARN log — list_pending is
authoritative, so missing one push is recoverable on the next poll.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_DEFAULT_QUEUE_MAX = 128


class NotificationBroker:
    """Fan-out broker for per-agent notification subscribers."""

    def __init__(self, queue_max: int = _DEFAULT_QUEUE_MAX) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict]]] = {}
        self._lock = asyncio.Lock()
        self._queue_max = queue_max

    async def subscribe(self, agent: str) -> asyncio.Queue[dict]:
        """Register a subscriber for *agent* and return its queue."""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._queue_max)
        async with self._lock:
            self._subs.setdefault(agent, set()).add(q)
        return q

    async def unsubscribe(self, agent: str, q: asyncio.Queue[dict]) -> None:
        """Remove a subscriber's queue. Cleans up the agent key when empty."""
        async with self._lock:
            subs = self._subs.get(agent)
            if subs:
                subs.discard(q)
                if not subs:
                    del self._subs[agent]

    async def publish(self, agent: str, event: dict) -> None:
        """Fan-out an event to all current subscribers for *agent*.

        A snapshot of the subscriber set is taken under the lock; we then
        publish without holding the lock so a slow Queue.put cannot block
        unsubscribes. Full queues drop the event with a WARN.
        """
        async with self._lock:
            subs = list(self._subs.get(agent, ()))
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("notify broker: dropped event for %s (slow consumer)", agent)
