# Inbound Notifications v2 — Schema-Flexible GitHub Event Matching

**Status:** Design — awaiting Jeff approval
**Author:** Wren
**Date:** 2026-06-21
**Supersedes parts of:** `2026-06-20-inbound-notifications-design.md` (v1.a, GitHub connector internals only)

## Goal

Generalize the GitHubConnector's event-matching engine from typed-class-per-event to **schema-flexible dotted-key matching against the raw webhook payload**. After v2, any of GitHub's 75 webhook event types — and any future ones GitHub adds — is matchable via TOML rules with **zero code changes**. Operators add new signals by writing a rule, not by extending Pydantic models.

## Background — why v1.a's shape doesn't scale

v1.a (merged 2026-06-21) ships a working inbound router with deny-by-default GitHub event classification. The smoke test passes end-to-end: GitHub → Tailscale Funnel → HMAC verify → connector classify → audit log → bus envelope → Wren's inbox.

But the connector models only **3** of GitHub's 75 webhook event types:

```python
_EVENT_KEYS = {
    GitHubPullRequestReviewRequestedEvent: "pull_request_review_requested",
    GitHubIssueCommentEvent:                "issue_comment",
    GitHubIssuesLabeledEvent:               "issues_labeled",
}
```

Adding a new event today requires: a new typed Pydantic class extending `GitHubEvent`, an entry in `_EVENT_KEYS`, an `isinstance()` branch in `_first_matching_rule()` if the rule shape needs a new field, tests, wheel rebuild, daemon redeploy. Multi-hour ceremony for one event type.

The operator role demands many event types over time: `workflow_run` (failure / success), `pull_request.opened`, `pull_request.closed`, `pull_request_review.submitted`, `push`, `check_suite`, `status`, `release`, `deployment_status`, etc. Doing them one at a time compounds. The substrate should make adding rules a TOML edit, not a code edit.

## Design

### Core change

Replace `_first_matching_rule()`'s isinstance-based field filtering with a **generic dotted-path resolver** that walks the raw GitHub JSON payload. The Pydantic class hierarchy collapses to a single `GitHubEvent` that holds `event_type`, `action`, `repo_full_name`, and `raw: dict` (the full payload).

### AllowRule shape v2

```toml
[[allow]]
rule_id = "ci_failed_main_foreman"
event = "workflow_run_completed"
repo = "jeffrichley/foreman"
match = { "workflow_run.conclusion" = "failure", "workflow_run.head_branch" = "main" }
tier = "red"
reason = "main CI failed on foreman"
```

Fields:
- **`rule_id`** — unique within the file (existing constraint preserved).
- **`event`** — synthetic composite key. Composed as `f"{event_type}_{action}"` when action is present; just `event_type` when not (e.g. `push`, `ping`, `create`).
- **`repo`** — top-level convenience filter equivalent to `match = { "repository.full_name" = "..." }`. Most rules need it; promoting it to a top-level field keeps the common case readable.
- **`match`** — dict of `dotted.path` → `expected_value`. All entries must match (AND semantics). Missing path → rule does not match.
- **`tier`** — `red` | `yellow` | `green`.
- **`reason`** — non-empty string, audit-log justification.

### Match semantics

- **Path resolution.** Each key is a dot-separated path into the raw payload dict. Each path component is a dict key — no array indexing in v2.
- **Value comparison.** Exact equality. TOML parser preserves types (string vs number vs bool); we compare with `==`.
- **All-of.** All key-value pairs in `match` must succeed. If any path doesn't resolve, the rule does NOT match.
- **First-match-wins.** Unchanged from v1.a. Rules evaluated top-to-bottom; first match wins.

### Event-key composition

For every incoming event, the connector computes one synthetic key:

| `X-GitHub-Event` | `action` field | synthetic key |
|---|---|---|
| `pull_request` | `opened` | `pull_request_opened` |
| `pull_request` | `review_requested` | `pull_request_review_requested` |
| `pull_request_review` | `submitted` | `pull_request_review_submitted` |
| `issues` | `labeled` | `issues_labeled` |
| `workflow_run` | `completed` | `workflow_run_completed` |
| `push` | _(no action field)_ | `push` |
| `ping` | _(no action field)_ | `ping` |
| `create` | _(no action field)_ | `create` |

This mirrors the existing v1.a convention but extends uniformly to all 75 events.

### Event parsing — `funnel_handler.py`

The current handler dispatches based on `X-GitHub-Event` to typed Pydantic classes. v2 collapses to one parser:

```python
def _parse_event(headers: dict[str, str], body: bytes) -> GitHubEvent:
    event_type = headers.get("X-GitHub-Event", "")
    payload = json.loads(body)
    return GitHubEvent(
        event_type=event_type,
        action=payload.get("action", ""),
        repo_full_name=payload.get("repository", {}).get("full_name", ""),
        raw=payload,
    )
```

Typed subclasses (`GitHubPullRequestReviewRequestedEvent`, etc.) are **deleted**. They were never load-bearing for anyone outside the connector.

### Dotted-path resolver

```python
def resolve_path(payload: dict, path: str) -> Any | _MISSING:
    cursor = payload
    for key in path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return _MISSING
        cursor = cursor[key]
    return cursor

def _rule_matches(rule: AllowRule, event: GitHubEvent) -> bool:
    if rule.event != _event_key(event):
        return False
    if rule.repo is not None and rule.repo != event.repo_full_name:
        return False
    for path, expected in (rule.match or {}).items():
        actual = resolve_path(event.raw, path)
        if actual is _MISSING or actual != expected:
            return False
    return True
```

### Backward compatibility — v1.a TOML shape still works

v1.a allows the convenience fields `reviewer`, `label_name`, `body_contains` directly on the rule. v2 honors these as **syntactic sugar** — a model validator translates them into the corresponding `match` entries:

| v1.a shortcut | v2 equivalent |
|---|---|
| `reviewer = "wrenrichley"` | `match = { "requested_reviewer.login" = "wrenrichley" }` |
| `label_name = "foreman:needs-help"` | `match = { "label.name" = "foreman:needs-help" }` |
| `body_contains = "TRIGGER"` | _Deprecated; substring match cannot be expressed via dotted-key equality. Operators using this rewrite to `match` with exact equality, or we add `match_contains` in v2.1._ |

The `reviewer` and `label_name` shortcuts keep working forever (cheap to maintain, doc them as discouraged). `body_contains` raises a deprecation warning on load and migrates the rule with `match = { "comment.body" = "<value>" }` (exact-equality, which is wrong for most cases) — operators must rewrite if they need substring semantics. v1.a ships zero `body_contains` rules in production, so this is a paper-cut not a real migration.

### Wren's allowance TOML — migrated rules

```toml
# ~/.wren/.config/inbound/github-allowance.toml

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

7 rules, all 3 projects covered. Operators can add more (release events, deployment status, dependabot alerts) by editing the file — no code change, no daemon restart (mtime reload).

### Webhook subscription

Each registered repo's GitHub webhook subscribes to **all events** (single checkbox in GitHub UI; via API: `events: ["*"]`). The connector's deny-by-default behavior drops anything without a matching rule. Subscribing to everything makes adding new rules a zero-touch op on the GitHub side — we never have to revisit the webhook config to enable a new event.

### Audit log

Unchanged from v1.a. Records `{ts, source, to, verdict, tier, rule_id, reason}` on allow; `{ts, source, to, verdict}` on deny. No payload-field exposure beyond the rule_id and reason.

### Bus envelope

Unchanged from v1.a. `Notification` envelope still carries `kind, source, reason, landed_at, poll_discovered_at, body`. The body is the raw payload (same as v1.a — operators may want every field downstream).

## Out of scope (deferred to v2.1+)

- **JMESPath / array indexing** in dotted paths. v2 supports flat dot-access only. If a rule needs `pull_request.assignees[0].login`, defer.
- **Substring / regex match.** Only exact equality in v2.
- **Wildcard repo match** (`repo = "jeffrichley/*"`). Defer until >10 projects.
- **Per-rule rate-limit overrides.** All rules share the connector-level token bucket.
- **Non-GitHub connectors.** Gmail (v1.b) and Calendar (v1.c) per original spec; separate work.

## Test strategy

**Unit:**
- `AllowRule` model validator translates v1.a shortcuts to `match` entries
- `resolve_path()` handles missing keys, nested dicts, mixed types, deep paths (5+ levels)
- Event-key composition: action-bearing vs action-less events; missing action defaults to `""`

**Connector:**
- First-match-wins preserved (same as v1.a)
- AND semantics across `match` entries
- Loading v1.a-shape TOML produces functionally identical classify() behavior
- Deny when synthetic event key doesn't match any rule
- Deny when path resolves but value differs

**Integration (router):**
- v1.a's existing test (PR review_requested) keeps passing without TOML changes
- New: `workflow_run.completed` w/ `conclusion=failure` → Allow with the right rule_id
- New: `pull_request.opened` → Allow when rule has only `event` + `repo` (no `match`)
- New: `pull_request.opened` on unregistered repo → Deny

**End-to-end smoke:**
- Existing PR review_requested smoke continues to pass after deploy (no Funnel/webhook config change)
- New: trigger a CI failure on foreman main → audit-log line + RED envelope in Wren's inbox

## Migration / rollout

1. Implement v2 on `feat/inbound-v2-schema-flexible-events` branch.
2. Land via PR, follow same deploy path as v1.a (build wheels, uv pip install into prod venv, restart daemon).
3. **No allowance TOML change needed** for the smoke to keep working — backward-compat layer translates the existing `reviewer = "wrenrichley"` rule.
4. After deploy, write the expanded 7-rule TOML in this spec.
5. Update each repo's webhook to subscribe to `*` (all events).

The daemon restart is the only operator-visible disruption — same blast radius as v1.a's deploy. Backward compat means existing rules don't break mid-deploy.

## Open questions for Jeff

1. **Naming.** I've called this "v2" of the inbound substrate; technically v1.b and v1.c are reserved for Gmail/Calendar per the original spec. Want this called v2 (substrate refactor), or v1.a-phase-2 (extension)? My vote: v2 is clearer.
2. **`body_contains` deprecation.** No production rules use it; the cleanest path is to remove it outright and raise on load. Acceptable, or keep the deprecation warning path?
3. **Webhook subscription to `*`.** Confirms operator intent that any event can flow in; the deny-by-default protects us. Concur?

## References

- v1.a design spec: `docs/superpowers/specs/2026-06-20-inbound-notifications-design.md`
- v1.a implementation plan: `docs/superpowers/plans/2026-06-20-inbound-notifications-v1a-implementation.md`
- v1.a runbook: `packages/agent-core-inbound/README.md`
- GitHub webhook events catalog: https://docs.github.com/en/webhooks/webhook-events-and-payloads
- v1.a code: `packages/agent-core-inbound/src/agent_core_inbound/{github_connector,github_event,github_allowance,funnel_handler}.py`
