"""EndpointSupervisor — per-endpoint circuit-breaker state machine (issue #272).

Pure logic; no bus wiring. Depends only on SupervisorConfig (from core.py),
Clock (from agent_core.clock), and an injected async restart callable. Fully
unit-testable without real sleeps.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from agent_core.bus.core import SupervisorConfig
from agent_core.clock import Clock


@dataclass
class EndpointState:
    """Runtime health record for one endpoint."""

    name: str
    status: Literal["active", "restarting", "quarantined"] = "active"
    breaker: Literal["closed", "open", "half_open"] = "closed"
    consecutive_failures: int = 0
    last_error: str | None = None
    next_probe_at: datetime | None = None


def _compute_restart_backoff(attempt: int, config: SupervisorConfig) -> float:
    """Exponential backoff with optional full jitter for restart scheduling.

    Args:
        attempt: 1-indexed restart attempt number (1 = first restart after failure).
        config: supervisor config with backoff knobs.

    Returns:
        Seconds to wait before the next restart attempt.
    """
    cap = config.restart_backoff_cap_seconds
    raw = config.restart_backoff_base_seconds * (config.restart_backoff_factor ** (attempt - 1))
    computed = min(raw, cap)
    if config.restart_jitter == "none":
        return computed
    # restart_jitter == "full": uniform jitter within [0, computed]
    return random.uniform(0, computed)


class EndpointSupervisor:
    """Central registry and state-machine driver for endpoint circuit breakers.

    Injected dependencies (no real I/O):
    - ``config``: ``SupervisorConfig`` with backoff / quarantine knobs.
    - ``clock``: ``Clock`` implementation (``FakeClock`` in tests).
    - ``restart_fn``: async callable ``(name: str) -> None`` that restarts the
      named endpoint; raises on failure.
    - ``on_transition``: optional async callable ``(name, transition, last_error) -> None``
      fired on quarantine-entry and recovery boundary crossings.

    Call ``tick(now)`` from the bus event loop on each iteration to drive due
    restart attempts and quarantine probes.
    """

    def __init__(
        self,
        config: SupervisorConfig,
        clock: Clock,
        restart_fn: Callable[[str], Awaitable[None]],
        on_transition: Callable[[str, str, str | None], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._restart_fn = restart_fn
        self._on_transition = on_transition
        self._states: dict[str, EndpointState] = {}

    # ------------------------------------------------------------------
    # Public registration / query surface
    # ------------------------------------------------------------------

    def register(self, name: str) -> None:
        """Create an active/closed state record for ``name``.

        Raises:
            ValueError: if ``name`` is already registered.
        """
        if name in self._states:
            raise ValueError(f"Endpoint '{name}' already registered")
        self._states[name] = EndpointState(name=name)

    def state(self, name: str) -> EndpointState | None:
        """Return the current state for ``name``, or ``None`` if not registered."""
        return self._states.get(name)

    def all_states(self) -> list[EndpointState]:
        """Return all registered endpoint states."""
        return list(self._states.values())

    # ------------------------------------------------------------------
    # Synchronous state-mutation methods (no I/O)
    # ------------------------------------------------------------------

    def record_failure(self, name: str, error: str = "") -> None:
        """Record a runtime failure for ``name``.

        If the endpoint is active/closed, transitions to ``restarting`` and
        schedules the first restart at the appropriate backoff offset from
        ``clock.now()``.

        If the endpoint is already ``restarting`` or ``quarantined``, only
        ``last_error`` is updated — the supervisor is already handling the
        restart sequence; no reschedule.
        """
        s = self._states[name]
        if s.status == "active":
            s.status = "restarting"
            s.last_error = error
            # attempt=1 for first restart (consecutive_failures is still 0 here)
            delay = _compute_restart_backoff(s.consecutive_failures + 1, self._config)
            s.next_probe_at = self._clock.now() + timedelta(seconds=delay)
        else:
            # Already restarting or quarantined — just update the error record.
            s.last_error = error

    def record_success(self, name: str) -> None:
        """Record a successful operation for ``name``, resetting all failure state."""
        s = self._states[name]
        s.consecutive_failures = 0
        s.last_error = None
        s.status = "active"
        s.breaker = "closed"
        s.next_probe_at = None

    async def quarantine(self, name: str, error: str = "") -> None:
        """Directly quarantine ``name``, bypassing the restarting phase.

        Used for boot-time failures where the endpoint is already known-bad
        and we want to enter the probe cycle immediately. Fires ``on_transition``
        with ``("quarantined", error)`` if set.
        """
        s = self._states[name]
        s.status = "quarantined"
        s.breaker = "open"
        s.last_error = error
        s.next_probe_at = self._clock.now() + timedelta(
            seconds=self._config.probe_interval_seconds
        )
        if self._on_transition is not None:
            await self._on_transition(name, "quarantined", error)

    # ------------------------------------------------------------------
    # Async tick — the bus loop calls this on each iteration
    # ------------------------------------------------------------------

    async def tick(self, now: datetime) -> None:
        """Drive due restart attempts and quarantine probes.

        Iterates over a snapshot of registered states so that state mutations
        during iteration are safe.
        """
        for s in list(self._states.values()):
            if s.next_probe_at is None or now < s.next_probe_at:
                continue
            if s.status == "restarting":
                await self._attempt_restart(s, now)
            elif s.status == "quarantined":
                await self._attempt_probe(s, now)

    # ------------------------------------------------------------------
    # Private async helpers — one call per due endpoint per tick
    # ------------------------------------------------------------------

    async def _attempt_restart(self, state: EndpointState, now: datetime) -> None:
        """Attempt to restart a restarting endpoint.

        On success: transition to active/closed, reset all counters, fire
        ``on_transition("recovered", None)``.
        On failure: increment ``consecutive_failures``; quarantine if threshold
        reached (fires ``on_transition("quarantined", last_error)``), else
        schedule next retry at the next backoff interval.
        """
        try:
            await self._restart_fn(state.name)
        except Exception as exc:
            state.consecutive_failures += 1
            state.last_error = str(exc)
            if state.consecutive_failures >= self._config.restarts_before_quarantine:
                state.status = "quarantined"
                state.breaker = "open"
                state.next_probe_at = now + timedelta(
                    seconds=self._config.probe_interval_seconds
                )
                if self._on_transition is not None:
                    await self._on_transition(state.name, "quarantined", state.last_error)
            else:
                delay = _compute_restart_backoff(
                    state.consecutive_failures + 1, self._config
                )
                state.next_probe_at = now + timedelta(seconds=delay)
        else:
            state.status = "active"
            state.breaker = "closed"
            state.consecutive_failures = 0
            state.last_error = None
            state.next_probe_at = None
            if self._on_transition is not None:
                await self._on_transition(state.name, "recovered", None)

    async def _attempt_probe(self, state: EndpointState, now: datetime) -> None:
        """Attempt a probe restart for a quarantined endpoint (HALF_OPEN phase).

        Sets ``breaker="half_open"`` before the call (observable by external
        inspection). On success: transition to active/closed, fire
        ``on_transition("recovered", None)``. On failure: return to
        open/quarantined with a fresh probe timer; ``consecutive_failures`` is
        NOT incremented (already at threshold). No ``on_transition`` on failure
        (status stays quarantined — no boundary crossing).
        """
        state.breaker = "half_open"
        try:
            await self._restart_fn(state.name)
        except Exception as exc:
            state.last_error = str(exc)
            state.breaker = "open"
            state.status = "quarantined"
            state.next_probe_at = now + timedelta(
                seconds=self._config.probe_interval_seconds
            )
        else:
            state.status = "active"
            state.breaker = "closed"
            state.consecutive_failures = 0
            state.last_error = None
            state.next_probe_at = None
            if self._on_transition is not None:
                await self._on_transition(state.name, "recovered", None)
