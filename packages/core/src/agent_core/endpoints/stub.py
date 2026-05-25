"""StubEndpoint — a simple in-memory adapter for tests and development.

Records every envelope delivered to it on a `.inbox` list. Optionally
auto-acks. Provides a `.send()` helper for tests that want to publish from
the stub's identity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import Envelope, EnvelopePayload

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


class StubEndpoint:
    """A minimal Endpoint suitable for tests and as a dev-mode echo."""

    def __init__(self, name: str, *, auto_ack: bool = True):
        self.name = name
        self.auto_ack = auto_ack
        self.inbox: list[Envelope] = []
        self._handle: BusHandle | None = None

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.inbox.append(envelope)
        if self.auto_ack and self._handle is not None:
            await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        self._handle = None

    async def send(
        self,
        *,
        to: str,
        kind: str,
        payload: EnvelopePayload,
        correlation_id: str | None = None,
        in_reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        """Convenience: build and publish an envelope from this endpoint."""
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")
        env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=correlation_id or uuid.uuid4().hex,
            in_reply_to=in_reply_to,
            to=to,
            kind=kind,
            payload=payload,
            metadata=metadata or {},
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        await self._handle.publish(env)
