"""Typed GitHub webhook event subset for v1.a.

The full GitHub webhook payload is a large open shape; v1.a needs only
the slice the policy rules examine. Connector parses the raw JSON into
one of these typed shapes; unknown actions / unsupported event types
fall through to GitHubUnknownEvent which the policy always denies.
"""
from typing import Literal

from pydantic import ConfigDict

from agent_core_inbound.types import ConnectorEvent


class GitHubEvent(ConnectorEvent):
    """Common base; ``action`` and ``repo_full_name`` are the two
    fields every event we care about carries.
    """

    action: str
    repo_full_name: str  # "jeffrichley/foreman"
    event_type: str      # "pull_request" | "issue_comment" | ...
    model_config = ConfigDict(extra="allow")


class GitHubPullRequestReviewRequestedEvent(GitHubEvent):
    event_type: Literal["pull_request"] = "pull_request"
    action: Literal["review_requested"] = "review_requested"
    pr_number: int
    requested_reviewer_login: str | None = None


class GitHubIssueCommentEvent(GitHubEvent):
    event_type: Literal["issue_comment"] = "issue_comment"
    action: Literal["created", "edited", "deleted"]
    issue_number: int
    comment_body: str = ""
    comment_author_login: str = ""


class GitHubIssuesLabeledEvent(GitHubEvent):
    event_type: Literal["issues"] = "issues"
    action: Literal["labeled"] = "labeled"
    issue_number: int
    label_name: str
