"""Endpoint and BusHook protocols + EndpointUnavailable exception.

The Endpoint protocol is the minimal interface every adapter satisfies.
@runtime_checkable lets the bus verify Protocol conformance at load time.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from agent_core.bus.envelope import Envelope
from agent_core.bus.notify_broker import NotificationBroker

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


class EndpointUnavailable(Exception):
    """Raised by Endpoint.deliver() to signal a temporary failure.

    The bus will pause delivery to this endpoint, queue subsequent envelopes
    in the mailbox, and retry on a backoff. Any other exception is treated
    as terminal and the envelope moves to dead-letter.
    """


@dataclass(frozen=True)
class SlowDeliverWarning:
    """Emitted (via structured log) when deliver() exceeds slow_deliver_warn_seconds.

    Warn-only — no delivery semantics are altered. Provides observability
    for Theme E and future T4 escalation.
    """

    endpoint: str
    envelope_id: str
    elapsed_seconds: float


@runtime_checkable
class Endpoint(Protocol):
    """An addressable participant on the bus.

    Implementation contract (MUST):

    - ``__init__`` MUST be cheap — no model loads, blocking I/O, or network.
      Heavy or slow setup belongs in ``start()``, which is async and awaited
      during boot.
    - ``deliver()`` MUST return promptly. Long work MUST be offloaded to a
      tracked background task via ``bus.spawn(coro, name=...)``.  Blocking the
      event loop stalls delivery to every other endpoint; the watchdog in
      ``Bus._dispatch`` emits ``SlowDeliverWarning`` when the threshold is
      exceeded.
    """

    name: str

    async def start(self, bus: "BusHandle") -> None:
        """Bus is ready. Open connections, register listeners, start your loop."""

    async def deliver(self, envelope: Envelope) -> None:
        """Bus is delivering an envelope addressed to you.

        You MUST eventually call bus.ack(envelope.id) when handling completes.
        Raise EndpointUnavailable to signal temporary failure (bus will retry).
        Other exceptions are terminal — envelope moves to dead-letter.

        The bus awaits this call before dispatching the next envelope to ANY
        endpoint, so deliver() MUST return promptly. Long work MUST be offloaded
        to a tracked background task via bus.spawn(coro, name=...). Blocking the
        event loop stalls delivery to every other endpoint; the watchdog in
        Bus._dispatch emits SlowDeliverWarning when the threshold is exceeded.
        """

    async def stop(self) -> None:
        """Graceful shutdown. Close connections, flush state."""


@runtime_checkable
class NotificationBrokerAwareEndpoint(Protocol):
    """Optional endpoint capability for broker-driven notifications."""

    def attach_notify_broker(self, broker: NotificationBroker) -> None:
        """Attach NotificationBroker after construction."""


@runtime_checkable
class AuditWriterAwareEndpoint(Protocol):
    """Optional endpoint capability for MCP tool-call audit logging.

    Endpoints that host an MCP server (FastMCP) can opt in to the
    daemon-wide audit log by implementing this protocol. The runner
    calls ``attach_audit_writer`` after construction (mirrors the
    ``NotificationBrokerAwareEndpoint`` wiring).
    """

    def attach_audit_writer(
        self,
        writer: object,  # MCPAuditWriter — referenced by name to avoid an import-cycle here
        skip_tools: frozenset[str],
    ) -> None:
        """Attach the daemon audit writer; register the middleware on this endpoint's MCP server."""


@runtime_checkable
class BusHook(Protocol):
    """A hook that runs at the pre_publish or pre_deliver pipeline stage."""

    async def execute(
        self,
        stage: Literal["pre_publish", "pre_deliver"],
        envelope: Envelope,
        params: dict,
    ) -> Envelope | None:
        """Return the (possibly modified) envelope to continue.
        Return None to drop the envelope.
        Raising aborts the operation and surfaces the error to the caller.
        """
