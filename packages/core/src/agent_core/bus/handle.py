"""BusHandle — the per-endpoint surface for bus operations.

Every endpoint receives a fresh BusHandle bound to its registered name. The
handle stamps `from_` to that name on every publish, so endpoints cannot
spoof each other regardless of what they put in the envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_core.bus.envelope import EndpointInfo, Envelope

if TYPE_CHECKING:
    from agent_core.bus.core import Bus
    from agent_core.bus.persistence import Persistence


class BusHandle:
    """A per-endpoint, identity-bound view of the bus.

    The endpoint's `name` is set at construction (by the bus, when registering
    the endpoint) and is overwritten onto every published envelope's `from_`.
    Endpoints never need to know their own name; the handle knows for them.
    """

    def __init__(self, bus: Bus, endpoint_name: str):
        self._bus = bus
        self._endpoint_name = endpoint_name

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
