"""Verdict + tier + event base types for the inbound-notifications router.

Connectors return an Allow{tier, reason} or Deny verdict. Router uses
these to decide what envelope to publish (or skip) and what to write
to the audit log.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Tier(StrEnum):
    """Urgency tier emitted at source by the connector.

    Set in the connector's matched policy rule, never inferred by the
    router. Maps 1:1 to the bus envelope's urgency field downstream.
    """

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class Allow(BaseModel):
    """Connector verdict: deliver this event at this urgency.

    Reason is the audit-log justification string the connector
    supplied. Required + non-empty so audit lines are never silent.
    """

    tier: Tier
    reason: str = Field(min_length=1)

    model_config = ConfigDict(frozen=True)


class Deny(BaseModel):
    """Connector verdict: drop this event.

    Intentionally empty — Deny carries no reason and no body. The
    audit log records ``verdict=deny`` with no event payload so a
    denied event leaves no privacy-sensitive traces.
    """

    model_config = ConfigDict(frozen=True)


class ConnectorEvent(BaseModel):
    """Base shape every connector-specific event extends.

    ``event_id`` is the stable string the router uses for de-dupe;
    connectors must derive it deterministically from the source
    (GitHub delivery ID, Gmail Message-ID, ICS UID).
    ``landed_at`` is when the source event happened.
    ``raw`` is the connector-specific payload preserved verbatim for
    the downstream Notification envelope body.
    """

    event_id: str = Field(min_length=1)
    landed_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")
