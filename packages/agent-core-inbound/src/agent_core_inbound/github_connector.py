"""GitHubConnector — TOML-policy classifier with mtime reload.

One connector instance per principal being. ``principal_being`` is
the only target_being for which the connector will return Allow;
calls with any other target_being deny without evaluating rules.
This enforces the "one connector per being" v1.a binding without
needing a multi-being constraint in the TOML rule shape.
"""
from __future__ import annotations

from pathlib import Path

from agent_core_inbound.github_allowance import (
    AllowanceConfig,
    AllowRule,
    load_allowance,
)
from agent_core_inbound.github_event import GitHubEvent
from agent_core_inbound.types import Allow, ConnectorEvent, Deny


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
            if rule.reviewer is not None:
                if not isinstance(event, GitHubPullRequestReviewRequestedEvent):
                    continue
                if event.requested_reviewer_login != rule.reviewer:
                    continue
            if rule.label_name is not None:
                if not isinstance(event, GitHubIssuesLabeledEvent):
                    continue
                if event.label_name != rule.label_name:
                    continue
            if rule.body_contains is not None:
                if not isinstance(event, GitHubIssueCommentEvent):
                    continue
                if rule.body_contains not in event.comment_body:
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
