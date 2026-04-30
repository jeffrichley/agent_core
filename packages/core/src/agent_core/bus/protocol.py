"""Endpoint and BusHook protocols + EndpointUnavailable exception.

The Endpoint protocol is the minimal interface every adapter satisfies.
@runtime_checkable lets the bus verify Protocol conformance at load time.
"""

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


@runtime_checkable
class Endpoint(Protocol):
    """An addressable participant on the bus."""

    name: str

    async def start(self, bus: "BusHandle") -> None:
        """Bus is ready. Open connections, register listeners, start your loop."""

    async def deliver(self, envelope: Envelope) -> None:
        """Bus is delivering an envelope addressed to you.

        You MUST eventually call bus.ack(envelope.id) when handling completes.
        Raise EndpointUnavailable to signal temporary failure (bus will retry).
        Other exceptions are terminal — envelope moves to dead-letter.

        The bus awaits this call before dispatching the next envelope to ANY
        endpoint, so deliver() should return promptly. Long work belongs in
        a background task — return after acking, then do the work and
        publish a Progress envelope or follow-up reply when ready.
        """

    async def stop(self) -> None:
        """Graceful shutdown. Close connections, flush state."""


@runtime_checkable
class NotificationBrokerAwareEndpoint(Protocol):
    """Optional endpoint capability for broker-driven notifications."""

    def attach_notify_broker(self, broker: NotificationBroker) -> None:
        """Attach NotificationBroker after construction."""


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
