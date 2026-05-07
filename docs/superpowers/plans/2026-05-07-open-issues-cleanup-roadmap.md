# Open Issues Cleanup Roadmap (2026-05-07)

> **Scope:** Sequencing plan to close all 15 open issues on `jeffrichley/agent_core` as of 2026-05-07.
>
> **Shape:** This is a *roadmap*, not a per-feature implementation plan. Each issue (or cluster) gets its own implementation plan via the `superpowers:writing-plans` skill when it's picked up. This document orders the work, explains the rationale, names dependencies, and gives realistic effort estimates so Jeff can decide where to spend the next chunks of time.
>
> **Living document.** Update phase status as work lands. Re-rank if priorities shift.

---

## Context

The webcam endpoint shipped to `main` on 2026-05-07 (PR #40), closing the active development branch. The issue tracker has 15 open items, most filed during the Pepper cutover (2026-05-06) when Jeff and the agent walked through the runbook together and surfaced architectural gaps.

The 15 issues fall into four broad categories:

- **Pepper reliability bugs** that affect day-to-day use (2 issues, both tagged `bug`)
- **Foundational gaps** the cutover exposed but didn't block (5 issues — observability, ACLs, expiry, etc.)
- **Future-feature scaffolding** that was always planned but is now well-scoped (5 issues — typed envelope kinds, DLQ, heartbeats, etc.)
- **Polish + docs** (3 issues)

The natural order: **fix what's broken now → close the visibility gaps → build the future-feature scaffolding → polish.** That's what this roadmap proposes.

---

## At-a-glance summary

| Phase | Theme | Issues | Effort | Order |
|---|---|---|---|---|
| 0 | Quick wins (small, mostly independent) | #37, #36, #34, #18 | ~3-5 days | Any time, parallelizable |
| 1 | Pepper reliability bugs | #35, #33 | ~7-10 days | Do first; sequential |
| 2 | Observability foundation | #39, #16 | ~6-8 days | After Phase 1 |
| 3 | User-facing UX (typing + bursts + typed kinds) | #19 → #13 → #14 | ~10-15 days | Sequential within phase |
| 4 | Bus reliability foundation | #17, #15 | ~6-8 days | After Phase 2 (uses observability) |
| 5 | Urgency redesign | #38 | 1-2 weeks (full) or 1 day (sigil-only MVP) | Standalone; defer if Pepper isn't getting false-positives |
| 6 | Documentation hygiene | #23 | 0.5 day | Last; fast |
| **Total** | | **15 issues** | **~10-12 work-weeks if sequential** | |

Effort assumes one focused developer; can compress with parallel workstreams in phases 0, 4, and 6.

---

## Phase 0 — Quick wins (1-day issues, mostly independent)

These are small, well-scoped issues whose fixes are mostly mechanical. They can land any time bandwidth allows; some can run in parallel because they touch unrelated files. **Recommended to land before Phase 1's heavier reliability work** so Jeff can see steady progress while the bug investigations spin up.

### #37 — Bus HTTP MCP host doesn't emit `notifications/tools/list_changed`

**What:** When the bus daemon restarts, connected MCP clients keep their stale tool cache. Currently the workaround is `/exit + relaunch` per agent. Bit us during the 2026-05-06 cutover ("the briefs framework looks broken!" was actually a stale-cache moment).

**Cost:** 0.5-1 day. Touches `packages/core/src/agent_core/bus/http_host.py` + the FastMCP server wrapper.

**Tests to add:**
- `test_http_mcp_host_emits_tools_list_changed_after_wire_endpoints`
- `test_http_mcp_host_no_spurious_tools_list_changed_on_static_registry`
- `test_streamable_http_client_re_enumerates_on_tools_list_changed`

**Branch:** `feat/issue-37-tools-list-changed`

### #34 — Scheduler ACL: ownership check on delete/update/pause

**What:** Currently any caller can mutate any scheduler job. Single-operator setup masks the issue, but it's a security footgun for multi-agent.

**Cost:** 0.5-1 day. Add `created_by` to job records, enforce at the mutation tools.

**Tests to add:**
- `test_scheduler_delete_rejects_when_caller_not_owner`
- `test_scheduler_update_rejects_when_caller_not_owner`
- `test_scheduler_pause_resume_rejects_when_caller_not_owner`
- `test_scheduler_list_jobs_filters_to_caller_by_default`

**Branch:** `feat/issue-34-scheduler-ownership-acl`

### #36 — Discord `AccessConfig`: add `deny_channels` blocklist

**What:** Currently `AccessConfig.channels` is allowlist-only. To make Pepper "respond everywhere except #test" requires enumerating every other channel (or kludging Discord server permissions, as we did 2026-05-06). Add a `deny_channels` field that's checked first.

**Cost:** 0.5 day. Single field addition + gate logic + tests.

**Tests to add:**
- `test_gate_denies_channel_in_deny_list_when_allowlist_empty`
- `test_gate_denies_channel_in_deny_list_when_in_allowlist_too` (deny wins)
- `test_gate_allows_when_in_allowlist_and_not_in_deny_list`
- `test_load_access_config_parses_deny_channels`
- `test_load_access_config_handles_missing_deny_channels` (back-compat)

**Branch:** `feat/issue-36-discord-deny-channels`

### #18 — Bus: enforce `expires_at` on envelopes

**What:** The schema already accepts `expires_at` but the bus doesn't filter on pickup. Stale messages get delivered hours later — confusing-bot moment for any time-sensitive flow.

**Cost:** 1-2 days. Filter at `list_pending`, mark expired envelopes, optional `Expired` envelope back to sender.

**Tests to add:**
- `test_list_pending_filters_expired_envelopes`
- `test_expired_envelopes_state_transitions_to_expired`
- `test_expired_in_flight_envelope_is_not_retroactively_expired`
- `test_optional_expired_notification_to_sender`

**Branch:** `feat/issue-18-enforce-expires-at`

### Phase 0 sequencing

These four can ship as **four separate small PRs** in any order, or batched into a "Phase 0 quick wins" branch. Recommend separate PRs — keeps each fix reviewable on its own, and each closes a discrete issue.

**Phase 0 deliverable:** 4 issues closed, ~3-5 days of work.

---

## Phase 1 — Pepper reliability bugs (do first)

Both bugs in this phase directly affect Pepper's day-to-day reliability. Neither is a one-line fix — each involves investigation before the implementation lands.

### #35 — Handoff pipeline: 4 compounding bugs

**What:** During the cutover, the post-cutover handoff workflow surfaced four interacting bugs that make handoffs unreliable:

1. **Coarse idempotency key** — `f"{session_id}:{event}:{output_path}"` collides across `--continue` resumes, returning stale `job_id`s.
2. **In-memory `_idempotency_index`** — wiped on daemon restart, so identical jobs re-execute after restart.
3. **Worker silent fallback on oversized transcripts** — 18MB transcript causes the bundled Claude SDK summarizer to fail, writes a 268-byte stub, marks state ready.
4. **In-memory state silently lost on daemon restart** — compounds with #2 above.

**Cost:** 5-7 days. Each bug needs investigation + fix + regression test. The four are interleaved enough that fixing them sequentially in a single branch is more efficient than one PR per bug.

**Approach:**
1. Spike: reproduce each bug in isolation with a failing test. Land all four failing tests as the first commit so the regression coverage is locked in before any fix.
2. Fix the idempotency key (richer key, e.g. include transcript hash or content fingerprint).
3. Persist `_idempotency_index` (sqlite-backed) so daemon restart doesn't reset it.
4. Fix the silent fallback (chunk + summarize, or fail loudly with a clear error).
5. Audit other in-memory state for the same restart-fragility pattern (briefs `_sessions`, scheduler dedupe).

**Tests to add:** at least one regression test per bug, plus an end-to-end test that simulates the cutover flow (PreCompact → daemon restart → SessionEnd → resume) and asserts the handoff lands correctly.

**Branch:** `fix/issue-35-handoff-pipeline-reliability`

**Risk:** This branch is the longest pole in Phase 1. Recommend a daily check-in to make sure each of the 4 bugs is actually being addressed (not just one fixed and the others forgotten).

### #33 — Bus wake-builder count + `urgency_max` snapshot lag

**What:** Bug. The wake-notification's `count` and `urgency_max` reflect the queue state at notification-build time, not at delivery time. Pepper can miss red-urgency items or get repeated wakes for the same envelope.

**Cost:** 1-2 days. Re-snapshot at delivery time, or attach the wake metadata to envelope groups instead of single envelopes.

**Tests to add:**
- `test_wake_count_reflects_actual_pending_at_delivery_time`
- `test_urgency_max_reflects_pending_red_envelope`
- `test_wake_does_not_duplicate_for_same_pending_set`

**Branch:** `fix/issue-33-wake-builder-snapshot-lag`

### Phase 1 sequencing

**Recommended:** #35 first (longer, has the worse blast radius), then #33 (smaller, depends on no shared code). Two separate PRs.

**Phase 1 deliverable:** 2 issues closed, ~7-10 days of work.

---

## Phase 2 — Observability foundation (after Phase 1)

Both issues here address the same underlying gap: today, the bus is a black box from outside any single endpoint. Phase 2 makes it inspectable.

**Why after Phase 1:** Once the handoff pipeline is solid, observability lets us catch the *next* round of bugs faster. Doing this before Phase 1 doesn't help fix the known bugs (we already know what they are).

### #39 — Bus HTTP MCP host: log every `tools/call` invocation

**What:** Daemon-wide audit JSONL covering every MCP tool invocation across every endpoint. The webcam endpoint shipped its own local audit log as a stop-gap; this generalizes the pattern.

**Cost:** 2-3 days. Add a hook in `http_host.py`'s `tools/call` dispatch that emits an `AuditEvent` to `~/.agent-core/mcp-audit/<YYYY-MM-DD>.jsonl`. Per-tool args summarizer registry (so sensitive payloads don't leak verbatim).

**Tests to add:**
- `test_http_host_logs_tool_call_invocation`
- `test_http_host_logs_tool_call_error`
- `test_http_host_uses_args_summary_when_registered`
- `test_http_host_uses_default_summary_when_no_summarizer`
- `test_http_host_skip_tools_excludes_named_tools`
- `test_http_host_disabled_emits_nothing`
- `test_http_host_daily_rotation`

**Migration:** Webcam's local audit log can stay (finer-grained domain record) or be removed (now redundant). Decide when this lands.

**Branch:** `feat/issue-39-mcp-tool-call-audit`

### #16 — Read-only bus tail / audit feed for debugging

**What:** A `tail` MCP tool (or HTTP endpoint) that returns recent envelope metadata across all endpoints — for cross-endpoint debugging that today requires log-grep across multiple files.

**Cost:** 4-5 days. Auth-gated (per-endpoint vs admin scope), filterable, optional streaming. Companion: lightweight metrics (counts by kind, queue depth per endpoint, ack latency).

**Tests to add:**
- `test_tail_returns_recent_envelopes_with_metadata`
- `test_tail_filters_by_from_to_kind_urgency`
- `test_tail_payload_redacted_by_default`
- `test_tail_full_payload_admin_only`
- `test_tail_streaming_emits_envelopes_live`
- `test_metrics_count_by_kind_is_accurate`

**Branch:** `feat/issue-16-bus-tail-audit-feed`

### Phase 2 sequencing

#39 first (smaller, more foundational — every other observability tool benefits from having tool-call records). #16 second (uses the same persistence patterns; can reference the `daily_raw_jsonl` design).

**Phase 2 deliverable:** 2 issues closed, ~6-8 days of work.

---

## Phase 3 — User-facing UX (typing + bursts + typed kinds)

These three issues are tightly coupled. #19 establishes the typed-envelope vocabulary; #13 uses one of those types (`Status`) to fix the typing-indicator UX; #14 collapses bursts using the same notification surface.

### #19 — Define typed command envelope kinds: `Reaction`, `Edit`, `DeleteMessage`, `DM`, `Status`

**What:** Define typed payload schemas for richer agent → bridge interactions beyond `TextMessage`. Each kind has a JSON schema; bridges register handlers per-kind they support.

**Cost:** 1-2 weeks. Multi-kind schema design + bridge dispatch routing + per-bridge registration. The schema design is the long pole — payload shapes need to work cross-platform (Discord, Slack, email) without leaking platform specifics into the agent layer.

**Sub-tasks:**
1. Schema design for each of the 5 kinds (1-2 days, design pass)
2. Bridge dispatch + per-kind handler registration (2 days)
3. `Reaction` end-to-end on Discord (1-2 days; reactions are well-defined)
4. `Edit` end-to-end on Discord (1 day; pairs with #13)
5. `DM` end-to-end on Discord (1 day)
6. `DeleteMessage` (1 day)
7. `Status` (1 day; minimal — actual UX uses are in #13 and #14)
8. `describe_endpoint` extension to advertise `supported_kinds` (0.5 day)

**Tests to add:** per-kind acceptance/rejection tests, bridge dispatch tests, end-to-end Discord tests using a fake Discord client.

**Branch:** `feat/issue-19-typed-envelope-kinds`

### #13 — Typing indicator TTL + placeholder + edit pattern

**What:** Cap the "X is typing…" indicator (currently stays up indefinitely on long composes). Better UX: typing → placeholder message → edit with final reply. Uses `Status` envelopes from #19.

**Cost:** 2-3 days *after #19 lands*. Without #19's `Status` and `Edit` kinds, the cost would be ~5 days because the typing logic would have to invent a private convention.

**Tests to add:**
- `test_typing_indicator_clears_after_ttl`
- `test_placeholder_posted_after_typing_ttl_expires`
- `test_placeholder_edited_to_final_reply_when_ready`
- `test_placeholder_edited_to_failure_message_on_timeout`
- `test_status_envelope_drives_placeholder_text`

**Branch:** `feat/issue-13-typing-placeholder-edit`

### #14 — Bus/bridge: collapse rapid same-sender envelope bursts

**What:** Two messages from the same user 2 seconds apart currently fire two wake notifications. Fold same-sender same-kind envelopes within a short window into one notification.

**Cost:** 1-2 days. Independent of #19 / #13 — but pairs naturally because all three are about wake-volume / chat-paced UX.

**Tests to add:**
- `test_burst_window_collapses_two_envelopes_into_one_notification`
- `test_red_urgency_bypasses_burst_collapse`
- `test_window_resets_after_quiet_period`
- `test_per_endpoint_window_configuration`

**Branch:** `feat/issue-14-collapse-burst-notifications`

### Phase 3 sequencing

**Recommended:** #19 first (its `Status` and `Edit` kinds are dependencies for #13's full version). Then #13. #14 can be done in parallel with #13 since it doesn't share files.

**Phase 3 deliverable:** 3 issues closed, ~10-15 days of work.

---

## Phase 4 — Bus reliability foundation

These two issues address bus-layer reliability that hasn't bitten loudly yet but is structural debt. They're also good candidates for the new observability tools from Phase 2 — the audit feed makes it easier to *see* whether DLQ and heartbeats are doing their job.

### #17 — Bus: dead-letter queue + retry policy + poison-message handling

**What:** Today, `nack(envelope_id, requeue=True)` puts an envelope back forever — no retry limit, no backoff, no DLQ. Add standard message-bus DLQ semantics: counter, max retries, dead-letter table, replay tooling.

**Cost:** 4-5 days. Schema additions + DLQ table + replay/inspect/purge tools + per-kind retry config.

**Tests to add:**
- `test_envelope_moves_to_dead_letter_after_max_retries`
- `test_exponential_backoff_between_requeues`
- `test_per_kind_retry_limits_respected`
- `test_dead_letter_replay_resets_attempts`
- `test_dead_letter_purge_removes_envelope`
- `test_dead_letter_visible_in_audit_feed` (Phase 2 dependency)

**Branch:** `feat/issue-17-bus-dlq-retry-policy`

### #15 — Bus: endpoint heartbeats + liveness for `list_endpoints`

**What:** Today `list_endpoints()` returns who's *registered*, not who's *alive*. Add `last_seen` + derived `status: live | stale | dead` based on activity timestamps.

**Cost:** 2-3 days. Activity-based heartbeats (passive — every send/list/handle marks the endpoint live), threshold config, optional `require_live=true` on `send`, optional `Undelivered` envelope when sending to a dead endpoint.

**Tests to add:**
- `test_endpoint_marked_live_on_send`
- `test_endpoint_marked_live_on_list_pending`
- `test_endpoint_status_transitions_to_stale_then_dead`
- `test_send_to_dead_endpoint_with_require_live_fails_fast`
- `test_undelivered_envelope_back_to_sender_after_ttl`

**Branch:** `feat/issue-15-endpoint-heartbeats-liveness`

### Phase 4 sequencing

**Recommended:** parallel — neither depends on the other. Two separate PRs.

**Phase 4 deliverable:** 2 issues closed, ~6-8 days of work.

---

## Phase 5 — Urgency detection redesign

### #38 — Discord adapter urgency detection: replace 3-word regex with layered signals

**What:** Current regex (`urgent|now|stop`) produces false positives ("right now we are looking at..." → red). Replace with layered signals: explicit sigil prefix, sender-map defaults, channel-of-origin defaults.

**Cost:**
- **MVP (sigil only):** 1 day. `!` prefix → red, `?` → yellow, `~` → green. Parse on inbound; strip from message body before delivery.
- **Full layered design:** 1-2 weeks. Sigil + sender map + channel map + (optionally) embedding-similarity classifier.

**Recommendation:** Ship MVP first (closes the immediate annoyance), defer the full layered design until usage shows the MVP isn't enough.

**Tests to add (MVP):**
- `test_sigil_prefix_promotes_urgency`
- `test_sigil_prefix_strips_from_message_content`
- `test_no_sigil_falls_through_to_green`

**Tests to add (full):** the above plus sender-floor + channel-default + max-of-signals composition tests, listed in the issue body.

**Branch:** `feat/issue-38-urgency-redesign-mvp` (MVP) or `feat/issue-38-urgency-layered` (full).

### Phase 5 deliverable: 1 issue closed, 1 day (MVP) or 1-2 weeks (full).

**Decision point for Jeff:** MVP-only or full layered? If Pepper isn't getting pinged on false-positives often, MVP is fine. If false positives are frequent, full design earns its cost.

---

## Phase 6 — Documentation hygiene

### #23 — Discord bridge: document ack contract + chunk-limit edge semantics

**What:** Recent send-reliability PR (#26/#28) introduced `message_id` vs `message_ids`, `status=sent|partial`, urgency on errors, and `MAX_CHUNKS` refusal — none of which are documented for agent consumers. Pure docs work.

**Cost:** 0.5 day. Add a `## Ack contract` section to `packages/agent-core-discord/README.md` (or wherever endpoint docs live), tests with explicit assertions, regression tests.

**Branch:** `docs/issue-23-discord-ack-contract`

### Phase 6 deliverable: 1 issue closed, 0.5 day.

---

## Execution conventions

For each branch:

1. **One issue per branch.** Branch names follow `feat/issue-NN-<slug>` or `fix/issue-NN-<slug>` per category.
2. **Implementation plan per branch.** Use the `superpowers:writing-plans` skill before starting code on each issue. The per-issue plan goes in `docs/superpowers/plans/` next to this roadmap.
3. **Subagent-driven implementation.** Use `superpowers:subagent-driven-development` for execution per the validated 2026-05-06 webcam pattern.
4. **PR per issue.** Standard `gh pr create --base main` flow. CI is currently no-op for this repo, so the gate is the test suite + spec compliance review + code quality review.
5. **Close the issue from the PR body.** Use `Fixes #NN` so GitHub auto-closes on merge.
6. **Update this roadmap as work lands.** Mark phase status (✅ done / 🚧 in progress / ⬜ not started) and add the merged PR number next to each issue.

## Effort + timeline summary

| Phase | Effort (sequential) | Effort (with parallelism) |
|---|---|---|
| Phase 0 — Quick wins | 3-5 days | 2 days |
| Phase 1 — Reliability bugs | 7-10 days | 7-10 days (sequential by design) |
| Phase 2 — Observability | 6-8 days | 5-6 days |
| Phase 3 — UX (typed kinds + typing + bursts) | 10-15 days | 8-12 days |
| Phase 4 — Bus reliability | 6-8 days | 4-5 days |
| Phase 5 — Urgency redesign | 1 day (MVP) or 1-2 weeks (full) | same |
| Phase 6 — Docs | 0.5 day | 0.5 day |
| **Total** | **~35-50 days sequential** | **~28-40 days with parallel branches** |

Calling it ~10-12 work-weeks if executed full-time without context switches. Realistic timeline if Pepper-driven priorities continue to interleave: 3-4 calendar months.

## Decision points for Jeff

These are places where this roadmap takes a position that you may want to override:

1. **Phase 0 first or Phase 1 first?** This roadmap puts Phase 0 first because the wins are immediate and the cost is small. If you'd rather see the user-facing bugs (#35, #33) addressed before any other work, swap Phase 0 and Phase 1.
2. **Phase 5 MVP vs full.** Defer the call until you see how often Pepper hits the urgency false-positive. If once a month, MVP. If once a week, full.
3. **#19's depth.** Do all 5 envelope kinds at once, or ship them one at a time? Doing them all at once means a longer branch but a single review pass for the dispatch architecture; one-at-a-time means earlier shipping but multiple bridge-routing rewrites.
4. **Webcam audit log retention after #39.** Once daemon-wide MCP audit lands, decide whether to drop the per-endpoint webcam audit (redundant) or keep it (finer-grained domain record). No urgency.
5. **Parallel vs sequential phases.** Phases 0, 4, and 6 contain naturally parallel work. If a second contributor (or a second focused work block from you) is available, those compress meaningfully. Phases 1, 3, and 5 are sequential by their dependency graph.

## Risks + contingency

- **#35 might surface a fifth bug during investigation.** The handoff pipeline is the most fragile area; the 4 known bugs may not be exhaustive. Budget for a 5-7 day branch with a +2 day buffer. If a fifth bug is found, file a new issue and either bundle into the same PR (if related) or a follow-up PR (if independent).
- **#19's schema design might bog down.** Cross-platform payload design (Discord vs Slack vs email) is a real rabbit hole. If schema review takes more than 2 days, ship `Reaction` + `Edit` + `Status` first (the three needed to unblock #13) and defer `DM` + `DeleteMessage` to a follow-up.
- **Phase 2's audit infrastructure might surface unrelated bugs.** Once `tools/call` is logged, expect to find at least one "wait, that tool is being called way more than it should be" finding. Treat as bonus discoveries, file new issues, don't let them derail the audit work.
- **Pepper-driven priorities will interleave.** The roadmap assumes focused work blocks. In reality, Pepper using the system day-to-day will surface new issues that take precedence. Build in a 20% buffer for that.

## Status tracking

Each phase's status is tracked here. Update as work lands.

| Phase | Status | Started | Completed | Notes |
|---|---|---|---|---|
| 0 — Quick wins | ⬜ Not started | — | — | |
| 1 — Reliability bugs | ⬜ Not started | — | — | |
| 2 — Observability | ⬜ Not started | — | — | |
| 3 — UX | ⬜ Not started | — | — | |
| 4 — Bus reliability | ⬜ Not started | — | — | |
| 5 — Urgency redesign | ⬜ Not started | — | — | |
| 6 — Docs | ⬜ Not started | — | — | |

## Related

- Repo issue tracker: https://github.com/jeffrichley/agent_core/issues (open issues as of 2026-05-07)
- Strategic vision: `docs/ROADMAP.md`
- Deferred design items: `docs/BACKLOG.md`
- Per-feature plans: `docs/superpowers/plans/`
- Recent shipped work: 2026-05-06 cutover (`docs/cutover/pepper-flip-2026-05-06.md`), 2026-05-07 webcam (PR #40)
