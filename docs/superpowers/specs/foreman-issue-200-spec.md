# Spec: v2.1 connector-default body projection — trim Notification payload (issue #200)

## Goal

Replace `payload.body = event.raw` in `Router.receive()` with a trimmed, per-event-type projection sourced from `GitHubConnector.project()`. After this change a `pull_request.review_requested` Notification body is ≤ 1 KB instead of ≥ 66 KB, and every body carries `rule_id`, `tier`, and `reason` so downstream consumers do not need to cross-reference the audit log. See [issue #200](https://github.com/jeffrichley/agent_core/issues/200).

## Acceptance criteria

- `Notification` body for a `pull_request.review_requested` event is ≤ 1 KB and contains all of: `rule_id`, `reason`, `tier`, `event_type=pull_request`, `action=review_requested`, `repo`, `html_url`, `landed_at`, `number`, `pull_request.title`, `pull_request.user.login`, `pull_request.head.ref`, `pull_request.base.ref`, `requested_reviewer.login`.
- `Notification` body for a `workflow_run.completed` event contains `workflow_run.id`, `workflow_run.name`, `workflow_run.conclusion`, `workflow_run.head_branch`, `workflow_run.html_url`, `workflow_run.event` and is ≤ 1 KB.
- `Connector` Protocol gains `project(event: ConnectorEvent) -> dict[str, Any]` with a concrete default body returning `dict(event.raw)`. The `@runtime_checkable isinstance()` check continues to pass for `FakeConnector` (which does not implement `project`) because concrete Protocol methods are not part of the structural check.
- `Router.receive()` calls `project()` via a `getattr` fallback (parallel to the existing `_extract_rule_id` pattern) so connectors without `project` silently fall back to `event.raw`, and `FakeConnector` needs no modification.
- `Router.receive()` merges `rule_id`, `reason`, `tier` into the projected dict before publishing.
- All existing tests pass unchanged. The existing `test_allow_publishes_notification_envelope` is updated to assert the new body shape (includes `rule_id`, `tier`, `reason`).
- New tests in `tests/test_github_connector.py` cover every event_type in `_PROJECTIONS` (pull_request, workflow_run, issues, issue_comment, push, release, status); each asserts expected fields are present and the large nested raw fields absent.
- `comment.body` is truncated to 200 characters in `issue_comment` projections.
- `just check` exits zero (ruff + mypy + pytest with 85 % coverage gate).
- `README.md` step 2 gains a 2–3 sentence note on body projection.

## Approach

**Pattern naming.** The design separates classification policy from body shaping, applying **SRP** (Single Responsibility Principle) at the Connector level: `classify()` answers allow/deny, `project()` answers "what fields does the consumer see?" The `Connector` Protocol's concrete default body for `project()` is the **Null Object** pattern for backward compatibility — connectors that haven't filled in a projection return the full raw payload, which is strictly correct if verbose. Pattern-fishing further than this would add noise without insight.

**`project()` in the Protocol.** Adding a method with a concrete body (`return dict(event.raw)`) to a `@runtime_checkable Protocol` means the method is NOT included in the structural `isinstance()` check (Python skips non-abstract members). `FakeConnector` therefore continues to satisfy `isinstance(obj, Connector)` without modification. The Router calls `project` via `getattr(connector, "project", None)` with a lambda fallback — identical to the existing `_extract_rule_id` pattern in `router.py:190` — so callers that have `project` use it and those that don't silently fall back to `event.raw`.

**Projected body key format.** The projection table uses dotted paths (e.g., `pull_request.title`, `requested_reviewer.login`). Rather than reconstructing nested dicts, the projection stores the dotted path string as the flat key. This is consistent with the matching engine's convention (operators already read `match = { "pull_request.title" = "..." }` in TOML), keeps the body dict flat and easy to serialize, and avoids a recursive merge helper. Consumers read `body["pull_request.title"]` not `body["pull_request"]["title"]`.

**Universal fields.** `GitHubConnector._universal_fields(event)` returns `event_type`, `action`, `repo` (from `event.repo_full_name`), `landed_at` (ISO-formatted), and `html_url` extracted from a per-event-type path looked up in `_URL_PATHS`. GitHub webhooks never place a URL at the top-level `html_url` key — the URL lives inside the primary object (e.g., `pull_request.html_url` for `pull_request` events, `workflow_run.html_url` for `workflow_run` events). `_URL_PATHS: dict[str, str]` maps each event_type to the correct dotted path, and `_universal_fields()` calls `resolve_path` with that path to extract the value. `html_url` is omitted only if the event_type is absent from `_URL_PATHS` (not the case for any type in the initial set). These form the baseline every projection starts from.

**Router merges router-scope fields.** `rule_id`, `reason`, and `tier` are Router/verdict-scope concepts unknown to the connector at projection time. The Router merges them into the body dict after calling `project()`. The existing `payload.reason` and `payload.landed_at` fields at the envelope top level are **not removed** (backward compat), but they now also appear inside `payload.body`.

**`comment.body` truncation.** A companion `_TRUNCATIONS: dict[str, int]` module-level constant in `github_connector.py` maps dotted-path field names to max-length integers. After `resolve_path` returns a string value, the projection loop checks `_TRUNCATIONS` and slices if needed. This is the only field requiring truncation in the initial set; the constant makes future additions a one-liner.

**File layout reuse.** The implementation reuses `_path.resolve_path` and `MISSING` exactly as `_first_matching_rule()` already does — no new import needed in `github_connector.py`.

## Sub-requests (topologically sorted)

1. **Add `project()` to `Connector` Protocol in `protocol.py`.** Import `Any` from `typing`. Add `from typing import Any` (already has `Protocol`). Add method after `classify`:

   ```python
   def project(self, event: ConnectorEvent) -> dict[str, Any]:
       """Return the body dict for the Notification envelope.

       Default implementation passes ``event.raw`` through verbatim
       (backward-compatible for connectors that have not filled in a
       per-event-type projection yet). ``GitHubConnector`` overrides
       with a trimmed per-event-type table.
       """
       return dict(event.raw)
   ```

   The concrete body means this method is excluded from `@runtime_checkable isinstance()` checks — `FakeConnector` passes without modification.

2. **Add projection table and `project()` to `GitHubConnector` in `github_connector.py`.** Add two module-level constants after the imports:

   ```python
   from typing import Any

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
   ```

   Add `_universal_fields()` module-level helper (NOT a method; takes the event only):

   ```python
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
   ```

   Add `project()` method to `GitHubConnector` (after `rule_id_for`):

   ```python
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
   ```

3. **Update `Router.receive()` in `router.py`** to call `project()` via `getattr` fallback and merge `rule_id`, `tier`, `reason` into the body. Add a `_project_body()` static helper immediately above `_extract_rule_id`:

   ```python
   @staticmethod
   def _project_body(
       *,
       connector: Connector,
       event: ConnectorEvent,
       rule_id: str,
       verdict: Allow,
   ) -> dict[str, Any]:
       """Build the Notification body dict.

       Calls connector.project(event) when the connector exposes it;
       falls back to dict(event.raw) for connectors that pre-date the
       projection interface (e.g. FakeConnector). Merges router-scope
       fields rule_id, tier, reason so consumers need not cross-reference
       the audit log.
       """
       project_fn = getattr(connector, "project", None)
       projected: dict[str, Any] = (
           project_fn(event) if callable(project_fn) else dict(event.raw)
       )
       return {
           "rule_id": rule_id,
           "reason": verdict.reason,
           "tier": verdict.tier.value,
           **projected,
       }
   ```

   In `receive()`, replace the existing `_bus_publish` call (currently at lines 160–171):
   ```python
   # OLD
   self._bus_publish(
       to=target_being,
       kind="Notification",
       payload={
           "kind": "Notification",
           "source": connector.name,
           "reason": verdict.reason,
           "landed_at": event.landed_at.isoformat(),
           "body": event.raw,
       },
       urgency=verdict.tier.value,
   )
   ```
   with:
   ```python
   # NEW
   body = self._project_body(
       connector=connector,
       event=event,
       rule_id=rule_id,
       verdict=verdict,
   )
   self._bus_publish(
       to=target_being,
       kind="Notification",
       payload={
           "kind": "Notification",
           "source": connector.name,
           "reason": verdict.reason,
           "landed_at": event.landed_at.isoformat(),
           "body": body,
       },
       urgency=verdict.tier.value,
   )
   ```

   Add `from typing import Any` import at the top of `router.py` (currently absent).

4. **Update `tests/test_router.py`** — the `test_allow_publishes_notification_envelope` test currently asserts `pub["payload"]["body"] == {"pr_number": 387, "repo": "jeffrichley/foreman"}` at line 87. With `FakeConnector` (no `project()`), the Router falls back to `dict(event.raw)` and then merges `rule_id`, `tier`, `reason`. Replace that single-line assertion with:

   ```python
   body = pub["payload"]["body"]
   assert body["rule_id"] == "pr_review_requested_foreman"
   assert body["tier"] == "red"
   assert body["reason"] == "PR review requested on foreman"
   # Passthrough raw still present (FakeConnector has no project()).
   assert body["pr_number"] == 387
   assert body["repo"] == "jeffrichley/foreman"
   ```

   No other router tests reference `payload["body"]` content directly, so no further router test changes are required.

5. **Add projection tests in `tests/test_github_connector.py`**.  Add a new section `# --- project() ---` after the existing tests. Use inline minimal payloads; each test verifies both the expected fields AND the absence of large raw-only fields. Example structure for the `pull_request` test:

   ```python
   # --- project() ---

   def test_project_pull_request_returns_universal_plus_pr_fields() -> None:
       from datetime import UTC, datetime
       from agent_core_inbound.github_connector import GitHubConnector
       from agent_core_inbound.github_event import GitHubEvent
       from pathlib import Path
       import tempfile, os

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
           landed_at=datetime(2026, 6, 21, 10, 0, 0, tzinfo=UTC),
           event_type="pull_request",
           action="review_requested",
           repo_full_name="jeffrichley/foreman",
           raw=raw,
       )
       with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
           f.write(b"")
           config_path = Path(f.name)
       try:
           conn = GitHubConnector(config_path=config_path)
           body = conn.project(event)
       finally:
           os.unlink(config_path)

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
   ```

   Add one similarly-shaped test for each of: `workflow_run`, `issues`, `issue_comment`, `push`, `release`, `status`. For each test:
   - Construct a minimal `GitHubEvent` with only the fields relevant to that event type plus one "big" decoy field.
   - Assert every expected projected key is present with the right value.
   - Assert the decoy key is absent.

   Add one test for the MISSING-field-skip behavior:
   ```python
   def test_project_skips_missing_optional_fields() -> None:
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
       # ... construct event, connector, call project() ...
       assert "requested_reviewer.login" not in body
   ```

   Add one test for `comment.body` truncation:
   ```python
   def test_project_issue_comment_truncates_comment_body_to_200_chars() -> None:
       raw = {
           "action": "created",
           "repository": {"full_name": "jeffrichley/foreman"},
           "issue": {"number": 7, "title": "Bug", "user": {"login": "bob"},
                     "html_url": "https://github.com/jeffrichley/foreman/issues/7"},
           "comment": {"user": {"login": "alice"}, "body": "x" * 500},
       }
       # ... construct event (event_type="issue_comment"), connector, call project() ...
       assert body["comment.body"] == "x" * 200
       assert len(body["comment.body"]) == 200
   ```

   Add one test for unknown event_type passthrough:
   ```python
   def test_project_unknown_event_type_falls_back_to_raw() -> None:
       raw = {"action": "frobbed", "some_big_key": {"nested": "data"}}
       # event_type="deployment" is not in _PROJECTIONS
       # ... construct event, connector, call project() ...
       assert body == dict(event.raw)
   ```

6. **Update `packages/agent-core-inbound/README.md` step 2** — append after the allowance TOML snippet (after the `body_contains` removal note) a 2–3 sentence note:

   > **Body projection.** The GitHub connector trims the Notification envelope body to a small per-event-type field set (event type, action, repo, key identifiers). This keeps inline bus payloads under 1 KB and avoids bloating tool results with GitHub metadata Wren never uses. The full raw webhook payload is always recoverable from GitHub's webhook delivery history via `gh api repos/<repo>/hooks/<id>/deliveries/<delivery_id>`.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-inbound/src/agent_core_inbound/protocol.py` | Add `from typing import Any`. Add `project(self, event: ConnectorEvent) -> dict[str, Any]` method with concrete default body `return dict(event.raw)`. |
| `packages/agent-core-inbound/src/agent_core_inbound/github_connector.py` | Add `from typing import Any`. Add `_PROJECTIONS` constant, `_TRUNCATIONS` constant, `_URL_PATHS` constant, `_universal_fields()` module-level helper, and `GitHubConnector.project()` method. |
| `packages/agent-core-inbound/src/agent_core_inbound/router.py` | Add `from typing import Any`. Add `Router._project_body()` static method. Replace `"body": event.raw` with `body = self._project_body(...)` in `receive()`. |
| `packages/agent-core-inbound/tests/test_router.py` | Update `test_allow_publishes_notification_envelope` to assert `body["rule_id"]`, `body["tier"]`, `body["reason"]` and the passthrough raw fields instead of asserting `body == event.raw`. |
| `packages/agent-core-inbound/tests/test_github_connector.py` | Add new `# --- project() ---` section with 10 tests: one per event_type in `_PROJECTIONS` (7), one for missing-field skip, one for comment.body truncation, one for unknown event_type passthrough. |
| `packages/agent-core-inbound/README.md` | Add 2–3 sentence body-projection note after the allowance TOML snippet in step 2. |

## Alternatives considered

- **Single flat key using the last path segment** (e.g., `pull_request.title` → `"title"`): Simpler key access, but causes collisions (e.g., `issue.title` and `pull_request.title` would both become `"title"`). Rejected in favor of dotted keys, which are already the project's convention for the matching engine.
- **Reconstruct nested dicts** (e.g., `"pull_request": {"title": "..."}`): More natural JSON, but requires a recursive merge helper and makes it ambiguous whether `body["pull_request"]` is a raw extract or a constructed projection. Rejected for simplicity.
- **Route-level body elision** (auto-elide oversized payloads at the consumer): Solves the symptom (large tool results) without solving the cause (publishing too much). Issue explicitly marks this out of scope. Rejected.
- **TOML-level per-rule field overrides** (`body_fields = [...]` on each `[[allow]]` block): Gives operators fine control but makes projection an operator concern rather than a connector default. The issue explicitly marks this out of scope (YAGNI). Rejected.
- **Store raw payload to disk** (content-addressed by delivery sha): Preserves the full payload but adds a storage system. Original recoverable from GitHub. Issue explicitly marks this out of scope. Rejected.
- **Add `project()` to `FakeConnector` directly**: The `getattr` fallback approach is cleaner — `FakeConnector` keeps working without any modification, and the fallback exercises the default passthrough path exactly as the acceptance criteria require.

## Open questions

None. The codebase conventions are clear, the issue acceptance criteria are unambiguous, and every referenced file has been read and verified against the spec.

## Out of scope

- TOML-level per-rule `body_fields` overrides (explicitly deferred by the issue).
- Out-of-band raw payload storage (explicitly deferred by the issue).
- Channel-layer auto-elision (explicitly deferred by the issue).
- Audit log changes (issue says none needed).
- `match_contains` operator or any other v2.1+ matching extensions.
- Adding projection for GitHub event types not listed in the issue's table (future one-line additions to `_PROJECTIONS`).
- Removing `payload.reason` or `payload.landed_at` from the envelope top level (backward compat preserved).
