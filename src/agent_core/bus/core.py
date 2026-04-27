"""Bus core — endpoint registration, lifecycle, dispatch, sweeps.

Single asyncio event loop. Endpoints register before start; once started, the
bus drains pending envelopes from each endpoint's mailbox. publish() persists
then dispatches if the endpoint is live; otherwise mail queues durably.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agent_core.bus.envelope import EndpointInfo, Envelope
from agent_core.bus.handle import BusHandle
from agent_core.bus.persistence import Persistence
from agent_core.bus.protocol import Endpoint

log = logging.getLogger(__name__)


@dataclass
class BusConfig:
    storage_path: Path
    redelivery_timeout_seconds: int = 300
    max_delivery_attempts: int = 5
    ttl_sweep_seconds: int = 60
    redelivery_sweep_seconds: int = 10
    acked_retention_days: int = 14
    max_pending_per_endpoint: int = 10_000


@dataclass
class EndpointSpec:
    endpoint: Endpoint
    description: str = ""

    @property
    def name(self) -> str:
        return self.endpoint.name


class Bus:
    """In-process bus router."""

    def __init__(self, config: BusConfig):
        self.config = config
        self._endpoints_by_name: dict[str, EndpointSpec] = {}
        self._store: Persistence | None = None
        self._started = False

    def register(self, spec: EndpointSpec) -> None:
        if spec.name in self._endpoints_by_name:
            raise ValueError(f"Endpoint '{spec.name}' already registered")
        self._endpoints_by_name[spec.name] = spec

    async def start(self) -> None:
        if self._started:
            return
        self._store = Persistence(self.config.storage_path)
        await self._store.connect()
        for spec in self._endpoints_by_name.values():
            handle = BusHandle(self, spec.name)
            await spec.endpoint.start(handle)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            if self._store is not None:
                await self._store.close()
            return
        for spec in reversed(list(self._endpoints_by_name.values())):
            try:
                await spec.endpoint.stop()
            except Exception:
                log.exception("error stopping endpoint %s", spec.name)
        if self._store is not None:
            await self._store.close()
        self._started = False

    # BusHandle-facing surface — implemented in Tasks 8/9
    async def _enqueue(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        raise NotImplementedError

    async def _ack(self, envelope_id: str) -> None:
        raise NotImplementedError

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        raise NotImplementedError

    def _endpoints(self) -> list[EndpointInfo]:
        return [
            EndpointInfo(name=spec.name, description=spec.description)
            for spec in self._endpoints_by_name.values()
        ]
