"""BusHandle — the per-endpoint surface for bus operations.

Every endpoint receives a fresh BusHandle bound to its registered name. The
handle stamps `from_` to that name on every publish, so endpoints cannot
spoof each other regardless of what they put in the envelope.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import EndpointInfo, Envelope

if TYPE_CHECKING:
    from agent_core.bus.core import Bus
    from agent_core.bus.persistence import Persistence

log = logging.getLogger(__name__)


class BusHandle:
    """A per-endpoint, identity-bound view of the bus.

    The endpoint's `name` is set at construction (by the bus, when registering
    the endpoint) and is overwritten onto every published envelope's `from_`.
    Endpoints never need to know their own name; the handle knows for them.
    """

    def __init__(
        self,
        bus: Bus,
        endpoint_name: str,
        on_task_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._bus = bus
        self._endpoint_name = endpoint_name
        self._tasks: set[asyncio.Task] = set()
        self._on_task_failure = (
            on_task_failure if on_task_failure is not None else self._default_failure
        )

    async def publish(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        """Send an envelope. The bus stamps `from_` to this endpoint's name,
        runs pre_publish hooks, persists, then dispatches.

        If `to` is provided it overrides envelope.to (and may be a list to
        fan out to N recipients via N envelopes — handled by the bus)."""
        stamped = envelope.model_copy(update={"from_": self._endpoint_name})
        await self._bus._enqueue(stamped, to)

    async def ack(self, envelope_id: str) -> None:
        """Confirm successful handling. Idempotent."""
        await self._bus._ack(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        """Reject a delivered envelope. requeue=True schedules redelivery."""
        await self._bus._nack(envelope_id, requeue)

    def endpoints(self) -> list[EndpointInfo]:
        """Snapshot of currently-registered endpoints (name + description)."""
        return self._bus._endpoints()

    def persistence(self) -> Persistence:
        """Return the bus's persistence store.

        Available after Bus.start() has run. Raises RuntimeError if called
        before the bus's storage layer is initialized — endpoints should
        call this from start(handle) onward, not __init__.

        Exposed primarily for the read-only audit/tail surface (see
        agent_core.bus_tail). Most endpoints should publish/ack/nack via
        this BusHandle's other methods rather than touch persistence
        directly.
        """
        return self._bus._require_store()

    def spawn(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        name: str | None = None,
    ) -> asyncio.Task:
        """Create and track a background asyncio task.

        The task is registered in this handle's internal set. On completion,
        the task is removed from the set. If the task raises an exception
        (other than CancelledError), the injected failure hook is called
        exactly once — the event loop is not crashed.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        """Done callback: remove task from set and route non-cancellation exceptions."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._on_task_failure(exc)

    def _default_failure(self, exc: BaseException) -> None:
        """Default failure hook: log the exception as an error."""
        log.error(
            "endpoint %r: background task raised (endpoint may need restart)",
            self._endpoint_name,
            exc_info=exc,
        )

    async def _drain_tasks(self) -> None:
        """Cancel and await all outstanding tracked tasks.

        Called by the bus on endpoint stop (and start-rollback). CancelledError
        from draining is NOT routed to the failure hook.
        """
        tasks = list(self._tasks)
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
