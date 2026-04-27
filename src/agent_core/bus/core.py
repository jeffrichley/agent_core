"""Bus core — endpoint registration and lifecycle.

Single asyncio event loop. Endpoints register before start; the bus
constructs a per-endpoint BusHandle and calls endpoint.start().

Dispatch (Task 8/9), ack/nack (Task 9), and sweeps (Task 10) are not
yet implemented — `_enqueue`, `_ack`, and `_nack` raise NotImplementedError
until those tasks land.
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
    # Per-envelope: seconds an in-flight delivery is allowed before being considered stuck and requeued.
    redelivery_timeout_seconds: int = 300
    max_delivery_attempts: int = 5
    # Bus-level: how often the TTL sweep loop runs.
    ttl_sweep_seconds: int = 60
    # Bus-level: how often the redelivery (in-flight timeout) sweep loop runs.
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
        started_specs: list[EndpointSpec] = []
        try:
            for spec in self._endpoints_by_name.values():
                handle = BusHandle(self, spec.name)
                await spec.endpoint.start(handle)
                started_specs.append(spec)
        except Exception:
            for spec in reversed(started_specs):
                try:
                    await spec.endpoint.stop()
                except Exception:
                    log.exception("error stopping endpoint %s during failed start", spec.name)
            await self._store.close()
            self._store = None
            raise
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
