# Inbound Notifications v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the agent-core-inbound GitHubConnector from typed-class-per-event to schema-flexible dotted-key matching, so any of GitHub's webhook event types is matchable via TOML rules with no code changes.

**Architecture:** Collapse 3 typed event classes into one generic `GitHubEvent` carrying `event_type`, `action`, `repo_full_name`, and `raw: dict` (full payload). Replace `_EVENT_KEYS` whitelist + isinstance-based field filtering with: (a) generic event-key composition (`f"{event_type}_{action}"`), (b) dotted-path resolver against the raw payload, and (c) `match: dict[str, Any]` field on `AllowRule` for filter conditions. Honor v1.a's `reviewer` and `label_name` shortcuts via a model validator. Remove `body_contains` outright (raise on load). Update webhook subscriptions on all 3 registered repos to the curated 8-event set.

**Tech Stack:** Python 3.12+, Pydantic v2, FastAPI/uvicorn, watchdog (mtime reload), pytest + pytest-asyncio, uv (workspace manager), ruff + mypy + lint-imports (linters), GitHub webhooks REST API.

**Spec:** `docs/superpowers/specs/2026-06-21-inbound-v2-schema-flexible-events-design.md` (this branch).

---

## File structure — what touches what

**Modified:**
- `packages/agent-core-inbound/src/agent_core_inbound/github_event.py` — collapse 4 typed classes to 1 generic `GitHubEvent`; add `raw: dict[str, Any]` field. Drop `GitHubPullRequestReviewRequestedEvent`, `GitHubIssueCommentEvent`, `GitHubIssuesLabeledEvent`.
- `packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py` — `AllowRule` gains `match: dict[str, Any] | None` field; model_validator translates `reviewer` / `label_name` shortcuts to `match` entries; raises `ValueError` if `body_contains` is set.
- `packages/agent-core-inbound/src/agent_core_inbound/github_connector.py` — drop `_EVENT_KEYS` map; add generic `_event_key()` + `_resolve_path()` helpers; rewrite `_first_matching_rule()` to use dotted-path resolution.
- `packages/agent-core-inbound/src/agent_core_inbound/funnel_handler.py` — collapse `X-GitHub-Event`-keyed dispatch to single `GitHubEvent` construction.
- `packages/agent-core-inbound/README.md` — update operator runbook for new rule shape + curated webhook event list.
- `packages/agent-core-inbound/tests/test_github_allowance.py` — new tests for `match` field + shortcuts + `body_contains` rejection.
- `packages/agent-core-inbound/tests/test_github_connector.py` — new tests for dotted-path resolver + event-key composition + generic classify.
- `packages/agent-core-inbound/tests/test_funnel_handler.py` — update existing tests; add coverage for unmodeled event types.
- `packages/agent-core-inbound/tests/test_router_integration.py` — update v1.a's PR-review-requested test for new shape; add new tests for `workflow_run` failure + `pull_request.opened`.

**Deleted:** None at file level — typed event classes are deleted IN `github_event.py`.

**Operator-side (post-merge deploy):**
- `~/.wren/.config/inbound/github-allowance.toml` — migrate 1 rule → 7 rules (schema-flexible form).
- `jeffrichley/foreman` + `jeffrichley/voice` + `jeffrichley/agent_core` webhooks — patch event subscriptions via `gh api`.

---

## Task 1: `AllowRule.match` field + body_contains rejection

**Files:**
- Test: `packages/agent-core-inbound/tests/test_github_allowance.py`
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py:18-33` (the `AllowRule` class)

- [ ] **Step 1: Write failing test for `match` field**

Add to `tests/test_github_allowance.py`:

```python
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
    from pydantic import ValidationError
    from agent_core_inbound.github_allowance import AllowRule
    with pytest.raises(ValidationError, match="body_contains.*removed"):
        AllowRule(
            rule_id="r1",
            event="issue_comment_created",
            body_contains="TRIGGER",
            tier="green",
            reason="comment trigger",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_allowance.py::test_allow_rule_accepts_match_dict -v
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_allowance.py::test_allow_rule_body_contains_rejected -v
```

Expected: both FAIL — `match` field doesn't exist; `body_contains` is still a valid field that doesn't raise.

- [ ] **Step 3: Add `match` field + body_contains rejection**

Edit `github_allowance.py`, replace the `AllowRule` class with:

```python
from typing import Any
from pydantic import model_validator


class AllowRule(BaseModel):
    """One allowance rule. First-match-wins in classify()."""

    rule_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    tier: Tier
    reason: str = Field(min_length=1)

    # Optional match constraints. All present constraints must match
    # for the rule to apply. Missing constraints are wildcard.
    repo: str | None = None
    reviewer: str | None = None
    label_name: str | None = None
    match: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_body_contains(cls, data: Any) -> Any:
        if isinstance(data, dict) and "body_contains" in data:
            raise ValueError(
                "AllowRule field 'body_contains' was removed in v2. "
                "Use `match` with an exact-equality dotted path; substring "
                "matching is deferred to v2.1 (`match_contains` operator)."
            )
        return data
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_allowance.py -v
```

Expected: PASS for the 3 new tests + all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py packages/agent-core-inbound/tests/test_github_allowance.py
git commit -m "feat(inbound): add AllowRule.match field; remove body_contains"
```

---

## Task 2: model_validator translates `reviewer` / `label_name` shortcuts to `match`

**Files:**
- Test: `packages/agent-core-inbound/tests/test_github_allowance.py`
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py` (the `AllowRule` class)

- [ ] **Step 1: Write failing tests for shortcuts**

Add to `tests/test_github_allowance.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_allowance.py::test_reviewer_shortcut_translates_to_match packages/agent-core-inbound/tests/test_github_allowance.py::test_label_name_shortcut_translates_to_match -v
```

Expected: FAIL — shortcuts aren't translated; `rule.match` is None.

- [ ] **Step 3: Add `@model_validator(mode="after")` for shortcut translation**

Append to `AllowRule` class in `github_allowance.py`:

```python
    @model_validator(mode="after")
    def _translate_shortcuts(self) -> AllowRule:
        # Merge reviewer / label_name shortcuts into match, then null them.
        # Existing match entries take precedence on key collision (shouldn't
        # happen in practice — these are distinct field names).
        added: dict[str, Any] = {}
        if self.reviewer is not None:
            added["requested_reviewer.login"] = self.reviewer
            object.__setattr__(self, "reviewer", None)
        if self.label_name is not None:
            added["label.name"] = self.label_name
            object.__setattr__(self, "label_name", None)
        if added:
            merged = dict(added)
            if self.match is not None:
                merged.update(self.match)
            object.__setattr__(self, "match", merged)
        return self
```

(Note: `object.__setattr__` is needed because `BaseModel` instances are mutable-by-default but field validation re-runs on standard assignment; using `object.__setattr__` bypasses validation cleanly for this internal mutation.)

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_allowance.py -v
```

Expected: all shortcut tests PASS; existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py packages/agent-core-inbound/tests/test_github_allowance.py
git commit -m "feat(inbound): translate reviewer/label_name shortcuts into match dict"
```

---

## Task 3: Collapse `GitHubEvent` to generic shape; add `raw: dict[str, Any]`

**Files:**
- Test: `packages/agent-core-inbound/tests/test_funnel_handler.py`
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/github_event.py` (whole file)

- [ ] **Step 1: Write failing test for generic event shape**

Add to `tests/test_funnel_handler.py`:

```python
def test_github_event_generic_shape_holds_raw() -> None:
    from agent_core_inbound.github_event import GitHubEvent
    event = GitHubEvent(
        event_id="abc123",
        landed_at=datetime(2026, 6, 21, 17, 2, 0, tzinfo=timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw={"workflow_run": {"conclusion": "failure"}, "repository": {"full_name": "jeffrichley/foreman"}},
    )
    assert event.event_type == "workflow_run"
    assert event.action == "completed"
    assert event.repo_full_name == "jeffrichley/foreman"
    assert event.raw["workflow_run"]["conclusion"] == "failure"


def test_github_event_action_optional_for_actionless_events() -> None:
    from agent_core_inbound.github_event import GitHubEvent
    event = GitHubEvent(
        event_id="ping123",
        landed_at=datetime(2026, 6, 21, 17, 2, 0, tzinfo=timezone.utc),
        event_type="ping",
        action="",
        repo_full_name="jeffrichley/foreman",
        raw={"zen": "..."},
    )
    assert event.action == ""
```

(Add datetime/timezone imports to the test file if needed.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_funnel_handler.py::test_github_event_generic_shape_holds_raw -v
```

Expected: FAIL — `raw` field doesn't exist on GitHubEvent.

- [ ] **Step 3: Rewrite `github_event.py`**

Replace the whole file content:

```python
"""Generic GitHub webhook event shape for v2.

v2 collapses the typed-class-per-event hierarchy from v1.a to a single
generic event. The connector's matching engine walks `raw` via dotted
paths — see ``github_connector.resolve_path()`` and the
schema-flexible spec
(``docs/superpowers/specs/2026-06-21-inbound-v2-schema-flexible-events-design.md``).
"""
from __future__ import annotations

from typing import Any

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
    so connector rules can match on any field via dotted paths.
    """

    event_type: str = Field(min_length=1)
    action: str = ""
    repo_full_name: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_funnel_handler.py::test_github_event_generic_shape_holds_raw packages/agent-core-inbound/tests/test_funnel_handler.py::test_github_event_action_optional_for_actionless_events -v
```

Expected: PASS. Existing tests will fail until Task 4-7 land — that's expected, do not fix here.

- [ ] **Step 5: Commit (with pre-existing test failures noted in message)**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_event.py packages/agent-core-inbound/tests/test_funnel_handler.py
git commit -m "feat(inbound): collapse GitHubEvent to generic shape with raw payload

Typed subclasses (GitHubPullRequestReviewRequestedEvent etc.) are
deleted; rules now match against the raw dict via dotted paths.
Subsequent tasks (4-7) update the connector + parser to consume
this new shape."
```

---

## Task 4: Add `resolve_path()` helper

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/_path.py`
- Test: `packages/agent-core-inbound/tests/test_path.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_path.py`:

```python
"""Tests for the dotted-path resolver."""
from agent_core_inbound._path import MISSING, resolve_path


def test_resolves_top_level_key() -> None:
    assert resolve_path({"a": 1}, "a") == 1


def test_resolves_nested_key() -> None:
    assert resolve_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42


def test_missing_top_level_returns_missing_sentinel() -> None:
    assert resolve_path({"a": 1}, "b") is MISSING


def test_missing_nested_returns_missing_sentinel() -> None:
    assert resolve_path({"a": {"b": 1}}, "a.c") is MISSING


def test_path_through_non_dict_returns_missing() -> None:
    assert resolve_path({"a": "string"}, "a.b") is MISSING


def test_path_through_none_returns_missing() -> None:
    assert resolve_path({"a": None}, "a.b") is MISSING


def test_resolves_falsy_values_correctly() -> None:
    # False / 0 / "" / None / [] / {} all resolve to themselves, not MISSING.
    assert resolve_path({"a": False}, "a") is False
    assert resolve_path({"a": 0}, "a") == 0
    assert resolve_path({"a": ""}, "a") == ""
    assert resolve_path({"a": None}, "a") is None
    assert resolve_path({"a": []}, "a") == []
    assert resolve_path({"a": {}}, "a") == {}


def test_empty_path_returns_missing() -> None:
    # Explicitly: empty path doesn't return the root dict.
    assert resolve_path({"a": 1}, "") is MISSING
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_path.py -v
```

Expected: FAIL with ImportError — module doesn't exist.

- [ ] **Step 3: Create the resolver module**

Create `src/agent_core_inbound/_path.py`:

```python
"""Dotted-path resolver for the GitHubConnector matching engine.

Walks a nested dict by splitting a dotted path into keys. Returns the
sentinel ``MISSING`` for any path that doesn't resolve cleanly —
including paths through non-dict cursor values, missing keys, or the
empty path. Falsy values (False, 0, "", None, [], {}) are NOT treated
as missing; they're real values the resolver returns directly.
"""
from __future__ import annotations

from typing import Any, Final


class _MissingSentinel:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _MissingSentinel()


def resolve_path(payload: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path against a nested dict.

    Returns ``MISSING`` for any path that doesn't fully resolve.
    """
    if not path:
        return MISSING
    cursor: Any = payload
    for key in path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return MISSING
        cursor = cursor[key]
    return cursor


__all__ = ["MISSING", "resolve_path"]
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_path.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/_path.py packages/agent-core-inbound/tests/test_path.py
git commit -m "feat(inbound): add dotted-path resolver helper"
```

---

## Task 5: Generic event-key composition in GitHubConnector

**Files:**
- Test: `packages/agent-core-inbound/tests/test_github_connector.py`
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/github_connector.py:104-108` (the `_event_key_for` function)

- [ ] **Step 1: Write failing test**

Add to `tests/test_github_connector.py`:

```python
def test_event_key_composes_event_type_plus_action() -> None:
    from agent_core_inbound.github_connector import _event_key
    from agent_core_inbound.github_event import GitHubEvent
    from datetime import datetime, timezone

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
    from agent_core_inbound.github_event import GitHubEvent
    from datetime import datetime, timezone

    event = GitHubEvent(
        event_id="ping",
        landed_at=datetime(2026, 6, 21, 17, 2, 0, tzinfo=timezone.utc),
        event_type="push",
        action="",
        repo_full_name="jeffrichley/foreman",
        raw={},
    )
    assert _event_key(event) == "push"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_connector.py::test_event_key_composes_event_type_plus_action -v
```

Expected: FAIL — `_event_key` doesn't exist (old name is `_event_key_for`, takes event type as parameter and looks it up in `_EVENT_KEYS`).

- [ ] **Step 3: Replace `_EVENT_KEYS` map + `_event_key_for` with generic `_event_key`**

In `github_connector.py`:
- Delete the `_EVENT_KEYS` dict (lines ~27-31)
- Delete the `from agent_core_inbound.github_event import (...specific typed classes...)` import; keep only `GitHubEvent`
- Delete the existing `_event_key_for()` function (lines ~104-108)
- Add the new generic `_event_key()` function:

```python
def _event_key(event: GitHubEvent) -> str:
    """Compose the synthetic event key the rules match against.

    For events with an action sub-type (pull_request, issues, etc.):
    returns ``f"{event_type}_{action}"``. For action-less events
    (push, ping, create, delete, etc.): returns just ``event_type``.
    """
    if event.action:
        return f"{event.event_type}_{event.action}"
    return event.event_type
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_connector.py::test_event_key_composes_event_type_plus_action packages/agent-core-inbound/tests/test_github_connector.py::test_event_key_drops_underscore_for_actionless_events -v
```

Expected: PASS.

- [ ] **Step 5: Commit (with note that connector body still references deleted symbols)**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_connector.py packages/agent-core-inbound/tests/test_github_connector.py
git commit -m "feat(inbound): generic event-key composition (drop _EVENT_KEYS whitelist)

Subsequent task rewires _first_matching_rule to use the new key shape
plus the dotted-path resolver — until then, some existing connector
tests fail. That's expected."
```

---

## Task 6: Rewrite `_first_matching_rule` to use dotted-path resolver

**Files:**
- Test: `packages/agent-core-inbound/tests/test_github_connector.py`
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/github_connector.py:78-101` (the `_first_matching_rule` method)

- [ ] **Step 1: Write failing tests for the new matcher**

Add to `tests/test_github_connector.py`:

```python
import tempfile
from pathlib import Path
from datetime import datetime, timezone


def _make_connector(rules_toml: str) -> "GitHubConnector":
    from agent_core_inbound.github_connector import GitHubConnector
    p = Path(tempfile.mkstemp(suffix=".toml")[1])
    p.write_text(rules_toml, encoding="utf-8")
    return GitHubConnector(config_path=p, principal_being="wren")


def test_matches_event_and_repo_only(monkeypatch) -> None:
    from agent_core_inbound.github_event import GitHubEvent
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "pr_opened"
        event = "pull_request_opened"
        repo = "jeffrichley/foreman"
        tier = "yellow"
        reason = "PR opened"
        """
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
    assert verdict.__class__.__name__ == "Allow"
    assert verdict.tier == "yellow"


def test_match_dotted_path_succeeds(monkeypatch) -> None:
    from agent_core_inbound.github_event import GitHubEvent
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """
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
    assert verdict.__class__.__name__ == "Allow"


def test_match_dotted_path_value_mismatch_denies(monkeypatch) -> None:
    from agent_core_inbound.github_event import GitHubEvent
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """
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
    assert verdict.__class__.__name__ == "Deny"


def test_match_missing_path_denies(monkeypatch) -> None:
    from agent_core_inbound.github_event import GitHubEvent
    conn = _make_connector(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """
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
    assert verdict.__class__.__name__ == "Deny"


def test_first_match_wins_preserved(monkeypatch) -> None:
    from agent_core_inbound.github_event import GitHubEvent
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
        """
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
    assert verdict.__class__.__name__ == "Allow"
    assert verdict.reason == "specific match"  # first rule wins
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_connector.py -v
```

Expected: tests written above FAIL — the connector still uses isinstance branches that reference deleted typed classes.

- [ ] **Step 3: Rewrite `_first_matching_rule`**

Replace the body of `_first_matching_rule()` in `github_connector.py` with:

```python
    def _first_matching_rule(self, event: GitHubEvent) -> AllowRule | None:
        from agent_core_inbound._path import MISSING, resolve_path

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
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_connector.py -v
```

Expected: all new tests PASS; existing connector tests fail until Task 7 lands (they reference old typed event classes). That's expected.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_connector.py packages/agent-core-inbound/tests/test_github_connector.py
git commit -m "feat(inbound): rewrite _first_matching_rule with dotted-path resolver

Replaces isinstance-based field filtering with generic resolver
against the raw payload dict. All match entries must succeed (AND
semantics); first matching rule wins (preserved from v1.a)."
```

---

## Task 7: Collapse `funnel_handler.py` parser to single GitHubEvent

**Files:**
- Test: `packages/agent-core-inbound/tests/test_funnel_handler.py`
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/funnel_handler.py` (entire `_parse_event` plus the typed-class import block)

- [ ] **Step 1: Write failing tests for the new parser**

Add to `tests/test_funnel_handler.py`:

```python
def test_parses_known_event_into_generic_shape() -> None:
    from agent_core_inbound.funnel_handler import _parse_event
    headers = {"X-GitHub-Event": "pull_request"}
    body = b'{"action": "opened", "pull_request": {"number": 21}, "repository": {"full_name": "jeffrichley/foreman"}}'
    event = _parse_event(headers, body, event_id="abc")
    assert event.event_type == "pull_request"
    assert event.action == "opened"
    assert event.repo_full_name == "jeffrichley/foreman"
    assert event.raw["pull_request"]["number"] == 21


def test_parses_actionless_event() -> None:
    from agent_core_inbound.funnel_handler import _parse_event
    headers = {"X-GitHub-Event": "push"}
    body = b'{"ref": "refs/heads/main", "repository": {"full_name": "jeffrichley/foreman"}}'
    event = _parse_event(headers, body, event_id="push1")
    assert event.event_type == "push"
    assert event.action == ""
    assert event.repo_full_name == "jeffrichley/foreman"


def test_parses_unmodeled_event_silently() -> None:
    # An event GitHub adds tomorrow should parse without crashing.
    from agent_core_inbound.funnel_handler import _parse_event
    headers = {"X-GitHub-Event": "some_future_event"}
    body = b'{"action": "newaction", "repository": {"full_name": "jeffrichley/foreman"}, "novelfield": 42}'
    event = _parse_event(headers, body, event_id="x")
    assert event.event_type == "some_future_event"
    assert event.action == "newaction"
    assert event.raw["novelfield"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_funnel_handler.py -v
```

Expected: FAIL — `_parse_event` doesn't exist or has the wrong signature; existing parser code references deleted typed classes.

- [ ] **Step 3: Rewrite parser code in `funnel_handler.py`**

(a) Remove all imports of typed event subclasses from `agent_core_inbound.github_event` — keep only `GitHubEvent`.

(b) Replace the existing parser (whatever its current name) with:

```python
import json
from datetime import datetime, timezone

from agent_core_inbound.github_event import GitHubEvent


def _parse_event(
    headers: dict[str, str],
    body: bytes,
    *,
    event_id: str,
) -> GitHubEvent:
    """Parse a GitHub webhook into a generic event.

    ``headers`` is the request headers map (case-insensitive lookup of
    ``X-GitHub-Event`` to identify the event type). ``body`` is the raw
    request body; we parse it as JSON and preserve the dict in
    ``GitHubEvent.raw`` for downstream matchers.
    """
    event_type = headers.get("X-GitHub-Event") or headers.get("x-github-event") or ""
    payload = json.loads(body) if body else {}
    return GitHubEvent(
        event_id=event_id,
        landed_at=datetime.now(timezone.utc),
        event_type=event_type,
        action=payload.get("action", ""),
        repo_full_name=(payload.get("repository") or {}).get("full_name", ""),
        raw=payload,
    )
```

(c) Update the FastAPI route handler in `funnel_handler.py` to call `_parse_event(...)` rather than the old typed-class dispatch.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_funnel_handler.py -v
```

Expected: all new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/funnel_handler.py packages/agent-core-inbound/tests/test_funnel_handler.py
git commit -m "feat(inbound): collapse funnel_handler parser to single GitHubEvent"
```

---

## Task 8: Update v1.a integration test for the new event shape

**Files:**
- Modify: `packages/agent-core-inbound/tests/test_router_integration.py` (the existing PR-review-requested test)

- [ ] **Step 1: Read the current v1.a integration test**

```bash
cat packages/agent-core-inbound/tests/test_router_integration.py
```

Identify the test that exercises the PR review_requested smoke (probably named something like `test_router_pr_review_requested_end_to_end`).

- [ ] **Step 2: Update the test to construct GitHubEvent via the new generic shape**

The test currently constructs a typed event class (e.g. `GitHubPullRequestReviewRequestedEvent`). Replace with:

```python
event = GitHubEvent(
    event_id="evt_1",
    landed_at=datetime.now(timezone.utc),
    event_type="pull_request",
    action="review_requested",
    repo_full_name="jeffrichley/foreman",
    raw={
        "action": "review_requested",
        "pull_request": {"number": 21, "html_url": "..."},
        "requested_reviewer": {"login": "wrenrichley"},
        "repository": {"full_name": "jeffrichley/foreman"},
    },
)
```

Also confirm: the TOML allowance the test loads should keep working with `reviewer = "wrenrichley"` (translated to `match = { "requested_reviewer.login" = "wrenrichley" }` by Task 2's validator).

- [ ] **Step 3: Run test to verify pass**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_router_integration.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-inbound/tests/test_router_integration.py
git commit -m "test(inbound): update PR review_requested integration test for v2 event shape"
```

---

## Task 9: Add `workflow_run.completed` failure integration test

**Files:**
- Modify: `packages/agent-core-inbound/tests/test_router_integration.py` (add a new test)

- [ ] **Step 1: Write the test**

Add to `tests/test_router_integration.py`:

```python
@pytest.mark.asyncio
async def test_router_workflow_run_failure_routes_to_bus(tmp_path: Path) -> None:
    """A workflow_run.completed event with conclusion=failure should
    match a rule with ``match = { "workflow_run.conclusion" = "failure" }``
    and produce an Allow + bus envelope."""
    from datetime import datetime, timezone
    from agent_core_inbound.github_event import GitHubEvent
    from agent_core_inbound.github_connector import GitHubConnector
    from agent_core_inbound.router import Router
    from agent_core_inbound.audit import JsonlAuditWriter

    rules_path = tmp_path / "rules.toml"
    rules_path.write_text(
        """
        [[allow]]
        rule_id = "ci_failed"
        event = "workflow_run_completed"
        match = { "workflow_run.conclusion" = "failure" }
        tier = "red"
        reason = "CI failed"
        """,
        encoding="utf-8",
    )
    connector = GitHubConnector(config_path=rules_path, principal_being="wren")
    audit = JsonlAuditWriter(path=tmp_path / "audit.jsonl")
    bus_calls: list[tuple[str, str, str]] = []

    async def fake_publish(target: str, source: str, reason: str, body: dict, urgency: str, landed_at) -> None:
        bus_calls.append((target, source, urgency))

    router = Router(
        connector=connector,
        target_being="wren",
        publish=fake_publish,
        audit=audit,
        rate_limit_per_minute=30,
    )
    event = GitHubEvent(
        event_id="wfr1",
        landed_at=datetime.now(timezone.utc),
        event_type="workflow_run",
        action="completed",
        repo_full_name="jeffrichley/foreman",
        raw={
            "action": "completed",
            "workflow_run": {"conclusion": "failure", "head_branch": "main"},
            "repository": {"full_name": "jeffrichley/foreman"},
        },
    )

    await router.handle(event)

    assert len(bus_calls) == 1
    assert bus_calls[0] == ("wren", "github", "red")
    audit_lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(audit_lines) == 1
    assert "ci_failed" in audit_lines[0]
```

- [ ] **Step 2: Run the test**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_router_integration.py::test_router_workflow_run_failure_routes_to_bus -v
```

Expected: PASS (everything was built in earlier tasks).

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-inbound/tests/test_router_integration.py
git commit -m "test(inbound): add workflow_run failure integration test"
```

---

## Task 10: Add `pull_request.opened` integration test (no `match` field)

**Files:**
- Modify: `packages/agent-core-inbound/tests/test_router_integration.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_router_integration.py`:

```python
@pytest.mark.asyncio
async def test_router_pull_request_opened_no_match_field(tmp_path: Path) -> None:
    """A pull_request.opened event matches a rule with only event + repo
    (no match field)."""
    from datetime import datetime, timezone
    from agent_core_inbound.github_event import GitHubEvent
    from agent_core_inbound.github_connector import GitHubConnector
    from agent_core_inbound.router import Router
    from agent_core_inbound.audit import JsonlAuditWriter

    rules_path = tmp_path / "rules.toml"
    rules_path.write_text(
        """
        [[allow]]
        rule_id = "pr_opened_any"
        event = "pull_request_opened"
        tier = "yellow"
        reason = "PR opened"
        """,
        encoding="utf-8",
    )
    connector = GitHubConnector(config_path=rules_path, principal_being="wren")
    audit = JsonlAuditWriter(path=tmp_path / "audit.jsonl")
    bus_calls: list[tuple[str, str, str]] = []

    async def fake_publish(target, source, reason, body, urgency, landed_at) -> None:
        bus_calls.append((target, source, urgency))

    router = Router(
        connector=connector,
        target_being="wren",
        publish=fake_publish,
        audit=audit,
        rate_limit_per_minute=30,
    )
    event = GitHubEvent(
        event_id="pr1",
        landed_at=datetime.now(timezone.utc),
        event_type="pull_request",
        action="opened",
        repo_full_name="jeffrichley/foreman",
        raw={"action": "opened", "pull_request": {"number": 22}, "repository": {"full_name": "jeffrichley/foreman"}},
    )
    await router.handle(event)
    assert bus_calls == [("wren", "github", "yellow")]
```

- [ ] **Step 2: Run test**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_router_integration.py::test_router_pull_request_opened_no_match_field -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-inbound/tests/test_router_integration.py
git commit -m "test(inbound): add pull_request.opened integration test (no match field)"
```

---

## Task 11: TOML load-time `body_contains` rejection integration test

**Files:**
- Modify: `packages/agent-core-inbound/tests/test_github_allowance.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_github_allowance.py`:

```python
def test_toml_load_rejects_body_contains(tmp_path) -> None:
    from pydantic import ValidationError
    from agent_core_inbound.github_allowance import load_allowance

    p = tmp_path / "rules.toml"
    p.write_text(
        """
        [[allow]]
        rule_id = "old"
        event = "issue_comment_created"
        body_contains = "TRIGGER"
        tier = "green"
        reason = "comment trigger"
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="body_contains.*removed"):
        load_allowance(p)
```

- [ ] **Step 2: Run test**

```bash
uv run --package agent-core-inbound pytest packages/agent-core-inbound/tests/test_github_allowance.py::test_toml_load_rejects_body_contains -v
```

Expected: PASS (Task 1's validator already handles this; the test just confirms it propagates through TOML load).

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-inbound/tests/test_github_allowance.py
git commit -m "test(inbound): confirm body_contains rejection at TOML load time"
```

---

## Task 12: README update — new rule shape + curated webhook events

**Files:**
- Modify: `packages/agent-core-inbound/README.md`

- [ ] **Step 1: Read the current README to identify sections to update**

```bash
cat packages/agent-core-inbound/README.md
```

Sections to touch:
- Step 2 (Write Wren's allowance file) — show the new `match`-based rule format alongside the still-supported shortcuts
- Step 5 (Configure the GitHub webhook) — update "Which events" to the curated 8-event list, with rationale
- Troubleshooting — add a one-liner about the v1.a `--set-path` Tailscale gotcha (deferred from inbound v1.a task #496 — fold it in here as long as we're touching the README)

- [ ] **Step 2: Apply the edits**

For step 2 in the README:

```markdown
### 2. Write Wren's allowance file

`~/.wren/.config/inbound/github-allowance.toml`:

```toml
# Schema-flexible rule shape (v2).  See spec
# docs/superpowers/specs/2026-06-21-inbound-v2-schema-flexible-events-design.md
# for the full grammar.

[[allow]]
rule_id = "pr_review_requested_any_project"
event = "pull_request_review_requested"
match = { "requested_reviewer.login" = "wrenrichley" }
tier = "red"
reason = "PR review requested on me"

[[allow]]
rule_id = "needs_help_foreman"
event = "issues_labeled"
repo = "jeffrichley/foreman"
match = { "label.name" = "foreman:needs-help" }
tier = "red"
reason = "Foreman escalation — needs operator unstick"
```

The `reviewer`/`label_name` shortcuts from v1.a still work — translate to `match` entries automatically. `body_contains` was removed in v2 (raises ValueError on load); use exact-equality `match` instead, or wait for v2.1's `match_contains` operator.

The router watches the file's mtime and reloads on every webhook delivery — edit the TOML and the next event picks up the new rules without restarting the daemon.
```

For step 5 in the README, the "Which events" line:

```markdown
- **Which events:** "Let me select individual events" — check **Workflow runs**, **Pull requests**, **Pull request reviews**, **Issues**, **Issue comments**, **Pushes**, **Releases**, **Statuses**. (Schema-flexible matching means we can add more later via `gh api -X PATCH repos/<repo>/hooks/<id> -f events='[...]'` with no daemon change.)
```

Add a troubleshooting bullet near the end:

```markdown
- **Webhook deliveries land 404 from uvicorn:** the Tailscale Funnel command should be `tailscale funnel <port>` — do NOT use `--set-path=/github`. That flag STRIPS the path prefix before forwarding, leaving uvicorn to see `POST /` (no route). The default mount at `/` is correct because the FastAPI route is at `/github`.
```

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-inbound/README.md
git commit -m "docs(inbound): README updated for v2 schema-flexible rules + curated event subscription"
```

---

## Task 13: Final `just check` + push branch

**Files:** none modified (verification + push)

- [ ] **Step 1: Run full check from agent_core repo root**

```bash
just check
```

Expected: ruff clean, mypy clean, lint-imports clean, pytest passes with 85%+ coverage on the inbound package.

- [ ] **Step 2: If anything fails, fix in-place and amend the relevant commit**

(Common failure: import-linter rules in `lint-imports.cfg` may need updating if we removed `_EVENT_KEYS`-related imports. Fix and amend.)

- [ ] **Step 3: Push the branch**

```bash
export GH_TOKEN=$(python "C:/Users/jeffr/.wren/.claude/skills/creds-management/scripts/creds.py" --being wren get github --keyring --password | tr -d '\r\n')
git push
unset GH_TOKEN
```

Expected: pre-push hook runs `just check` (clean), then push succeeds.

- [ ] **Step 4: Open the PR**

```bash
export GH_TOKEN=$(python "C:/Users/jeffr/.wren/.claude/skills/creds-management/scripts/creds.py" --being wren get github --keyring --password | tr -d '\r\n')
gh pr create --title "feat(inbound): v2 — schema-flexible GitHub event matching" --body "$(cat <<'EOF'
Implements the v2 schema-flexible event matching design.

See spec: \`docs/superpowers/specs/2026-06-21-inbound-v2-schema-flexible-events-design.md\`

## Summary
- Collapse \`GitHubEvent\` to one generic class with \`raw: dict\` payload.
- Drop \`_EVENT_KEYS\` whitelist; compose event keys generically.
- \`AllowRule\` grows \`match: dict[str, Any]\` field; \`reviewer\` and \`label_name\` honored as shortcuts via model validator.
- \`body_contains\` removed; raises ValueError on load (no production rules use it).
- README updated for new rule shape + curated 8-event subscription.

## Test plan
- All v1.a tests pass (no regression).
- New tests cover dotted-path resolver edge cases, shortcut translation, event-key composition, integration tests for workflow_run.failure and pull_request.opened, body_contains rejection at TOML load time.
- Pre-push \`just check\` clean.
- Post-merge: operator deploy (Task 14 below).

## After merge
- Build wheels, uv pip install into ~/.agent-core/.venv/, restart daemon.
- Migrate Wren's allowance TOML to the 7-rule v2 form.
- Update webhook subscriptions on jeffrichley/foreman, voice, agent_core to the curated 8 events.
EOF
)"
unset GH_TOKEN
```

Expected: PR opened. Return URL.

- [ ] **Step 5: Adversarial review of the diff before requesting merge**

Per memory rule `feedback_adversarial_review_before_pr`: skim the diff with a hostile reviewer's eye. Surface any concerns inline as PR comments.

---

## Task 14: Operator deploy + smoke validation (post-merge)

**Files (operator-side, not in this PR):**
- Modify: `~/.wren/.config/inbound/github-allowance.toml` (7-rule v2 form)
- Update: webhook event subscriptions on 3 repos via `gh api`

**Trigger:** Run after PR merges to main.

- [ ] **Step 1: Build wheels from main**

```bash
cd e:/workspaces/ai/agents/agent_core
git checkout main
git pull
uv build --package agent-core
uv build --package agent-core-inbound
```

- [ ] **Step 2: Install wheels into prod venv**

```bash
VIRTUAL_ENV="C:/Users/jeffr/.agent-core/.venv" uv pip install --reinstall --no-deps \
  "dist/agent_core-0.<version>-py3-none-any.whl" \
  "dist/agent_core_inbound-0.<version>-py3-none-any.whl"
```

(Replace `<version>` with the actual built version.)

- [ ] **Step 3: Verify imports + plugin discovery in prod venv**

```bash
"C:/Users/jeffr/.agent-core/.venv/Scripts/python.exe" -c "
from agent_core_inbound.github_connector import GitHubConnector, _event_key
from agent_core_inbound.github_event import GitHubEvent
from agent_core_inbound._path import resolve_path, MISSING
print('imports OK')
print('event key for issues_labeled:', _event_key(GitHubEvent(event_id='x', event_type='issues', action='labeled', repo_full_name='r/r', raw={})))
"
```

Expected: `imports OK; event key for issues_labeled: issues_labeled`

- [ ] **Step 4: Migrate Wren's allowance TOML to the 7-rule v2 form**

Replace `~/.wren/.config/inbound/github-allowance.toml` with:

```toml
[[allow]]
rule_id = "pr_review_requested_any_project"
event = "pull_request_review_requested"
match = { "requested_reviewer.login" = "wrenrichley" }
tier = "red"
reason = "PR review requested on me"

[[allow]]
rule_id = "needs_help_foreman"
event = "issues_labeled"
repo = "jeffrichley/foreman"
match = { "label.name" = "foreman:needs-help" }
tier = "red"
reason = "Foreman escalation — needs operator unstick"

[[allow]]
rule_id = "needs_help_voice"
event = "issues_labeled"
repo = "jeffrichley/voice"
match = { "label.name" = "foreman:needs-help" }
tier = "red"
reason = "Foreman escalation — needs operator unstick"

[[allow]]
rule_id = "needs_help_agent_core"
event = "issues_labeled"
repo = "jeffrichley/agent_core"
match = { "label.name" = "foreman:needs-help" }
tier = "red"
reason = "Foreman escalation — needs operator unstick"

[[allow]]
rule_id = "ci_failed_any_project"
event = "workflow_run_completed"
match = { "workflow_run.conclusion" = "failure" }
tier = "red"
reason = "CI failure — investigate"

[[allow]]
rule_id = "ci_passed_pr_branch"
event = "workflow_run_completed"
match = { "workflow_run.conclusion" = "success", "workflow_run.event" = "pull_request" }
tier = "yellow"
reason = "PR CI passed — mergeable"

[[allow]]
rule_id = "pr_opened_any_project"
event = "pull_request_opened"
tier = "yellow"
reason = "New PR opened — queue for review"
```

- [ ] **Step 5: Update webhook event subscriptions on all 3 repos**

For each of foreman, voice, agent_core:

```bash
export GH_TOKEN=$(python C:/Users/jeffr/.wren/.claude/skills/creds-management/scripts/creds.py --being wren get github --keyring --password | tr -d '\r\n')

# Get the hook ID first
HOOK_ID=$(env -u GH_TOKEN gh api repos/jeffrichley/foreman/hooks --jq '.[] | select(.config.url | contains("platinumplatypu")) | .id')

env -u GH_TOKEN gh api -X PATCH "repos/jeffrichley/foreman/hooks/${HOOK_ID}" \
  -f events='["workflow_run","pull_request","pull_request_review","issues","issue_comment","push","release","status"]'

# Repeat for voice + agent_core (create the webhook first if it doesn't exist —
# same payload URL + secret from Wren keyring as v1.a smoke).
unset GH_TOKEN
```

(For repos without existing webhooks, create per v1.a runbook step 5 — same URL, same secret, with the curated event list.)

- [ ] **Step 6: Restart daemon**

```bash
"C:/Users/jeffr/.agent-core/.venv/Scripts/agent-core.exe" daemon stop --instance prod
bash "C:/Users/jeffr/.local/bin/agent-core-start.sh"
"C:/Users/jeffr/.agent-core/.venv/Scripts/agent-core.exe" daemon status --instance prod
```

Expected: daemon up, "InboundEndpoint(name=inbound) started on 127.0.0.1:8765" in the log.

- [ ] **Step 7: Smoke validation — fire a workflow_run via PR**

Push a trivial commit to a test PR on foreman; let CI run; verify:
1. New line in `~/.wren/state/inbound-audit.jsonl` with `verdict=allow, rule_id=ci_failed_any_project` (if CI failed) or `ci_passed_pr_branch` (if passed)
2. Notification envelope in Wren's bus inbox with correct urgency

- [ ] **Step 8: Surface to Jeff**

Report: webhook subscriptions updated, allowance TOML migrated, daemon restarted, smoke landed. Note: existing PR-review-requested flow continues to work (no TOML change disruption).

---

## Notes for the executor

- **TDD discipline.** Every code task starts with a failing test, runs to confirm it fails, then implements, then runs to confirm it passes, then commits. Do not skip the "verify failing" step.
- **Tasks 3–7 leave the test suite temporarily red.** That's expected and called out in commit messages. The full suite is green again after Task 7.
- **`just check` between tasks** is optional; **mandatory after Task 12** before pushing.
- **Pre-push hook (`just check` runs on every push).** Do NOT use `--no-verify` to bypass; if a check fails, fix it in-place and amend.
- **Commit attribution.** Repo doesn't carry `Co-Authored-By` trailers. Lowercase conventional-commit subjects.
- **Staging.** `git add <specific files>`; never `git add -A` or `git add .`.
- **PAT usage.** All GitHub ops use Wren's PAT via `creds.py` → `GH_TOKEN` env. Switch to `env -u GH_TOKEN gh ...` only when explicitly needing an admin-scoped account (e.g. webhook subscription updates require admin scope on the repo — use jeffrichley's keyring auth via `env -u GH_TOKEN`).
