"""Bus core — endpoint registration, lifecycle, dispatch, sweeps.

Single asyncio event loop. Endpoints register before start; the bus
constructs a per-endpoint BusHandle and calls endpoint.start().

Dispatch is awaited synchronously per envelope: the bus calls
endpoint.deliver() and waits for it to return (or raise) before
moving on. Endpoints that need to do long work should return promptly
from deliver() and continue work in a background task.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from agent_core.bus.envelope import EndpointInfo, Envelope
from agent_core.bus.handle import BusHandle
from agent_core.bus.persistence import Persistence
from agent_core.bus.protocol import BusHook, Endpoint

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


@dataclass
class BusHookSpec:
    hook: BusHook
    params: dict = field(default_factory=dict)


class MailboxFull(Exception):
    """Raised when an endpoint's pending mailbox has reached max_pending_per_endpoint."""


class Bus:
    """In-process bus router."""

    def __init__(self, config: BusConfig):
        self.config = config
        self._endpoints_by_name: dict[str, EndpointSpec] = {}
        self._hooks: dict[str, list[BusHookSpec]] = {
            "pre_publish": [],
            "pre_deliver": [],
        }
        self._store: Persistence | None = None
        self._started = False

    def _require_store(self) -> Persistence:
        if self._store is None:
            msg = "Bus persistence store is not initialized"
            raise RuntimeError(msg)
        return self._store

    def register(self, spec: EndpointSpec) -> None:
        if spec.name in self._endpoints_by_name:
            raise ValueError(f"Endpoint '{spec.name}' already registered")
        self._endpoints_by_name[spec.name] = spec

    def register_hook(
        self, stage: Literal["pre_publish", "pre_deliver"], spec: BusHookSpec
    ) -> None:
        if stage not in self._hooks:
            raise ValueError(f"unknown hook stage: {stage}")
        self._hooks[stage].append(spec)

    async def _run_hooks(
        self, stage: Literal["pre_publish", "pre_deliver"], envelope: Envelope
    ) -> Envelope | None:
        """Run hooks in registration order. Return the (possibly mutated)
        envelope, or None if any hook dropped it."""
        current = envelope
        for spec in self._hooks[stage]:
            result = await spec.hook.execute(stage, current, spec.params)
            if result is None:
                return None
            current = result
        return current

    def snapshot_for_agent(self, name: str) -> dict | None:
        """Return the current notification summary for an agent, or None.

        Only ClaudeCodeMCPEndpoint instances support snapshots; other endpoint
        types return None. Used by the /notify/<agent> SSE route to emit an
        immediate state event when a relay connects, so reconnecting agents
        with pending mail get woken without waiting for the next arrival.
        """
        ep_spec = self._endpoints_by_name.get(name)
        if ep_spec is None:
            return None
        ep = ep_spec.endpoint
        snapshot_fn = getattr(ep, "snapshot", None)
        if snapshot_fn is None:
            return None
        return snapshot_fn()

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
                await self.drain_for(spec.name)
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

    # BusHandle-facing surface — _ack / _nack implemented in Task 9
    async def _enqueue(self, envelope: Envelope, to: str | list[str] | None = None) -> None:
        # `from_` was already stamped by BusHandle.publish before we got here,
        # so hooks see authenticated provenance.
        hooked = await self._run_hooks("pre_publish", envelope)
        if hooked is None:
            return  # dropped before persist
        envelope = hooked

        # Determine recipient list. If `to` provided, override envelope.to.
        recipients: list[str]
        if to is None:
            recipients = [envelope.to]
        elif isinstance(to, str):
            recipients = [to]
        else:
            recipients = list(to)

        # Pre-validate ALL recipients before any side effect.
        # This makes fan-out atomic: either all recipients are accepted, or none.
        for recipient in recipients:
            if recipient not in self._endpoints_by_name:
                raise ValueError(f"publish to unregistered endpoint '{recipient}'")
            store = self._require_store()
            count = await store.count_pending(recipient)
            if count >= self.config.max_pending_per_endpoint:
                raise MailboxFull(f"mailbox '{recipient}' full ({count} pending)")

        # All checks passed. Now insert and dispatch.
        for i, recipient in enumerate(recipients):
            # First recipient reuses the original id; rest get fresh ids.
            new_env = envelope.model_copy(
                update={"id": envelope.id if i == 0 else uuid.uuid4().hex, "to": recipient}
            )
            store = self._require_store()
            await store.insert(new_env)
            await self._dispatch(new_env)

    async def _dispatch(self, envelope: Envelope) -> None:
        hooked = await self._run_hooks("pre_deliver", envelope)
        if hooked is None:
            # Pre_deliver dropped: dead-letter rather than silently leaving in pending.
            store = self._require_store()
            await store.mark_dead_letter(envelope.id, reason="dropped by pre_deliver hook")
            return
        envelope = hooked

        spec = self._endpoints_by_name.get(envelope.to)
        if spec is None:
            return  # shouldn't happen — caller already checked
        endpoint = spec.endpoint
        in_flight_until = datetime.now(UTC) + timedelta(
            seconds=self.config.redelivery_timeout_seconds
        )
        store = self._require_store()
        await store.mark_in_flight(envelope.id, in_flight_until)
        try:
            await endpoint.deliver(envelope)
        except Exception as exc:
            from agent_core.bus.protocol import EndpointUnavailable

            if isinstance(exc, EndpointUnavailable):
                # Temporary failure — return to pending; sweep will retry.
                await store.requeue(envelope.id)
                log.info(
                    "endpoint %s unavailable; envelope %s requeued: %s",
                    envelope.to,
                    envelope.id,
                    exc,
                )
            else:
                # Terminal failure — dead-letter.
                await store.mark_dead_letter(envelope.id, reason=str(exc))
                log.exception(
                    "endpoint %s deliver() raised; dead-lettering envelope %s",
                    envelope.to,
                    envelope.id,
                )

    async def drain_for(self, endpoint_name: str) -> None:
        """Drain persisted-but-pending envelopes addressed to this endpoint.

        Called after an endpoint comes online (start() returns, or a previously
        unavailable endpoint becomes available again).
        """
        store = self._require_store()
        pending = await store.list_pending(endpoint_name)
        for env in pending:
            await self._dispatch(env)

    async def run_ttl_sweep_once(self, *, now: datetime | None = None) -> int:
        """Mark expired-and-undelivered envelopes as 'expired'. Returns count swept."""
        if self._store is None:
            return 0
        now = now or datetime.now(UTC)
        expired = await self._store.find_expired(now=now)
        for env in expired:
            await self._store.expire(env.id)
            log.info("ttl swept envelope %s (to=%s)", env.id, env.to)
        return len(expired)

    async def run_redelivery_sweep_once(self, *, now: datetime | None = None) -> int:
        """Find in_flight envelopes whose timeout has lapsed; requeue or dead-letter."""
        if self._store is None:
            return 0
        now = now or datetime.now(UTC)
        stale = await self._store.find_in_flight_timeouts(now=now)
        moved = 0
        for env in stale:
            try:
                row = await self._store.row(env.id)
                if row is None:
                    continue
                if row["delivery_count"] >= self.config.max_delivery_attempts:
                    await self._store.mark_dead_letter(
                        env.id,
                        reason=f"exceeded {self.config.max_delivery_attempts} delivery attempts",
                    )
                else:
                    await self._store.requeue(env.id)
                    await self._dispatch(env)
            except Exception:
                log.exception("redelivery sweep error on envelope %s; skipping", env.id)
                continue
            moved += 1
        return moved

    async def _ack(self, envelope_id: str) -> None:
        # Idempotent: marking acked twice (or acking a missing id) is a no-op.
        store = self._require_store()
        await store.mark_acked(envelope_id)

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        store = self._require_store()
        if requeue:
            await store.requeue(envelope_id)
        else:
            await store.mark_dead_letter(envelope_id, reason="nack")

    def _endpoints(self) -> list[EndpointInfo]:
        return [
            EndpointInfo(name=spec.name, description=spec.description)
            for spec in self._endpoints_by_name.values()
        ]
