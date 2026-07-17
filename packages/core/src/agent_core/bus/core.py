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
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agent_core.bus.envelope import EndpointInfo, Envelope
from agent_core.bus.handle import BusHandle
from agent_core.bus.persistence import Persistence
from agent_core.bus.protocol import BusHook, Endpoint, SlowDeliverWarning
from agent_core.clock import Clock, SystemClock

if TYPE_CHECKING:
    from agent_core.bus.supervisor import EndpointSupervisor

log = logging.getLogger(__name__)


_VALID_JITTER = {"full", "equal", "none"}


@dataclass
class SupervisorConfig:
    """Tunable knobs for the supervision layer (T3 EndpointSupervisor reads this)."""

    restart_backoff_base_seconds: int = 1
    restart_backoff_factor: int = 2
    restart_backoff_cap_seconds: int = 60
    restart_jitter: str = "full"
    restarts_before_quarantine: int = 5
    probe_interval_seconds: int = 300
    delivery_backoff_base_seconds: int = 2
    delivery_backoff_factor: int = 2
    delivery_backoff_cap_seconds: int = 60
    deliver_failures_before_breaker: int = 5

    def __post_init__(self) -> None:
        for name in (
            "restart_backoff_base_seconds",
            "restart_backoff_cap_seconds",
            "probe_interval_seconds",
            "delivery_backoff_base_seconds",
            "delivery_backoff_cap_seconds",
        ):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be > 0, got {value!r}")
        for name in ("restart_backoff_factor", "delivery_backoff_factor"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"{name} must be >= 1, got {value!r}")
        if self.restarts_before_quarantine < 1:
            raise ValueError(
                f"restarts_before_quarantine must be >= 1, got {self.restarts_before_quarantine!r}"
            )
        if self.deliver_failures_before_breaker < 1:
            raise ValueError(
                f"deliver_failures_before_breaker must be >= 1, "
                f"got {self.deliver_failures_before_breaker!r}"
            )
        if self.restart_jitter not in _VALID_JITTER:
            raise ValueError(
                f"restart_jitter must be one of {sorted(_VALID_JITTER)!r}, "
                f"got {self.restart_jitter!r}"
            )


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
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    # Watchdog: warn when deliver() takes longer than this many seconds.
    # Non-positive value disables the watchdog entirely.
    slow_deliver_warn_seconds: float = 5.0
    # Liveness watchdog: OS thread fires os._exit if the event loop stops
    # bumping the heartbeat for this many seconds. Non-positive disables.
    watchdog_timeout_seconds: int = 90
    # Backup: directory where VACUUM INTO snapshots are written. None disables
    # backup entirely; must point to a different volume than the stores in prod.
    backup_dir: Path | None = None
    # Backup: cadence of the hourly snapshot loop (default 3600 = 1 hour).
    backup_interval_seconds: int = 3600


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


def _compute_delivery_backoff(attempt: int, config: SupervisorConfig) -> float:
    """Full-jitter exponential backoff for message delivery retries.

    attempt: delivery_count after the failed attempt (1-indexed).
    Returns: seconds to wait before the next delivery attempt.
    """
    cap = config.delivery_backoff_cap_seconds
    raw = config.delivery_backoff_base_seconds * (config.delivery_backoff_factor ** (attempt - 1))
    return random.uniform(0, min(raw, cap))


class Bus:
    """In-process bus router."""

    def __init__(self, config: BusConfig, *, clock: Clock | None = None):
        self.config = config
        self._clock: Clock = clock or SystemClock()
        self._endpoints_by_name: dict[str, EndpointSpec] = {}
        self._hooks: dict[str, list[BusHookSpec]] = {
            "pre_publish": [],
            "pre_deliver": [],
        }
        self._store: Persistence | None = None
        self._started = False
        self._handles: dict[str, BusHandle] = {}
        self._supervisor: EndpointSupervisor | None = None

    def _require_store(self) -> Persistence:
        if self._store is None:
            msg = "Bus persistence store is not initialized"
            raise RuntimeError(msg)
        return self._store

    def _make_task_failure_hook(self, endpoint_name: str) -> Callable[[BaseException], None]:
        """Factory: produces a per-endpoint logging hook that also feeds the supervisor."""

        def _hook(exc: BaseException) -> None:
            log.error(
                "endpoint %r: background task raised (endpoint may need restart)",
                endpoint_name,
                exc_info=exc,
            )
            if self._supervisor is not None:
                self._supervisor.record_failure(endpoint_name, str(exc))

        return _hook

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
        from agent_core.bus.supervisor import EndpointSupervisor

        self._store = Persistence(self.config.storage_path, clock=self._clock)
        await self._store.connect()
        sup = self.config.supervisor
        log.info(
            "supervisor config: restart_backoff=%ds×%d cap=%ds jitter=%s quarantine_after=%d "
            "probe=%ds delivery_backoff=%ds×%d cap=%ds breaker_after=%d",
            sup.restart_backoff_base_seconds,
            sup.restart_backoff_factor,
            sup.restart_backoff_cap_seconds,
            sup.restart_jitter,
            sup.restarts_before_quarantine,
            sup.probe_interval_seconds,
            sup.delivery_backoff_base_seconds,
            sup.delivery_backoff_factor,
            sup.delivery_backoff_cap_seconds,
            sup.deliver_failures_before_breaker,
        )
        self._supervisor = EndpointSupervisor(
            config=self.config.supervisor,
            clock=self._clock,
            restart_fn=self._restart_endpoint,
            on_transition=self._on_supervisor_transition,
        )
        started_count = 0
        for spec in self._endpoints_by_name.values():
            self._supervisor.register(spec.name)
            handle = BusHandle(
                self,
                spec.name,
                on_task_failure=self._make_task_failure_hook(spec.name),
            )
            self._handles[spec.name] = handle
            try:
                await spec.endpoint.start(handle)
                await self.drain_for(spec.name)
                started_count += 1
            except Exception as exc:
                log.error("endpoint %r failed to start; quarantining: %s", spec.name, exc)
                await self._supervisor.quarantine(spec.name, str(exc))

        if started_count == 0 and self._endpoints_by_name:
            log.critical(
                "all %d endpoint(s) failed to start; bus is running degraded-empty",
                len(self._endpoints_by_name),
            )
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
            handle = self._handles.get(spec.name)
            if handle is not None:
                await handle._drain_tasks()
        if self._store is not None:
            await self._store.close()
        self._started = False
        self._supervisor = None

    # ------------------------------------------------------------------
    # Supervisor integration
    # ------------------------------------------------------------------

    async def _restart_endpoint(self, name: str) -> None:
        """Restart callable injected into EndpointSupervisor.

        Called by the supervisor's tick loop when a restart or probe is due.
        Raises on failure so the supervisor can record the attempt.
        """
        spec = self._endpoints_by_name[name]
        handle = self._handles.get(name)
        # Invariant: handle is always created before the endpoint's start() call
        # in Bus.start(), so it is always present for registered endpoints.
        if handle is None:
            raise RuntimeError(f"No handle for endpoint '{name}' — bus may not be started")
        try:
            await spec.endpoint.stop()
        except Exception:
            log.exception("error stopping endpoint %s during supervisor restart", name)
        await handle._drain_tasks()
        await spec.endpoint.start(handle)  # raises on failure → supervisor catches
        await self.drain_for(name)

    async def _on_supervisor_transition(
        self, name: str, transition: str, last_error: str | None
    ) -> None:
        """Transition callback injected into EndpointSupervisor.

        Persists quarantine/recovery state to the supervisor_state table so
        ``bus status`` can surface it. Fires on quarantine-entry and recovery.
        """
        if transition == "quarantined":
            log.warning("endpoint %r quarantined; last error: %s", name, last_error or "(none)")
            if self._store is not None:
                await self._store.upsert_supervisor_state(name, last_error)
        elif transition == "recovered":
            log.info("endpoint %r recovered", name)
            if self._store is not None:
                await self._store.clear_supervisor_state(name)

    async def run_supervisor_tick_once(self, *, now: datetime | None = None) -> None:
        """Drive one supervisor tick (due restarts and probes).

        Called from the CLI redelivery loop alongside the envelope redelivery
        sweep. No-op if the supervisor has not been created yet (bus not started).
        """
        if self._supervisor is None:
            return
        now = now or self._clock.now()
        await self._supervisor.tick(now)

    # ------------------------------------------------------------------
    # BusHandle-facing surface — _ack / _nack implemented in Task 9
    # ------------------------------------------------------------------

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
        t0 = time.monotonic()
        try:
            await endpoint.deliver(envelope)
        except Exception as exc:
            from agent_core.bus.protocol import EndpointUnavailable

            if isinstance(exc, EndpointUnavailable):
                # Transient failure — backoff-requeue or dead-letter if exhausted.
                row = await store.row(envelope.id)
                if row is None:
                    log.warning(
                        "envelope %s not found after mark_in_flight; skipping requeue", envelope.id
                    )
                    return
                attempt = row["delivery_count"]  # already incremented by mark_in_flight
                if attempt >= self.config.max_delivery_attempts:
                    await store.mark_dead_letter(
                        envelope.id,
                        reason=f"exceeded {self.config.max_delivery_attempts} delivery attempts (transient)",
                    )
                    log.info(
                        "endpoint %s transient failure; exceeded %d attempts; dead-lettering %s",
                        envelope.to,
                        self.config.max_delivery_attempts,
                        envelope.id,
                    )
                else:
                    backoff_secs = _compute_delivery_backoff(attempt, self.config.supervisor)
                    next_attempt_at = self._clock.now() + timedelta(seconds=backoff_secs)
                    await store.requeue_with_backoff(envelope.id, next_attempt_at)
                    log.info(
                        "endpoint %s unavailable (attempt %d); envelope %s requeued until %s: %s",
                        envelope.to,
                        attempt,
                        envelope.id,
                        next_attempt_at.isoformat(),
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
        finally:
            elapsed = time.monotonic() - t0
            warn_s = self.config.slow_deliver_warn_seconds
            if warn_s > 0 and elapsed >= warn_s:
                warning = SlowDeliverWarning(
                    endpoint=envelope.to,
                    envelope_id=envelope.id,
                    elapsed_seconds=elapsed,
                )
                log.warning("%r threshold=%.1fs", warning, warn_s)

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
        """Find in_flight envelopes whose timeout has lapsed; requeue with backoff or dead-letter.

        Part 1: stale in-flight → requeue_with_backoff (not immediate re-dispatch).
        Part 2: pending envelopes whose backoff has elapsed → dispatch.
        """
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
                    backoff_secs = _compute_delivery_backoff(
                        row["delivery_count"], self.config.supervisor
                    )
                    next_attempt_at = now + timedelta(seconds=backoff_secs)
                    await self._store.requeue_with_backoff(env.id, next_attempt_at)
            except Exception:
                log.exception("redelivery sweep error on envelope %s; skipping", env.id)
                continue
            moved += 1

        # Part 2: dispatch pending envelopes whose backoff has elapsed.
        for endpoint_name in self._endpoints_by_name:
            try:
                due = await self._store.list_pending(endpoint_name, now=now)
                for env in due:
                    await self._dispatch(env)
            except Exception:
                log.exception(
                    "delivery backoff sweep error for endpoint %s; skipping", endpoint_name
                )

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
