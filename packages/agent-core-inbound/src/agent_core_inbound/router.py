"""Router — receive external events, classify via connector, deliver via bus.

Pure plumbing per the design: it does NOT classify (the connector does),
it does NOT hold per-source config (the connector does), it does NOT
decide urgency (the connector does). It owns: dispatch to the connector,
de-dupe across redeliveries, rate-limit, publish to the bus, write to
the audit log.

This task implements receive() + classify routing + bus delivery + audit.
De-dupe and rate-limit land in tasks 7 and 8.
"""
from collections.abc import Callable
from datetime import UTC, datetime

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


class Router:
    """De-dupe + rate-limit + classify + deliver + log.

    ``connectors`` maps connector name → Connector instance. The router
    dispatches to ``connectors[connector_name]`` on each receive(). A
    missing key raises KeyError — this surfaces wiring bugs early
    rather than silently dropping events.
    """

    def __init__(
        self,
        *,
        connectors: dict[str, Connector],
        bus_publish: BusPublish,
        audit: AuditLog,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._connectors = connectors
        self._bus_publish = bus_publish
        self._audit = audit
        self._clock = clock

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

        verdict = connector.classify(event, target_being)
        if isinstance(verdict, Deny):
            self._audit.record_deny(
                connector_name=connector.name,
                target_being=target_being,
            )
            return

        assert isinstance(verdict, Allow)
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

        # Publish Notification envelope. Body carries the connector-specific
        # raw payload preserved verbatim.
        self._bus_publish(
            to=target_being,
            kind="Notification",
            payload={
                "kind": "Notification",
                "source": connector.name,
                "reason": verdict.reason,
                "landed_at": event.landed_at.isoformat(),
                "body": event.raw,
            },
            urgency=verdict.tier.value,
        )

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
