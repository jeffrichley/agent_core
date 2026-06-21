"""GitHub allowance TOML schema + loader."""
from pathlib import Path

import pytest
from agent_core_inbound.github_allowance import load_allowance
from agent_core_inbound.types import Tier
from pydantic import ValidationError


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_empty_allowance(tmp_path: Path):
    p = _write(tmp_path / "g.toml", "")
    cfg = load_allowance(p)
    assert cfg.allow == []


def test_load_single_rule(tmp_path: Path):
    p = _write(
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
    cfg = load_allowance(p)
    assert len(cfg.allow) == 1
    rule = cfg.allow[0]
    assert rule.rule_id == "pr_review_requested_foreman"
    assert rule.event == "pull_request_review_requested"
    assert rule.repo == "jeffrichley/foreman"
    # v2: reviewer shortcut is translated into match dict and nulled.
    assert rule.reviewer is None
    assert rule.match == {"requested_reviewer.login": "wrenrichley"}
    assert rule.tier == Tier.RED
    assert rule.reason == "PR review requested on foreman"


def test_load_rejects_unknown_tier(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "x"
event = "issue_comment"
tier = "purple"
reason = "x"
""",
    )
    with pytest.raises(ValidationError):
        load_allowance(p)


def test_rule_id_unique_across_rules_enforced(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "duplicate"
event = "issue_comment"
tier = "yellow"
reason = "a"

[[allow]]
rule_id = "duplicate"
event = "pull_request_review_requested"
tier = "red"
reason = "b"
""",
    )
    with pytest.raises(ValidationError, match="duplicate rule_id"):
        load_allowance(p)


def test_rule_id_required(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
event = "issue_comment"
tier = "yellow"
reason = "x"
""",
    )
    with pytest.raises(ValidationError):
        load_allowance(p)


def test_reason_required(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "x"
event = "issue_comment"
tier = "yellow"
""",
    )
    with pytest.raises(ValidationError):
        load_allowance(p)


def test_allow_rule_accepts_match_dict() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    rule = AllowRule(
        rule_id="r1",
        event="workflow_run_completed",
        match={"workflow_run.conclusion": "failure"},
        tier="red",
        reason="CI failed",
    )
    assert rule.match == {"workflow_run.conclusion": "failure"}


def test_allow_rule_match_defaults_to_none() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    rule = AllowRule(rule_id="r1", event="ping", tier="green", reason="webhook ping")
    assert rule.match is None


def test_allow_rule_body_contains_rejected() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    with pytest.raises(ValidationError, match="body_contains.*removed"):
        AllowRule(
            rule_id="r1",
            event="issue_comment_created",
            body_contains="TRIGGER",
            tier="green",
            reason="comment trigger",
        )


def test_reviewer_shortcut_translates_to_match() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    rule = AllowRule(
        rule_id="r1",
        event="pull_request_review_requested",
        reviewer="wrenrichley",
        tier="red",
        reason="PR review on me",
    )
    assert rule.match == {"requested_reviewer.login": "wrenrichley"}
    # reviewer field is cleared after translation:
    assert rule.reviewer is None


def test_label_name_shortcut_translates_to_match() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    rule = AllowRule(
        rule_id="r1",
        event="issues_labeled",
        label_name="foreman:needs-help",
        tier="red",
        reason="needs-help escalation",
    )
    assert rule.match == {"label.name": "foreman:needs-help"}
    assert rule.label_name is None


def test_shortcut_and_match_can_coexist() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    rule = AllowRule(
        rule_id="r1",
        event="pull_request_review_requested",
        reviewer="wrenrichley",
        match={"pull_request.draft": False},
        tier="red",
        reason="non-draft PR review on me",
    )
    # Both entries merge into the final match dict:
    assert rule.match == {
        "requested_reviewer.login": "wrenrichley",
        "pull_request.draft": False,
    }


def test_reviewer_and_label_name_both_translate() -> None:
    from agent_core_inbound.github_allowance import AllowRule
    rule = AllowRule(
        rule_id="r1",
        event="pull_request_review_requested",
        reviewer="wrenrichley",
        label_name="foreman:needs-help",
        tier="red",
        reason="both shortcuts on one rule",
    )
    assert rule.match == {
        "requested_reviewer.login": "wrenrichley",
        "label.name": "foreman:needs-help",
    }
    assert rule.reviewer is None
    assert rule.label_name is None
