"""GitHubConnector: first-match-wins rule eval + mtime reload."""
import time
from datetime import UTC, datetime
from pathlib import Path

from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.github_event import (
    GitHubIssueCommentEvent,
    GitHubPullRequestReviewRequestedEvent,
)
from agent_core_inbound.types import Allow, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_pr_review_requested_allowed(tmp_path: Path):
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
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-1",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=387,
        requested_reviewer_login="wrenrichley",
        raw={"pr_number": 387},
    )
    verdict = conn.classify(event, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.tier == Tier.RED
    assert verdict.reason == "PR review requested on foreman"


def test_pr_review_requested_wrong_reviewer_denied(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "x"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-2",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=387,
        requested_reviewer_login="other-bot",
        raw={},
    )
    assert isinstance(conn.classify(event, "wren"), Deny)


def test_body_contains_match_required(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "mention"
event = "issue_comment"
repo = "jeffrichley/agent_core"
body_contains = "@wrenrichley"
tier = "yellow"
reason = "@-mention in agent_core issue thread"
""",
    )
    conn = GitHubConnector(config_path=cfg)

    matching = GitHubIssueCommentEvent(
        event_id="gh-3",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/agent_core",
        action="created",
        issue_number=200,
        comment_body="hey @wrenrichley please look at this",
        comment_author_login="jeffrichley",
        raw={},
    )
    non_matching = GitHubIssueCommentEvent(
        event_id="gh-4",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/agent_core",
        action="created",
        issue_number=200,
        comment_body="random comment",
        comment_author_login="someone",
        raw={},
    )
    assert isinstance(conn.classify(matching, "wren"), Allow)
    assert isinstance(conn.classify(non_matching, "wren"), Deny)


def test_first_match_wins(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "specific"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "specific"

[[allow]]
rule_id = "fallback"
event = "pull_request_review_requested"
tier = "yellow"
reason = "fallback"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-5",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    verdict = conn.classify(event, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.reason == "specific"  # first match


def test_rule_id_for_returns_matched_rule_id(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "PR review requested on foreman"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-6",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    conn.classify(event, "wren")
    assert conn.rule_id_for(event_id="gh-6", target_being="wren") == "pr_review_requested_foreman"


def test_mtime_reload_picks_up_new_rule(tmp_path: Path):
    cfg = _write(tmp_path / "g.toml", "")
    conn = GitHubConnector(config_path=cfg)

    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-7",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
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


def test_unknown_target_being_does_not_match_allow(tmp_path: Path):
    # v1.a only routes to wren. A connector configured against Wren's
    # allowance file should deny if someone calls classify with
    # target_being other than "wren". The being-binding is not in the
    # TOML rule shape (yet) — the router level binding is one connector
    # per being. So a defensive deny here is the right shape for v1.a.
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
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-8",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    assert isinstance(conn.classify(event, "pepper"), Deny)
