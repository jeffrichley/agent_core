"""Connector Protocol — per-source policy module contract.

Each connector parses one external source's events (GitHub webhooks,
Gmail messages, calendar events) and applies a TOML-driven policy
to decide which events reach which being. The router calls
classify(event, target_being) and acts on the returned Allow|Deny.
"""
from typing import Protocol, runtime_checkable

from agent_core_inbound.types import Allow, ConnectorEvent, Deny


@runtime_checkable
class Connector(Protocol):
    """Per-source policy module.

    ``name`` is the source identifier ("github", "gmail", "calendar").
    Used by the router for audit-log lines and as the Notification
    envelope's ``payload.source``.

    ``classify`` decides whether ``event`` should be delivered to
    ``target_being``. Connectors are deny-by-default: any event the
    connector's policy rules do not explicitly match returns Deny.
    """

    name: str

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,
    ) -> Allow | Deny: ...
