"""GitHubConnector: first-match-wins rule eval + mtime reload."""
import time
from datetime import UTC, datetime, timezone
from pathlib import Path

from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.github_event import GitHubEvent
from agent_core_inbound.types import Allow, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _make_connector(rules_toml: str, tmp_path: Path) -> GitHubConnector:
    p = tmp_path / "rules.toml"
    p.write_text(rules_toml, encoding="utf-8")
    return GitHubConnector(config_path=p, principal_being="wren")


# ---------------------------------------------------------------------------
# v2 dotted-path matching (new in Task 6)
# ---------------------------------------------------------------------------


def test_matches_event_and_repo_only(tmp_path: Path) -> None:
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "pr_opened"
        event = "pull_request_opened"
        repo = "jeffrichley/foreman"
        tier = "yellow"
        reason = "PR opened"
        """,
        tmp_path,
    )
    event = GitHubEvent(
        event_id="e1",
        landed_at=datetime(2026, 6, 21, 17, 0, 0, tzinfo=timezone.utc),
        event_type="pull_request",
        action="opened",
        repo_full_name="jeffrichley/foreman",
        raw={"action": "opened", "repository": {"full_name": "jeffrichley/foreman"}},
    )
    verdict = conn.classify(event, target_being="wren")
    assert isinstance(verdict, Allow)
    assert verdict.tier == Tier.YELLOW


def test_match_dotted_path_succeeds(tmp_path: Path) -> None:
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """,
        tmp_path,
    )
    event = GitHubEvent(
        event_id="e1",
        landed_at=datetime(2026, 6, 21, 17, 0, 0, tzinfo=timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw={"workflow_run": {"conclusion": "failure"}, "action": "completed"},
    )
    verdict = conn.classify(event, target_being="wren")
    assert isinstance(verdict, Allow)


def test_match_dotted_path_value_mismatch_denies(tmp_path: Path) -> None:
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """,
        tmp_path,
    )
    event = GitHubEvent(
        event_id="e1",
        landed_at=datetime(2026, 6, 21, 17, 0, 0, tzinfo=timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw={"workflow_run": {"conclusion": "success"}, "action": "completed"},
    )
    verdict = conn.classify(event, target_being="wren")
    assert isinstance(verdict, Deny)


def test_match_missing_path_denies(tmp_path: Path) -> None:
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """,
        tmp_path,
    )
    event = GitHubEvent(
        event_id="e1",
        landed_at=datetime(2026, 6, 21, 17, 0, 0, tzinfo=timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw={"action": "completed"},  # no workflow_run key
    )
    verdict = conn.classify(event, target_being="wren")
    assert isinstance(verdict, Deny)


def test_first_match_wins_preserved(tmp_path: Path) -> None:
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "specific"
        event = "issues_labeled"
        repo = "jeffrichley/foreman"
        match = { "label.name" = "foreman:needs-help" }
        tier = "red"
        reason = "specific match"

        [[allow]]
        rule_id = "fallback"
        event = "issues_labeled"
        tier = "green"
        reason = "any labeled issue"
        """,
        tmp_path,
    )
    event = GitHubEvent(
        event_id="e1",
        landed_at=datetime(2026, 6, 21, 17, 0, 0, tzinfo=timezone.utc),
        event_type="issues",
        action="labeled",
        repo_full_name="jeffrichley/foreman",
        raw={"label": {"name": "foreman:needs-help"}},
    )
    verdict = conn.classify(event, target_being="wren")
    assert isinstance(verdict, Allow)
    assert verdict.reason == "specific match"  # first rule wins


# ---------------------------------------------------------------------------
# Migrated v1.a tests — shape converted to GitHubEvent + raw dict
# ---------------------------------------------------------------------------


def test_rule_id_for_returns_matched_rule_id(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "PR review requested on foreman"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubEvent(
        event_id="gh-6",
        landed_at=_stamp(),
        event_type="pull_request",
        action="review_requested",
        repo_full_name="jeffrichley/foreman",
        raw={"requested_reviewer": {"login": "wrenrichley"}},
    )
    conn.classify(event, "wren")
    assert (
        conn.rule_id_for(event_id="gh-6", target_being="wren")
        == "pr_review_requested_foreman"
    )


def test_mtime_reload_picks_up_new_rule(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "g.toml", "")
    conn = GitHubConnector(config_path=cfg)

    event = GitHubEvent(
        event_id="gh-7",
        landed_at=_stamp(),
        event_type="pull_request",
        action="review_requested",
        repo_full_name="jeffrichley/foreman",
        raw={"requested_reviewer": {"login": "wrenrichley"}},
    )
    assert isinstance(conn.classify(event, "wren"), Deny)

    # Sleep briefly to guarantee mtime jumps a whole second (Windows
    # filesystem resolution).
    time.sleep(1.1)

    _write(
        cfg,
        """
[[allow]]
rule_id = "added_after_first_load"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "new rule"
""",
    )
    verdict = conn.classify(event, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.reason == "new rule"


def test_unknown_target_being_does_not_match_allow(tmp_path: Path) -> None:
    # v1.a only routes to wren. A connector configured against Wren's
    # allowance file should deny if someone calls classify with
    # target_being other than "wren". The being-binding is not in the
    # TOML rule shape (yet) — the router level binding is one connector
    # per being. So a defensive deny here is the right shape.
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "x"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "x"
""",
    )
    conn = GitHubConnector(config_path=cfg, principal_being="wren")
    event = GitHubEvent(
        event_id="gh-8",
        landed_at=_stamp(),
        event_type="pull_request",
        action="review_requested",
        repo_full_name="jeffrichley/foreman",
        raw={},
    )
    assert isinstance(conn.classify(event, "pepper"), Deny)


def test_label_name_shortcut_skips_event_without_label_key(tmp_path: Path) -> None:
    # A rule constrained by label_name (shortcut for match["label.name"])
    # must NOT match an event whose raw payload has no "label" key. The
    # resolver returns MISSING, the match fails, the rule is skipped,
    # and the classifier falls through to Deny.
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "label_constraint"
event = "issue_comment"
label_name = "bug"
tier = "yellow"
reason = "labeled bug"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    # An issue_comment-shaped event with no "label" key in raw — should
    # be denied even though rule.event matches "issue_comment".
    event = GitHubEvent(
        event_id="gh-label-1",
        landed_at=_stamp(),
        event_type="issue_comment",
        action="",
        repo_full_name="jeffrichley/agent_core",
        raw={"comment": {"body": "x"}},
    )
    assert isinstance(conn.classify(event, "wren"), Deny)


def test_nonexistent_config_path_denies_everything(tmp_path: Path) -> None:
    # If the config file doesn't exist at construction, the connector
    # uses the empty default config — every classify returns Deny.
    # Subsequent reloads also short-circuit until the file appears.
    missing = tmp_path / "does-not-exist.toml"
    assert not missing.exists()
    conn = GitHubConnector(config_path=missing)
    event = GitHubEvent(
        event_id="gh-missing-1",
        landed_at=_stamp(),
        event_type="pull_request",
        action="review_requested",
        repo_full_name="jeffrichley/foreman",
        raw={},
    )
    assert isinstance(conn.classify(event, "wren"), Deny)


# ---------------------------------------------------------------------------
# _event_key composition (retained from Task 5)
# ---------------------------------------------------------------------------


def test_event_key_composes_event_type_plus_action() -> None:
    from agent_core_inbound.github_connector import _event_key

    event = GitHubEvent(
        event_id="abc",
        landed_at=datetime(2026, 6, 21, 17, 2, 0, tzinfo=timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw={},
    )
    assert _event_key(event) == "workflow_run_completed"


def test_event_key_drops_underscore_for_actionless_events() -> None:
    from agent_core_inbound.github_connector import _event_key

    event = GitHubEvent(
        event_id="ping",
        landed_at=datetime(2026, 6, 21, 17, 2, 0, tzinfo=timezone.utc),
        event_type="push",
        action="",
        repo_full_name="jeffrichley/foreman",
        raw={},
    )
    assert _event_key(event) == "push"
