"""GitHubConnector — TOML-policy classifier with mtime reload.

One connector instance per principal being. ``principal_being`` is
the only target_being for which the connector will return Allow;
calls with any other target_being deny without evaluating rules.
This enforces the "one connector per being" v1.a binding without
needing a multi-being constraint in the TOML rule shape.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core_inbound._path import MISSING, resolve_path
from agent_core_inbound.github_allowance import (
    AllowanceConfig,
    AllowRule,
    load_allowance,
)
from agent_core_inbound.github_event import GitHubEvent
from agent_core_inbound.types import Allow, ConnectorEvent, Deny

# Dotted paths extracted per event_type on top of the universal fields.
# Paths use the same resolve_path() resolver as the matching engine.
# Adding a new event type is a one-line edit here; no TOML or operator
# action required.
_PROJECTIONS: dict[str, list[str]] = {
    "pull_request": [
        "number",
        "pull_request.title",
        "pull_request.user.login",
        "pull_request.head.ref",
        "pull_request.base.ref",
        "pull_request.html_url",
        "requested_reviewer.login",
    ],
    "workflow_run": [
        "workflow_run.id",
        "workflow_run.name",
        "workflow_run.conclusion",
        "workflow_run.head_branch",
        "workflow_run.html_url",
        "workflow_run.event",
    ],
    "issues": [
        "issue.number",
        "issue.title",
        "issue.user.login",
        "label.name",
    ],
    "issue_comment": [
        "issue.number",
        "issue.title",
        "comment.user.login",
        "comment.body",
    ],
    "push": [
        "ref",
        "before",
        "after",
        "head_commit.id",
        "head_commit.message",
        "pusher.name",
    ],
    "release": [
        "release.tag_name",
        "release.name",
        "release.html_url",
        "release.prerelease",
    ],
    "status": [
        "state",
        "context",
        "description",
        "target_url",
        "sha",
    ],
}

# String fields whose values are truncated to N characters.
_TRUNCATIONS: dict[str, int] = {
    "comment.body": 200,
}

# Maps each event_type to the dotted path of its canonical URL in the raw payload.
# GitHub webhooks never place a URL at the top-level "html_url" key — it lives
# inside the primary object for each event type. This table lets _universal_fields()
# extract the right URL without inspecting the wrong location.
_URL_PATHS: dict[str, str] = {
    "pull_request": "pull_request.html_url",
    "workflow_run": "workflow_run.html_url",
    "issues": "issue.html_url",
    "issue_comment": "issue.html_url",
    "push": "compare",
    "release": "release.html_url",
    "status": "target_url",
}


def _universal_fields(event: GitHubEvent) -> dict[str, Any]:
    """Fields present in every Notification body regardless of event type."""
    result: dict[str, Any] = {
        "event_type": event.event_type,
        "action": event.action,
        "repo": event.repo_full_name,
        "landed_at": event.landed_at.isoformat(),
    }
    url_path = _URL_PATHS.get(event.event_type)
    if url_path is not None:
        url = resolve_path(event.raw, url_path)
        if url is not MISSING:
            result["html_url"] = url
    return result


class GitHubConnector:
    name = "github"

    def __init__(
        self,
        *,
        config_path: Path,
        principal_being: str = "wren",
    ) -> None:
        self._config_path = config_path
        self._principal_being = principal_being
        self._config: AllowanceConfig = AllowanceConfig(allow=[])
        self._loaded_mtime: float | None = None
        self._last_matched_rule_id: dict[tuple[str, str], str] = {}
        self._reload_if_needed()

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,
    ) -> Allow | Deny:
        if target_being != self._principal_being:
            return Deny()
        if not isinstance(event, GitHubEvent):
            return Deny()
        self._reload_if_needed()
        rule = self._first_matching_rule(event)
        if rule is None:
            return Deny()
        self._last_matched_rule_id[(event.event_id, target_being)] = rule.rule_id
        return Allow(tier=rule.tier, reason=rule.reason)

    def rule_id_for(self, *, event_id: str, target_being: str) -> str:
        return self._last_matched_rule_id.get((event_id, target_being), "unknown")

    def project(self, event: ConnectorEvent) -> dict[str, Any]:
        """Return a trimmed body dict for the Notification envelope.

        Starts from the universal fields (event_type, action, repo,
        landed_at, html_url), then overlays per-event-type dotted-path
        fields from _PROJECTIONS. Fields that resolve to MISSING are
        silently omitted; string fields listed in _TRUNCATIONS are
        capped at their max length.

        Falls back to dict(event.raw) for event types not in
        _PROJECTIONS (unknown / future GitHub event types).
        """
        if not isinstance(event, GitHubEvent):
            return dict(event.raw)
        body: dict[str, Any] = _universal_fields(event)
        paths = _PROJECTIONS.get(event.event_type)
        if paths is None:
            # Unknown event type: passthrough (safe default).
            return dict(event.raw)
        for path in paths:
            value = resolve_path(event.raw, path)
            if value is MISSING:
                continue
            max_len = _TRUNCATIONS.get(path)
            if max_len is not None and isinstance(value, str):
                value = value[:max_len]
            body[path] = value
        return body

    def _reload_if_needed(self) -> None:
        if not self._config_path.exists():
            return
        current_mtime = self._config_path.stat().st_mtime
        if self._loaded_mtime is not None and current_mtime <= self._loaded_mtime:
            return
        self._config = load_allowance(self._config_path)
        self._loaded_mtime = current_mtime

    def _first_matching_rule(self, event: GitHubEvent) -> AllowRule | None:
        event_key = _event_key(event)
        for rule in self._config.allow:
            if rule.event != event_key:
                continue
            if rule.repo is not None and rule.repo != event.repo_full_name:
                continue
            if rule.match:
                all_matched = True
                for path, expected in rule.match.items():
                    actual = resolve_path(event.raw, path)
                    if actual is MISSING or actual != expected:
                        all_matched = False
                        break
                if not all_matched:
                    continue
            return rule
        return None


def _event_key(event: GitHubEvent) -> str:
    """Compose the synthetic event key the rules match against.

    For events with an action sub-type (pull_request, issues, etc.):
    returns ``f"{event_type}_{action}"``. For action-less events
    (push, ping, create, delete, etc.): returns just ``event_type``.
    """
    if event.action:
        return f"{event.event_type}_{event.action}"
    return event.event_type
