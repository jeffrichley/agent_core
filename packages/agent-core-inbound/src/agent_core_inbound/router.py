"""Router — receive external events, classify via connector, deliver via bus.

Pure plumbing per the design: it does NOT classify (the connector does),
it does NOT hold per-source config (the connector does), it does NOT
decide urgency (the connector does). It owns: dispatch to the connector,
de-dupe across redeliveries, rate-limit, publish to the bus, write to
the audit log.
"""
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.protocol import Connector
from agent_core_inbound.types import Allow, ConnectorEvent, Deny

# The router's call into the bus. Real wiring uses the agent-core
# BusHandle; tests inject a fake. Keeping this as a callable rather
# than an interface avoids dragging the BusHandle dependency into the
# router substrate's test surface.
BusPublish = Callable[..., None]


def _default_clock() -> datetime:
    return datetime.now(UTC)


# Cap on the LRU of recently-seen (connector, event_id, target_being)
# triples. 4096 is generous enough that GitHub's ~5 retry delivery
# window will never roll out under realistic burst, but small enough
# that a stuck-source scenario can't grow memory without bound.
_DEDUPE_CAPACITY: int = 4096


class _TokenBucket:
    """Sliding-window count: N events allowed per ``window_seconds``.

    Implemented as a deque of event timestamps; on each consume() we
    drop entries older than the window and check the remaining count.
    Simple and correct for the ~10s-of-events-per-minute scale we
    actually care about.
    """

    def __init__(self, *, capacity: int, window_seconds: float) -> None:
        self._capacity = capacity
        self._window = window_seconds
        self._stamps: deque[datetime] = deque()

    def consume(self, now: datetime) -> bool:
        cutoff = now.timestamp() - self._window
        while self._stamps and self._stamps[0].timestamp() < cutoff:
            self._stamps.popleft()
        if len(self._stamps) >= self._capacity:
            return False
        self._stamps.append(now)
        return True


class Router:
    """De-dupe + rate-limit + classify + deliver + log.

    ``connectors`` maps connector name → Connector instance. The router
    dispatches to ``connectors[connector_name]`` on each receive(). A
    missing key raises KeyError — this surfaces wiring bugs early
    rather than silently dropping events.

    De-dupe key is ``(connector_name, event_id, target_being)`` and
    evicts on LRU when the cache fills. A redelivered event hits the
    cache and is silently dropped — no audit line, because the prior
    delivery's audit line is the canonical record.

    Rate limiting is per-(connector_name, target_being): pass
    ``rate_limits={(source, target): (capacity, window_seconds)}`` at
    construction time to cap how many Allow-classified events per pair
    can reach the bus inside any sliding window. Over-quota events are
    written to the audit log as denials (so operators see the throttle
    firing) but do NOT populate the de-dupe cache — a future window
    re-opening allows the same event_id through.

    Note: not thread-safe; intended for single-event-loop or single-threaded callers
    (asyncio cooperative concurrency is fine; multi-threaded callers must serialize).
    """

    def __init__(
        self,
        *,
        connectors: dict[str, Connector],
        bus_publish: BusPublish,
        audit: AuditLog,
        clock: Callable[[], datetime] = _default_clock,
        dedupe_capacity: int = _DEDUPE_CAPACITY,
        rate_limits: dict[tuple[str, str], tuple[int, float]] | None = None,
    ) -> None:
        self._connectors = connectors
        self._bus_publish = bus_publish
        self._audit = audit
        self._clock = clock
        self._seen: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._dedupe_capacity = dedupe_capacity
        self._buckets: dict[tuple[str, str], _TokenBucket] = {}
        for key, (capacity, window) in (rate_limits or {}).items():
            self._buckets[key] = _TokenBucket(
                capacity=capacity, window_seconds=window,
            )

    def receive(
        self,
        *,
        connector_name: str,
        target_being: str,
        event: ConnectorEvent,
    ) -> None:
        connector = self._connectors.get(connector_name)
        if connector is None:
            raise KeyError(f"unknown connector {connector_name!r}")

        # De-dupe: skip if we've delivered this (connector, event_id,
        # target_being) recently. Skipped events do NOT write to the
        # audit log — they are structurally identical to the prior
        # delivery, which already has its own audit line.
        dedupe_key = (connector_name, event.event_id, target_being)
        if dedupe_key in self._seen:
            self._seen.move_to_end(dedupe_key)
            return

        verdict = connector.classify(event, target_being)
        if isinstance(verdict, Deny):
            self._audit.record_deny(
                connector_name=connector.name,
                target_being=target_being,
            )
            return

        assert isinstance(verdict, Allow)

        # Rate limit: per-(source, target) token bucket. Over-quota
        # events count as denials so operators see the throttle fired.
        bucket = self._buckets.get((connector_name, target_being))
        if bucket is not None and not bucket.consume(self._clock()):
            self._audit.record_deny(
                connector_name=connector.name,
                target_being=target_being,
            )
            return

        rule_id = self._extract_rule_id(
            connector=connector,
            event_id=event.event_id,
            target_being=target_being,
        )
        self._audit.record_allow(
            connector_name=connector.name,
            target_being=target_being,
            verdict=verdict,
            rule_id=rule_id,
        )

        # Publish Notification envelope. Body is a per-event-type projected
        # dict assembled by _project_body() (trimmed for GitHub events;
        # raw passthrough for connectors without a project() method).
        body = self._project_body(
            connector=connector,
            event=event,
            rule_id=rule_id,
            verdict=verdict,
        )
        self._bus_publish(
            to=target_being,
            kind="Notification",
            payload={
                "kind": "Notification",
                "source": connector.name,
                "reason": verdict.reason,
                "landed_at": event.landed_at.isoformat(),
                "body": body,
            },
            urgency=verdict.tier.value,
        )

        # Record successful delivery in the de-dupe cache. Evict the
        # oldest entry when the cache is full.
        self._seen[dedupe_key] = None
        if len(self._seen) > self._dedupe_capacity:
            self._seen.popitem(last=False)

    @staticmethod
    def _project_body(
        *,
        connector: Connector,
        event: ConnectorEvent,
        rule_id: str,
        verdict: Allow,
    ) -> dict[str, Any]:
        """Build the Notification body dict.

        Delegates to connector.project(event) — a required member of the
        Connector protocol, so every connector supplies one (the base
        Protocol's concrete default returns dict(event.raw)). Merges
        router-scope fields rule_id, tier, reason so consumers need not
        cross-reference the audit log.
        """
        projected: dict[str, Any] = connector.project(event)
        return {
            "rule_id": rule_id,
            "reason": verdict.reason,
            "tier": verdict.tier.value,
            **projected,
        }

    @staticmethod
    def _extract_rule_id(
        *,
        connector: Connector,
        event_id: str,
        target_being: str,
    ) -> str:
        # Optional helper hook: connectors that want rich audit logs can
        # expose rule_id_for(); the router falls back to "unknown" when
        # the connector doesn't provide one. This keeps Connector's
        # required surface minimal (just name + classify).
        rule_id_for = getattr(connector, "rule_id_for", None)
        if callable(rule_id_for):
            try:
                return rule_id_for(event_id=event_id, target_being=target_being)
            except Exception:
                return "unknown"
        return "unknown"
