"""Generic GitHub webhook event shape for v2.

v2 collapses the typed-class-per-event hierarchy from v1.a to a single
generic event. The connector's matching engine walks `raw` via dotted
paths — see ``github_connector.resolve_path()`` and the
schema-flexible spec
(``docs/superpowers/specs/2026-06-21-inbound-v2-schema-flexible-events-design.md``).
"""
from __future__ import annotations

from pydantic import ConfigDict, Field

from agent_core_inbound.types import ConnectorEvent


class GitHubEvent(ConnectorEvent):
    """Every parsed GitHub webhook event.

    ``event_type`` is the X-GitHub-Event header value.
    ``action`` is the payload's ``action`` field, or "" for action-less
    events (push, ping, create, delete, etc.).
    ``repo_full_name`` is hoisted to a top-level field for fast
    repo-filter matching; equivalent to ``raw["repository"]["full_name"]``.
    ``raw`` is the full webhook payload as a dict, preserved verbatim
    so connector rules can match on any field via dotted paths
    (inherited from ``ConnectorEvent``).
    """

    event_type: str = Field(min_length=1)
    action: str = ""
    repo_full_name: str = ""

    model_config = ConfigDict(extra="allow")
