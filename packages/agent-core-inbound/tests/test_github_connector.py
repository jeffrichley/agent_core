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


# ---------------------------------------------------------------------------
# project() — body projection per event type
# ---------------------------------------------------------------------------


def _make_connector_empty(tmp_path: Path) -> GitHubConnector:
    """Return a GitHubConnector with an empty (no-rules) config."""
    p = tmp_path / "empty.toml"
    p.write_text("", encoding="utf-8")
    return GitHubConnector(config_path=p, principal_being="wren")


def test_project_pull_request_returns_universal_plus_pr_fields(tmp_path: Path) -> None:
    raw = {
        "action": "review_requested",
        "number": 42,
        "repository": {"full_name": "jeffrichley/foreman"},
        "pull_request": {
            "title": "Add feature",
            "user": {"login": "alice"},
            "head": {"ref": "feature/foo"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/jeffrichley/foreman/pull/42",
        },
        "requested_reviewer": {"login": "wrenrichley"},
        "installation": {"id": 12345, "account": {}},  # big nested — must be absent
    }
    event = GitHubEvent(
        event_id="e1",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="pull_request",
        action="review_requested",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    # Universal fields
    assert body["event_type"] == "pull_request"
    assert body["action"] == "review_requested"
    assert body["repo"] == "jeffrichley/foreman"
    assert "landed_at" in body
    assert body["html_url"] == "https://github.com/jeffrichley/foreman/pull/42"

    # Per-event-type fields (dotted-path keys)
    assert body["number"] == 42
    assert body["pull_request.title"] == "Add feature"
    assert body["pull_request.user.login"] == "alice"
    assert body["pull_request.head.ref"] == "feature/foo"
    assert body["pull_request.base.ref"] == "main"
    assert body["requested_reviewer.login"] == "wrenrichley"
    assert body["pull_request.html_url"] == "https://github.com/jeffrichley/foreman/pull/42"

    # Large raw-only fields must NOT appear
    assert "installation" not in body
    assert "pull_request" not in body  # the full sub-dict key must not appear


def test_project_workflow_run_returns_universal_plus_workflow_fields(tmp_path: Path) -> None:
    raw = {
        "action": "completed",
        "repository": {"full_name": "jeffrichley/foreman"},
        "workflow_run": {
            "id": 9876543,
            "name": "CI",
            "conclusion": "success",
            "head_branch": "main",
            "html_url": "https://github.com/jeffrichley/foreman/actions/runs/9876543",
            "event": "push",
            "big_nested_key": {"lots": "of", "data": True},  # decoy
        },
        "sender": {"login": "alice", "extra": "lots"},  # decoy
    }
    event = GitHubEvent(
        event_id="e2",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["event_type"] == "workflow_run"
    assert body["action"] == "completed"
    assert body["repo"] == "jeffrichley/foreman"
    assert body["html_url"] == "https://github.com/jeffrichley/foreman/actions/runs/9876543"
    assert body["workflow_run.id"] == 9876543
    assert body["workflow_run.name"] == "CI"
    assert body["workflow_run.conclusion"] == "success"
    assert body["workflow_run.head_branch"] == "main"
    assert body["workflow_run.html_url"] == "https://github.com/jeffrichley/foreman/actions/runs/9876543"
    assert body["workflow_run.event"] == "push"

    # Decoys must be absent
    assert "sender" not in body
    assert "workflow_run" not in body


def test_project_issues_returns_universal_plus_issue_fields(tmp_path: Path) -> None:
    raw = {
        "action": "labeled",
        "repository": {"full_name": "jeffrichley/foreman"},
        "issue": {
            "number": 77,
            "title": "Bug report",
            "user": {"login": "bob"},
            "html_url": "https://github.com/jeffrichley/foreman/issues/77",
        },
        "label": {"name": "bug"},
        "installation": {"id": 99},  # decoy
    }
    event = GitHubEvent(
        event_id="e3",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="issues",
        action="labeled",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["event_type"] == "issues"
    assert body["html_url"] == "https://github.com/jeffrichley/foreman/issues/77"
    assert body["issue.number"] == 77
    assert body["issue.title"] == "Bug report"
    assert body["issue.user.login"] == "bob"
    assert body["label.name"] == "bug"

    # Decoy absent
    assert "installation" not in body
    assert "issue" not in body


def test_project_issue_comment_returns_universal_plus_comment_fields(tmp_path: Path) -> None:
    raw = {
        "action": "created",
        "repository": {"full_name": "jeffrichley/foreman"},
        "issue": {
            "number": 7,
            "title": "A question",
            "user": {"login": "bob"},
            "html_url": "https://github.com/jeffrichley/foreman/issues/7",
        },
        "comment": {"user": {"login": "alice"}, "body": "Short comment"},
        "sender": {"login": "alice", "big_blob": "ignore me"},  # decoy
    }
    event = GitHubEvent(
        event_id="e4",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="issue_comment",
        action="created",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["event_type"] == "issue_comment"
    assert body["html_url"] == "https://github.com/jeffrichley/foreman/issues/7"
    assert body["issue.number"] == 7
    assert body["issue.title"] == "A question"
    assert body["comment.user.login"] == "alice"
    assert body["comment.body"] == "Short comment"

    # Decoy absent
    assert "sender" not in body
    assert "comment" not in body


def test_project_push_returns_universal_plus_push_fields(tmp_path: Path) -> None:
    raw = {
        "ref": "refs/heads/main",
        "before": "aaa",
        "after": "bbb",
        "repository": {"full_name": "jeffrichley/foreman"},
        "head_commit": {
            "id": "bbb",
            "message": "Fix bug",
        },
        "pusher": {"name": "alice"},
        "compare": "https://github.com/jeffrichley/foreman/compare/aaa...bbb",
        "commits": [{"id": "bbb", "message": "Fix bug", "big_blob": True}],  # decoy
    }
    event = GitHubEvent(
        event_id="e5",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="push",
        action="",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["event_type"] == "push"
    assert body["html_url"] == "https://github.com/jeffrichley/foreman/compare/aaa...bbb"
    assert body["ref"] == "refs/heads/main"
    assert body["before"] == "aaa"
    assert body["after"] == "bbb"
    assert body["head_commit.id"] == "bbb"
    assert body["head_commit.message"] == "Fix bug"
    assert body["pusher.name"] == "alice"

    # Decoy absent
    assert "commits" not in body
    assert "head_commit" not in body


def test_project_release_returns_universal_plus_release_fields(tmp_path: Path) -> None:
    raw = {
        "action": "published",
        "repository": {"full_name": "jeffrichley/foreman"},
        "release": {
            "tag_name": "v1.2.3",
            "name": "Release 1.2.3",
            "html_url": "https://github.com/jeffrichley/foreman/releases/tag/v1.2.3",
            "prerelease": False,
            "body": "# Changelog\n" + "x" * 5000,  # large decoy
        },
        "sender": {"login": "alice", "big_blob": True},  # decoy
    }
    event = GitHubEvent(
        event_id="e6",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="release",
        action="published",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["event_type"] == "release"
    assert body["html_url"] == "https://github.com/jeffrichley/foreman/releases/tag/v1.2.3"
    assert body["release.tag_name"] == "v1.2.3"
    assert body["release.name"] == "Release 1.2.3"
    assert body["release.html_url"] == "https://github.com/jeffrichley/foreman/releases/tag/v1.2.3"
    assert body["release.prerelease"] is False

    # Large release.body and sender absent
    assert "sender" not in body
    assert "release" not in body


def test_project_status_returns_universal_plus_status_fields(tmp_path: Path) -> None:
    raw = {
        "state": "success",
        "context": "ci/test",
        "description": "All checks passed",
        "target_url": "https://ci.example.com/build/123",
        "sha": "abc123",
        "repository": {"full_name": "jeffrichley/foreman"},
        "commit": {"author": {"name": "alice"}, "big_blob": True},  # decoy
    }
    event = GitHubEvent(
        event_id="e7",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="status",
        action="",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["event_type"] == "status"
    assert body["html_url"] == "https://ci.example.com/build/123"
    assert body["state"] == "success"
    assert body["context"] == "ci/test"
    assert body["description"] == "All checks passed"
    assert body["target_url"] == "https://ci.example.com/build/123"
    assert body["sha"] == "abc123"

    # Decoy absent
    assert "commit" not in body


def test_project_skips_missing_optional_fields(tmp_path: Path) -> None:
    # pull_request event where requested_reviewer is absent.
    # The key must not appear in the body at all (not as None).
    raw = {
        "action": "opened",
        "number": 1,
        "repository": {"full_name": "jeffrichley/foreman"},
        "pull_request": {
            "title": "T",
            "user": {"login": "alice"},
            "head": {"ref": "feat"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/jeffrichley/foreman/pull/1",
        },
        # requested_reviewer intentionally absent
    }
    event = GitHubEvent(
        event_id="e-miss",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="pull_request",
        action="opened",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    # Requested reviewer is absent from raw — must be absent from body too.
    assert "requested_reviewer.login" not in body
    # Other fields must still be present.
    assert body["pull_request.title"] == "T"


def test_project_issue_comment_truncates_comment_body_to_200_chars(tmp_path: Path) -> None:
    raw = {
        "action": "created",
        "repository": {"full_name": "jeffrichley/foreman"},
        "issue": {
            "number": 7,
            "title": "Bug",
            "user": {"login": "bob"},
            "html_url": "https://github.com/jeffrichley/foreman/issues/7",
        },
        "comment": {"user": {"login": "alice"}, "body": "x" * 500},
    }
    event = GitHubEvent(
        event_id="e-trunc",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="issue_comment",
        action="created",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body["comment.body"] == "x" * 200
    assert len(body["comment.body"]) == 200


def test_project_unknown_event_type_falls_back_to_raw(tmp_path: Path) -> None:
    raw = {"action": "frobbed", "some_big_key": {"nested": "data"}}
    # event_type="deployment" is not in _PROJECTIONS
    event = GitHubEvent(
        event_id="e-unknown",
        landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc),
        event_type="deployment",
        action="frobbed",
        repo_full_name="jeffrichley/foreman",
        raw=raw,
    )
    conn = _make_connector_empty(tmp_path)
    body = conn.project(event)

    assert body == dict(event.raw)
