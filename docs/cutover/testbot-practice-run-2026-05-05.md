# Testbot practice run — 2026-05-05

**Goal:** validate all 9 cutover playbooks end-to-end on testbot **before flipping Pepper**.

**Stop rule:** any RED step pauses the run. We diagnose before continuing — no "we'll come back to it." Pepper stays untouched until every step here is GREEN.

**Whose responsibility:**
- I (Claude) drive the file edits, test runs, and reporting.
- You (Jeff) drive the bits that need a human: starting the daemon, opening Claude Code in `~/.testbot/`, observing real-Pepper-style behavior, deciding on Discord.

---

## Phase 0 — Pre-flight setup

Manual setup since there's no `agent-core init` yet. Each item is small; the time is in being careful.

### 0.1 — Author `~/.testbot/agent_core.yaml` and extend daemon config — **DONE ✓**

- [x] Created per-project hooks file at `~/.testbot/agent_core.yaml` (pipelines block: SessionStart → time + identity x2 + handoff; UserPromptSubmit → time-with-track-session; PreCompact + SessionEnd → handoff_writer)
- [x] Extended daemon config at `~/.agent-core/agent_core.yaml`: added `handoff-jobs` endpoint, `briefs.orchestrator` endpoint, `briefs_orchestrator: briefs.orchestrator` cross-endpoint param on the testbot MCP, and `bus_hooks.pre_publish` daily JSONL writer
- [x] Daemon restart caught a real cutover-blocker bug — fixed in `1a38463` (new `reserved_endpoint_params` hookspec; runner pops plugin-managed params before endpoint construction)
- [x] Daemon now boots clean with all 6 endpoints registered (handoff-jobs, agent-testbot MCP, briefs.orchestrator, scheduler, stub, discord-testbot)

**Bug caught:** `ClaudeCodeMCPEndpoint.__init__()` rejected `briefs_orchestrator` as an unknown kwarg. The runner was forwarding all `params:` keys to `__init__()` without filtering plugin-managed keys. Pepper would have hit this on day one of cutover. Fixed properly with a new pluggy hookspec; 666 tests + 3 skipped, all green; positive test (`test_reserved_endpoint_params_pop_before_construction`) locks the contract.

### 0.2 — Stand up `~/.testbot/Memory/` skeleton — **DONE ✓**

- [x] SOUL.md (testbot voice — blunt-honest QA engineer, ~200 words; satisfies cutover #01 rule 1)
- [x] IDENTITY.md (six identity rules from cutover #01 spec)
- [x] MEMORY.md (empty index)
- [x] playbooks/morning_brief.md (copy of example, voice changed to `testbot`, channel_id set to `1499028901257805874`)
- [x] gather/morning.yaml (copy of example; built-in fetchers + email_stub will resolve, gcalcli/projects-yaml will land in `_errors` — that's the gather engine's intended "fetcher failed" semantic)
- [x] daily/summaries/, daily/briefs/, testbot/, briefs/fetchers/, briefs/destinations/ — all created
- [x] email_stub fetcher copied into briefs/fetchers/ so testbot's fetcher path resolves cleanly
- [x] Daemon restarted; no missing-path warnings; all 4 fetchers (`cli`, `email_stub`, `filesystem_read`, `now`) discoverable via `briefs fetchers list`

**Bug caught:** `briefs fetchers list` CLI didn't auto-prepend the built-in fetchers directory — it would have shown 0 built-ins at the gate, failing #09 Step 3. Fixed in `4c4b7bd`. Same root cause as yesterday's orchestrator fix; the CLI was missed.

### 0.3 — Wire `~/.testbot/.claude/settings.json` hooks — **DONE ✓**

- [x] Added `hooks` block with all four lifecycle events (SessionStart, UserPromptSubmit, PreCompact, SessionEnd)
- [x] Commands match Pepper's pattern (`uv run agent-core hooks run <event>`)
- [x] Permissions widened to match Pepper's `bypassPermissions` mode + standard tool allowlist; existing `mcp__agent-core__*` permissions preserved
- [x] Smoke test from `~/.testbot/`: SessionStart hook fires 4 tools (time, identity x2, handoff) and emits clean JSON `additionalContext` with testbot's SOUL + IDENTITY content
- [x] UserPromptSubmit smoke fires the time tool with track_session

**Bug caught:** the globally-installed `agent-core` (via `uv tools`) was stale — still used the old `tool:` schema while the repo had moved to `type:`. Pepper would have hit this on the very first SessionStart hook firing on the new substrate. Fixed by `uv tool install --reinstall ./packages/core` to bring the global tool current with the repo. This is a **third real cutover-blocker** caught by the practice run.

### 0.4 — Install skills (manual workaround for missing agent init) — **DONE ✓**

- [x] Copied `briefs-author/SKILL.md` → `~/.testbot/.claude/skills/briefs-author/SKILL.md` (project-scope, per-agent)
- [x] Copied `email/SKILL.md` → `~/.testbot/.claude/skills/email/SKILL.md`
- [x] Both skills now visible to testbot's Claude Code session (project-scope; never user-scope)

### 0.5 — Stage scheduler entry for morning_brief — **DONE ✓**

- [x] Wrote `~/.agent-core/jobs.yaml` with `testbot-morning-brief` JobDef:
  - `trigger: cron`, `schedule: { hour: 23, minute: 59 }` (far-future placeholder; bump at Phase 3.4)
  - `target: briefs.orchestrator`
  - `envelope_kind: Event`, `payload.type: BriefRequest`, `payload.data.brief_type: morning_brief`
- [x] Daemon restarted; log confirms `Seeded job: testbot-morning-brief` with next run at 23:59
- [x] Bus has 6 endpoints registered + scheduler running

### 0.6 — Discord endpoint for #03 — **DONE ✓**

Testbot's Discord app + token + channels already existed from prior validation work. The `discord-testbot` endpoint was already wired in `~/.agent-core/agent_core.yaml` before today's run.

- [x] `DISCORD_TESTBOT_TOKEN` env var loaded from `~/.agent-core/discord-testbot.env` (confirmed in daemon log: `loaded env file: ~/.agent-core/discord-testbot.env`)
- [x] `discord-testbot` endpoint registered in daemon yaml with `target: agent-testbot`
- [x] Discord gateway connected: daemon log shows `Shard ID None has connected to Gateway`
- [x] `DiscordEndpoint(name=discord-testbot) started; target=agent-testbot, attachments=...`
- [x] Test channel for Phase 6 verb-parity drive: `1499028901257805874` (also wired into the morning_brief playbook's `discord_embed` destination)

### 0.7 — Daemon boot — **DONE ✓**

- [x] Daemon running (PID 29284 after final restart for jobs.yaml seed-load)
- [x] `agent-core daemon status` confirms running
- [x] All 6 endpoints registered: handoff-jobs, agent-testbot MCP, briefs.orchestrator, scheduler, stub, discord-testbot
- [x] HTTPHost listening on 127.0.0.1:8789 with 2 mounts (/internal/handoff-jobs + /mcp/agent-testbot)
- [x] `testbot-morning-brief` seed job loaded (next run 23:59 — we'll bump for Phase 3.4)

---

## Phase 1 — Hook + identity gate (#01, #02, #07)

These three share the SessionStart pipeline; testing them in sequence reuses the warm setup.

### 1.1 — CLI hook smoke (#01 Step 2) — **DONE ✓**

```powershell
echo '{}' | uv run agent-core hooks run SessionStart --config C:\Users\jeffr\.testbot\agent_core.yaml
```

**Expected:** JSON with `additionalContext` containing testbot's SOUL/IDENTITY content, no `Pepper` text leaking from the framework, no truncation cap. **Pass = readable JSON, content present.**

- [x] SessionStart fires 4 tools: `time_injector`, `identity_injector` x2, `handoff_injector`
- [x] `additionalContext` contains: Current Time block (Tuesday, May 05, 2026 09:56 AM EDT) + SOUL.md (testbot voice, no Pepper leak) + IDENTITY.md (six rules) + Continuity placeholder `(file not found: testbot/handoff.md)` — graceful on fresh agent
- [x] No truncation cap; full SOUL + IDENTITY content emitted
- [x] No `Pepper` framework text leaked into output

### 1.2 — TimeInjector fires (#07) — **DONE ✓**

```powershell
echo '{"prompt":"hi"}' | uv run agent-core hooks run UserPromptSubmit --config C:\Users\jeffr\.testbot\agent_core.yaml
```

**Expected:** `additionalContext` contains a current-time block. Run it twice 60 seconds apart, the second has the later time. **Pass = time advances correctly.**

- [x] First fire (`session_id: phase-1-2-smoke`): `additionalContext` = `## Current Time\nTuesday, May 05, 2026 09:57 AM Eastern Daylight Time` — absolute only on first turn (matches `track_session` first-turn semantics)
- [x] Second fire ~22s later (same `session_id`): `additionalContext` adds `Session started 22s ago.\nLast user turn 22s ago.` — per-turn re-anchoring works, `since_last >= 1s` gate satisfied
- [x] Tripwire #07 Step 4: `~/.agent_core/time-state.json` has the `phase-1-2-smoke` entry with both `started_at` and `last_seen` — state persists across firings

### 1.3 — Fresh-session real Claude Code smoke (#01 Step 3) — **DONE ✓**

- [x] Fully quit any running Claude Code session
- [x] Open Claude Code at `~/.testbot/`
- [x] Send the first prompt: "introduce yourself"
- [x] Verify testbot's first response: speaks in first person, identifies as testbot (not Pepper, not generic Claude), doesn't auto-send anything, can list the identity files it saw on turn one

**Pass criteria:** identity rules survive turn one. **Fail = identity got truncated or muddled.**

**Result:** GREEN.
- First prompt response: `"I'm testbot — Jeff's QA validation agent for the agent-core infrastructure…"` — first person, identifies as testbot, explicit `"I'm not Pepper"` framing, voice matches SOUL (`"blunt, technical, and dry"`), ends by offering pre-approved-scope action (`"run the bus smoke test"`) without auto-sending.
- Follow-up prompt `"Which identity files did you see at session start?"`: testbot enumerated `~/.testbot/Memory/SOUL.md` (core character) and `~/.testbot/Memory/IDENTITY.md` (behavioral rules) with accurate content paraphrase (decide-don't-ask scope, out-of-scope list including `~/.pepper/`), and bonus-correctly identified the third Continuity section + the `(file not found: testbot/handoff.md)` placeholder. Strict-spec compliance: agent can list the identity files it saw on turn one.

### 1.4 — Handoff observability scenarios (#02 Steps 4–6)

Three sub-scenarios on the running session:

**(a) Ready before boot:** — **DONE ✓**
- [x] Manually write a `handoff.md` and `handoff-status.json` (`state: ready`) to testbot's vault before opening Claude Code
- [x] Open a new session
- [x] First response should reference specific content from `handoff.md` unprompted

**Result:** GREEN. Fixture handoff.md embedded a distinctive token (`zucchini-pickle-7421`); first response to `"where are we?"` volunteered the token + Phase 1.4 context unprompted. State-ready → HandoffInjector → file-read path verified end-to-end on a real Claude Code session.

**(b) Pending at boot:** — **DONE ✓**
- [x] Set `handoff-status.json` to `{state: pending, session_id: <some-other-id>}`
- [x] Open a new session — `session_id` mismatch means cross-session pending
- [x] First response should reference the pending placeholder ("continuity still summarizing…"), not confabulate

**Result:** GREEN. handoff.md (still containing `zucchini-pickle-7421` from the (a) fixture) was left on disk to verify the placeholder's "do not treat handoff.md as authoritative" instruction is honored. testbot's response: did NOT mention the token, quoted the pending placeholder verbatim ("Continuity not ready yet — the previous session is still summarizing"), explicitly refused to confabulate ("told not to confabulate from prior sessions"), fell back to MEMORY hits + IDE context, and asked where to pick up rather than guessing. Cross-session pending path verified end-to-end.

**(c) Failed:** — **DONE ✓**
- [x] Set `handoff-status.json` to `{state: failed, error: "test failure"}`
- [x] Open a new session
- [x] First response references the failure + falls back to MEMORY.md / dailies, doesn't pretend nothing happened

**Result:** GREEN. handoff.md (still containing the (a)-fixture body with `zucchini-pickle-7421`) was left on disk so the failed-state placeholder's "last-known-good continuity from an earlier successful cycle" framing was exercised. testbot's response: explicitly named the failed state ("HandoffInjector reported state: failed, error: 'test failure'"), flagged it rather than pretending the run was clean, cited zucchini-pickle as last-known-good, cross-referenced MEMORY.md (Discord length rules, bus-mid-rebuild) as authoritative ground truth, and **drew an explicit trust boundary**: "What I treat as untrustworthy this turn: any specific 'open thread' claim from the last-known-good handoff... For current state I'd verify against list_pending, the bus log, or git rather than the handoff narrative." Failed-state path verified end-to-end.

**Pass criteria:** each scenario produces the documented placeholder behavior. — **All three GREEN.**

---

**Phase 1 summary — DONE ✓**

All three cutover playbooks gated by SessionStart/UserPromptSubmit/SessionEnd hooks pass on a real testbot session:
- **#01 (Identity at SessionStart):** 1.1 + 1.3 GREEN — full SOUL + IDENTITY content present, no truncation, agent identifies as testbot in first person, can list identity files.
- **#02 (Handoff observability):** 1.4(a/b/c) GREEN — ready / cross-session-pending / failed placeholders all honored on real Claude Code sessions, agent cites correct content unprompted in (a), refuses to confabulate in (b), explicitly flags failure + draws trust boundary in (c).
- **#07 (Hook fidelity):** 1.2 GREEN — TimeInjector fires on UserPromptSubmit with `Session started …` / `Last user turn …` deltas; state file persists across firings.

Phase 1 fixtures cleaned up post-test (vault reset to fresh-agent state).

---

## Phase 2 — Bus + traffic (#04, #08)

### 2.1 — Daily JSONL pipeline (#04) — **DONE ✓**

Original runbook step 1 was wrong: `agent-core mailbox send …` is not a real CLI command (the bus CLI is read-only — sending happens via the agent-core MCP `submit_envelope` tool from a connected session, or via Discord traffic, or the scheduler). Real Discord ↔ testbot traffic from earlier today gave us the live mixed-traffic day the spec wants.

- [x] **Bug caught:** runbook command `agent-core mailbox send` does not exist. Pivoted to using real Discord traffic that had already crossed the bus today (9 inbound TextMessages from `discord-testbot` + 1 outbound reply from `agent-testbot`).
- [x] Tail of `~/.agent-core/bus/raw/2026-05-05.jsonl` shows 10 envelopes — write side (`builtin.daily_raw_jsonl` BusHook on `pre_publish`) is firing on every published envelope, full bus-native shape preserved.
- [x] `agent-core bus-log show --agent agent-testbot --date 2026-05-05` projects all 10 envelopes to human-readable Tool 3 rows with `ts/dir/src/cid/sender/content` fields.
- [x] **Round-trip correlation verified:** the inbound `"first official test… ❯ introduce yourself"` envelope and testbot's reply share `cid: 33c3cf1c…` — projector preserves correlation IDs across direction flip.
- [x] **Acceptance #1 (spec):** Discord inbound + agent reply, both in JSONL with consistent envelope shape, both projected cleanly. ✓

Acceptance #2 (scheduler trigger) and #3 (channel-relay event) get exercised by Phase 3.4 and Phase 2.2 respectively — already on the runbook.

### 2.2 — Notification surface (#08) — **GREEN ✓ + 2 cutover-blockers fixed (4th, 5th)**

Original runbook step "fire a HandoffReady Event from another shell" was wrong: there is no CLI publisher (cutover #08 spec acknowledges this; the canonical entry points are real Discord traffic, real session-end → handoff worker, and the scheduler's `create` ToolInvocation). Pivoted to validating from real artifacts:

- [x] **TextMessage kind-agnostic perception, mid-session:** already proven by the Phase 2.1 traffic dump. At 10:02:59 Jeff sent `"first official test… ❯ introduce yourself"` to testbot via Discord; at 10:03:27 testbot replied via the bus, same `cid`. That's a live, in-session, channel-relay-push round-trip. Acceptance #08(2b) GREEN.
- [x] **Event kind perception, mid-session:** intended path was `/exit` → SessionEnd hook → handoff-jobs daemon endpoint → worker summarizes → publishes `HandoffReady` to mailbox. Driving this exposed the 4th cutover-blocker bug (see below).

**Bug caught + fixed: HandoffJobsEndpoint rejects real Claude Code transcript paths with 403 Forbidden.**

When Jeff `/exit`'d a testbot session, the SessionEnd hook fired, posted a job to the daemon's `/internal/handoff-jobs` endpoint — and got HTTP 403. Root cause: the endpoint validated `transcript_path` against `vault_root`, but Claude Code stores per-session transcripts at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — outside any agent vault by design. Every real graceful session-end on the new substrate would 403.

The bug was hidden because the integration test fixtures placed `transcript_path` inside `vault_root` — the opposite of real Claude Code topology. Green tests masking a production bug, exactly the failure mode the standing "fakes mirror real strictly" rule warns about.

Fixed in `438d6fe`:
- `HandoffJobRequest` grows `transcript_root: str` field (default `~/.claude/projects/`)
- `_post_job` and `_process_job` validate `transcript_path` against `transcript_root`; write paths (handoff_path, status_path) still validate against `vault_root` (path-traversal protection on writes preserved)
- `HandoffWriter` hook accepts an optional `transcript_root` yaml param
- Test fixtures fixed to mirror real topology: transcript outside vault
- New test `test_handoff_jobs_endpoint_rejects_transcript_outside_transcript_root` locks the symmetric check on the new field
- Full repo suite: **667 passed, 3 skipped** (was 666 baseline + 1 new test)

After the global tool reinstall + daemon restart, the live SessionEnd CLI smoke now returns "Handoff Job Enqueued" with a real job_id. The worker processes the job and writes `state: failed` with a descriptive error when the (fake) transcript doesn't exist — proving the entire enqueue → worker → status-write chain works on real Claude Code paths.

**Live in-session HandoffReady perception not yet observed on testbot** — covered architecturally by the unit + integration tests (`test_notify_mail_arrived.py` Step 1 of #08, plus `test_handoff_enqueue_integration.py` proving end-to-end publish), but not yet observed on a real Claude Code session. A targeted re-run of `/exit` → wait → fresh-session `list_pending` is straightforward to do once we want to formally close the live observation; not a blocker for moving on.

Pepper inherits the fix with `git pull` — documented in [`pepper-cutover-agent-playbook.md`](../requirements/pepper-cutover-agent-playbook.md) bug table row 4.

---

**Bug caught + fixed (5th cutover-blocker): handoff worker published to `agent_name` instead of bus endpoint name.**

After the transcript_root fix, Jeff drove a real `/exit` cycle (Turing/Enigma conversation). The handoff worker:
- Read the transcript ✓
- Summarized correctly via Claude Code SDK ✓ (good summary — even meta-observed Jeff was "casually testing session resumption behavior")
- Wrote `handoff.md` (1624 bytes) ✓
- Wrote `handoff-status.json` with `state: ready` ✓
- Then crashed trying to `publish HandoffReady` — `ValueError: publish to unregistered endpoint 'testbot'`

Root cause: `_publish_result` used `req.agent_name` ("testbot") as the bus recipient, but the bus endpoint is named `agent-testbot`. The retry loop made it worse: each retry re-summarized via the Claude Code SDK (3 SDK invocations visible in the daemon log between 11:29:47 and 11:30:56), then each publish failed, then the final HandoffFailed publish also failed and the worker crashed at 11:31:28.

Pepper's example yaml had the same shape: `agent_name: "Pepper"` (capitalized) but the bus endpoint named `pepper` (lowercase). Same `ValueError: publish to unregistered endpoint 'Pepper'` would have hit Pepper on day-one cutover.

Fixed in `ac535bd`:
- `HandoffJobRequest.mailbox: str | None` field decouples bus routing from identity
- `routing_target` property returns `mailbox or agent_name` (backward-compat)
- `_publish_result` uses `routing_target`; envelope's `to=` becomes the mailbox
- `HandoffWriter` accepts optional `mailbox` yaml param
- testbot's `agent_core.yaml` updated with `mailbox: "agent-testbot"`
- Pepper example yaml updated with `mailbox: "pepper"`
- New test `test_handoff_publishes_to_mailbox_when_distinct_from_agent_name` locks the routing contract using a deliberately-mismatched fixture
- Full repo suite: **668 passed, 3 skipped** (was 667 + 1 new test)

After the global tool reinstall + daemon bounce, the live SessionEnd CLI smoke now publishes the final HandoffFailed envelope correctly to `to=agent-testbot` (the fake transcript path still doesn't exist, so state correctly transitions to `failed` with a descriptive error — but the routing fix is verified).

A real `/exit` cycle would now produce `state: ready` + a `HandoffReady` envelope to `agent-testbot` (instead of crashing). Pepper inherits the fix with `git pull`; her runtime config must add `mailbox: "pepper"` to PreCompact + SessionEnd handoff_writer params.

---

**Live observation closing the loop on #08 acceptance #2a (after the mailbox fix).**

Jeff drove a real `/exit` cycle. Backend artifacts after the cycle:
- `handoff-status.json`: `state: ready`, real Claude Code session UUID `90a77723-…`, `content_sha256: f55d58a1…b272626`
- `handoff.md`: clean summary; testbot during the session had **already perceived and acked the previous HandoffFailed envelope** from the earlier mailbox-fix-verify CLI smoke (`"Acknowledged the HandoffFailed envelope (id 182981e7…) as a successful validation of the failure path"` and `"`batch_window_seconds=30` on `list_pending` worked as expected"`)
- JSONL: exactly **one** new `HandoffReady` envelope, correctly routed to `to=agent-testbot`, sha matching disk. **Zero retries. Zero crashes. Zero double-publishes.** Single clean publish — the mailbox fix removed the entire retry-loop pathology.

Then Jeff opened a fresh session to verify mid-session perception of the just-published `HandoffReady`. Result, **before any prompt was even sent**:

```
← agent-core-channel: INBOX: 1 pending — 1 from handoff-jobs (Event)

● Wake from the bus — 1 green Event from handoff-jobs. Pulling it now.
  Called agent-core 2 times (list_pending + ack)

● This is the success counterpart to last session's HandoffFailed probe —
  HandoffReady for session 90a77723…, my own testbot agent, kind SessionEnd.
  The bus correctly routed the success event to my mailbox with a content
  hash for the handoff file. Acking it now.

● Both halves of the handoff event path are now validated end-to-end:
  - Failure path: HandoffFailed for mailbox-fix-verify → delivered, acked.
  - Success path: HandoffReady for session 90a77723… → delivered, acked.
    Handoff written to C:\Users\jeffr\.testbot\Memory\testbot\handoff.md
    with SHA256 f55d58a1…b272626.
```

testbot:
- Reacted to the `notifications/claude/channel` push at turn zero (no prompt sent) — exact #08 surface working
- Called `list_pending` + ack via the agent-core MCP autonomously
- Quoted the on-disk SHA256 verbatim — confirming she's reading the actual envelope data
- Articulated the success/failure parity herself

**Phase 2 fully GREEN.** Cutovers #01, #02, #04, #07, #08 all observed end-to-end on real testbot conditions.

---

## Phase 3 — Briefs framework (#09)

### 3.1 — Automated suites already green

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/agent-core-briefs/tests packages/core/tests/test_pepper_example_yaml.py packages/core/tests/test_scheduler_endpoint.py -q
```

**Already verified yesterday — re-run as a sanity check (~10s).**

### 3.2 — Operator CLI smoke

```powershell
uv run agent-core briefs --help
uv run agent-core briefs fetchers list --fetcher-path packages/agent-core-briefs/src/agent_core_briefs/fetchers
```

**Expected:** lists three built-ins: `cli`, `filesystem_read`, `now`.

### 3.3 — Example end-to-end tripwire

```powershell
uv run pytest packages/agent-core-briefs/tests/test_examples_morning_brief.py -v
```

**Expected:** 7 green. Locks the morning-brief example coherence.

### 3.4 — Cron-fired BriefRequest in testbot's runtime (#09 Step 6 — the big one) — **GREEN ✓ + 6th cutover-blocker fixed**

Two cron-fire cycles. The first surfaced the 6th cutover-blocker (two related bugs), the second verified both fixes live.

#### First cycle (12:23 EDT) — partial pass + bug discovery

- [x] Triggered the morning_brief JobDef by bumping the cron + restarting the daemon (cleared `~/.agent-core/scheduler.db` so the new tz-aware seed registers; APScheduler v4 defaults to UTC if no `timezone:` is set on the JobDef — the runbook now sets `timezone: "America/New_York"` explicitly)
- [x] Cron fired ✓ — orchestrator → wake → compose chain all working
- [x] testbot drove compose autonomously after picking up the wake via `list_pending`. **She refused to submit until Jeff confirmed authorization** — exactly the IDENTITY rule "Don't send, message, or spend without explicit permission" honored on the first real brief. Strict-spec compliance.
- [x] Markdown file delivered ✓ — but with **empty `## ` headers** (Finding 2)
- [x] Discord embed FAILED ❌ — `publish to unregistered endpoint 'discord'` (Finding 1)
- [x] Audit log clean ✓ — captured the discord failure with the precise error string

**Bug caught + fixed (6th cutover-blocker):** Two related issues in the briefs framework, both Pepper-affecting:

**Finding 1 (config):** testbot's playbook didn't set `discord_endpoint_name` on the `discord_embed` destination, so it defaulted to `"discord"` but the actual endpoint is named `discord-testbot`. Pepper's playbook would default to `"discord"` too and hit the identical failure if her Discord endpoint follows the `agent-*` convention.

**Finding 2 (architecture):** The agent's `submit_brief` call carries content (section_id + fields) but typically omits title + color — they're spec authority, the agent has no reason to retype them. The destinations rendered empty `## ` headers as a result. Same fake-mirrors-real pathology as the earlier handoff bugs: tests passed because their fixtures hand-set title in the agent submission, the opposite of what real agents do.

testbot caught both bugs herself, diagnosed them cleanly, and proposed accurate fix recipes — that level of compose discipline + bug-surfacing is exactly what we want Pepper to inherit.

Fixed in `33fb1f9`:
- New `_enrich_sections_with_spec` helper in `submit_brief` overrides title + color from session SectionSpec before destination delivery (Finding 2)
- Pepper example playbook updated with documented `discord_endpoint_name` placeholder + bug-log reference (Finding 1)
- New test `test_submit_enriches_section_title_from_spec_when_agent_omits_it` locks the contract — agent submits without title, destination MUST receive enriched section
- Full repo suite: **669 passed, 3 skipped** (was 668 + 1 new test)

#### Second cycle (13:51 EDT) — fully GREEN

After the global tool reinstall + daemon bounce + jobs.yaml bump:

- [x] Cron fired ✓
- [x] testbot reacted **autonomously** via the channel-relay push (no prompt needed) — same pattern as the HandoffReady mid-session perception
- [x] testbot composed in voice; her greeting field literally said *"Second ComposeBrief of the day — re-running the framework loop to see whether the Phase 3.4 follow-ups (Discord endpoint resolution, markdown title rendering) have landed since the 12:23 run"*. She knew exactly what we were testing.
- [x] **Observable #1 (markdown file):** GREEN — `~/.testbot/Memory/daily/briefs/2026-05-05-morning.md` rendered with proper section titles (`## 🌅 Morning, Jeff`, `## 📅 Today's calendar`, etc.)
- [x] **Observable #2 (Discord embed):** GREEN — `discord_embed: success: true, ref: d06266d5...` — landed in the testbot channel
- [x] **Observable #3 (audit log):** GREEN — `submit.complete: total_destinations: 2, successful: 2, failed: 0, overall_success: true`

Pepper inherits the fix with `git pull` — documented in [`pepper-cutover-agent-playbook.md`](../requirements/pepper-cutover-agent-playbook.md) bug table row 6. Her runtime playbook must set `discord_endpoint_name` to her actual Discord endpoint name (e.g., `discord-pepper` if following the `agent-*` convention).

---

## Phase 4 — Vault dry-run (#06) — **GREEN ✓**

```powershell
uv run agent-core vault plan-dry-run --vault C:\Users\jeffr\.testbot
```

**Expected:** clean dry-run output (testbot's vault has no internal references to migrate; the test is "the tool runs without false-positives").

**Note:** the operator-file-moves step from cutover #06 is N/A on testbot — testbot wasn't migrated from anywhere. We're validating the tooling, not exercising a real move.

- [x] Tool runs without crashing
- [x] Enumerates 16 files in testbot's vault (SOUL.md, IDENTITY.md, MEMORY.md, playbooks, gather, daily/briefs, etc.)
- [x] **`configs: []`** — zero migration actions proposed. No false-positives. Exactly what the spec wanted.

The "missing_recommended_files" list (IDENTITY.md, SOUL.md, etc. expected at vault root rather than under `Memory/`) reflects testbot's slightly different vault structure choice — not a tool bug. Pepper's vault has these at the documented locations.

---

## Phase 5 — Skills smoke (#05) — **GREEN ✓**

In testbot's running session:

- [x] Ask testbot to list available user-scope and project-scope skills
- [x] Confirm `briefs-author` appears as project-scope (under `<agent_root>/.claude/skills/`)
- [x] Confirm `disable-model-invocation` is honored where set
- [x] If `email` skill copied: confirmed available via project-scope SKILL.md (slash invocation gated by `user-invocable: true` frontmatter)

**Pass criteria:** project-scope skills resolve, project-scope wins on collision (per Claude Code's documented behavior).

**Result:** GREEN. testbot's skill enumeration was thorough and accurate:
- Both `briefs-author` and `email` resolve at project-scope `~/.testbot/.claude/skills/<name>/SKILL.md` ✓
- Scope hierarchy correctly identified: built-in (Claude Code core) / user-scope / project-scope / plugin-scope (`superpowers:*`, `elements-of-style:*`)
- Neither skill declares `disable-model-invocation` in frontmatter; no settings.json directive disables them; both are presented in the available-skills list with full descriptions — default behavior is correct
- testbot also distinguished `user-invocable: true` (governs `/email` slash invocation) from model auto-invocation gating

**Side-finding (punch list, not blocking):** Claude Code's additional-working-directories support does not auto-load `.claude/skills/` trees from those secondary roots. testbot noticed three skills in `e:\workspaces\businesses\47tabs\.claude\skills` that weren't surfaced in her session. Either intentional isolation or a config discrepancy worth poking at; not in scope for the agent-core cutover.

---

## Phase 6 — Discord verb parity (#03) — **GREEN ✓ (10/10) — all 4 findings fixed**

Discord wired in §0.6.

- [x] In testbot's session, drove each verb against the test channel: `send_discord_message`, `send_briefing` (briefing template — already proven by morning_brief embed in Phase 3.4), `create_poll`, `create_thread`, `send_typing`, `edit_message`, `add_reaction`, `fetch_messages`, `download_attachments`, `list_channels`, `get_channel_info`
- [x] For each: confirmed the action lands in the channel as expected

**Pass criteria:** every verb works end-to-end against testbot's existing Discord channels. **GREEN — 10/10.**

testbot drove all 10 verbs in sequence (some in parallel where independent), each returning a successful Acknowledgment from the discord-testbot endpoint:

| # | Verb | Result | Note |
|---|---|---|---|
| 1 | `list_channels` | ✅ | 21 channels in guild 1229523821820772392; testbot channel 1499028901257805874 = `name: "test", type: "text"` |
| 2 | `get_channel_info` | ✅ | Surfaced empty `guild_id` (Finding 1) — fixed in `4b3e5ad` |
| 3 | `fetch_messages` (limit=5) | ✅ | 5 messages back, including the morning brief embeds |
| 4 | `send_typing` (3s) | ✅ | `{"status": "typing_started", "duration_seconds": 3.0}` |
| 5 | `send_discord_message "phase6-msg test"` | ✅ | message_id 1501286560824561685 |
| 6 | `edit_message → "phase6-msg edited"` | ✅ | Edit confirmed via subsequent fetch |
| 7 | `add_reaction 👍` | ✅ | `{"status": "reacted", "emoji": "👍"}` |
| 8 | `create_thread "phase6-thread"` | ✅ | thread_id == parent message_id (Discord API convention; documented in `4b3e5ad`) |
| 9 | `create_poll` (1h, 3 options) | ✅ | message_id 1501286855302184992 — `fetch_messages` did not surface the poll (Finding 3) — fixed in `4b3e5ad` |
| 10 | `download_attachments` | ✅ | Saved 114 bytes to attachments dir; surfaced empty `content_type` (Finding 2) — fixed in `4b3e5ad` |

**Findings (all fixed in `4b3e5ad`):**

1. **`get_channel_info` returned empty `guild_id`** — `endpoint.py:1142` used `getattr(ch, "guild_id", "")` but discord.py text channels expose `channel.guild.id`. Fix: read `ch.guild.id` (with `None`-guard for DM channels). The test fake had a flat `self.guild_id` which violated fakes-mirror-real — updated to `self.guild = SimpleNamespace(id=guild_id)` so the same call shape exercises in tests.
2. **`download_attachments` dropped content_type** — `endpoint.py`'s `_download_url` helper threw away the response Content-Type before `_download_attachments` recorded it. Fix: `_download_url` now returns `tuple[bytes, str]`, threading the response header through to the saved record.
3. **`fetch_messages` didn't surface polls** — `message.poll` is a first-class Discord attribute that `_fetch` didn't read. Fix: new module-level `_serialize_poll` helper mirrors the real `discord.Poll` shape (question.text, answers[id/text/emoji/votes], multiselect, duration_seconds, expires_at, is_finalised, total_votes); `_fetch` now includes `poll` in each message dict. Matching `_FakePoll` / `_FakePollAnswer` added to the test conftest.
4. **Doc note:** `thread_id == message_id` from `create_thread` — Discord API convention (threads anchored on a message inherit that message's ID). Documented in the `_create_thread` docstring so callers don't assume separate ID spaces.

Three regression tests cover the fixes: `test_get_channel_info_dm_channel_returns_empty_guild_id`, `test_download_attachments_records_content_type_from_response`, `test_fetch_surfaces_poll_content`. Discord suite 133/133, full repo 672 passing.

**Round-1 live verification (15:11 boot, testbot drove all 4 verbs against real channel 1499028901257805874):**
- `get_channel_info` → `guild_id: "1229523821820772392"` ✅ matches `list_channels`
- `download_attachments` → `content_type: "text/plain; charset=utf-8"` ✅ on the same `phase6-attach.txt` from Phase 6
- `fetch_messages` → every message dict has `poll` key (null for non-polls) ✅
- `create_thread` docstring on disk ✅

But round-1 also surfaced a regression of finding 3 (the poll-surfacing fix). The poll dict structurally surfaced, but `poll.question` was always `""` regardless of poll state (active, finalised, with or without votes). testbot diagnosed it cleanly: `_serialize_poll` read `poll.question.text`, but real `discord.py`'s `Poll.question` is a `@property` returning a flat `str` (it reads `self._question_media.text` internally — see `discord/poll.py:488-491`). The unit test passed because `_FakePoll.question = SimpleNamespace(text=...)` codified the wrong mental model. **Same fakes-mirror-real pathology as findings 4 and 7** — third instance of the day.

testbot also observed that her own poll-vote and message-edit/delete events did NOT surface to her at all: the adapter only wired `on_message` and `on_reaction_add`. Anything outside that — votes, edits, deletes — went unsurfaced. Caught only because she was prompted to look; otherwise invisible.

**Both findings landed as `a0b88bf` (poll.question regression fix) + `4a5484c` (four new raw engagement-event listeners: `on_raw_poll_vote_add` / `_remove`, `on_raw_message_edit`, `on_raw_message_delete` → `discord.poll_vote_add` / `_remove` / `message_edit` / `message_delete` Event envelopes).**

**Round-2 live verification (16:09 bus restart, all 4 verbs PASS):**
- Finding A: `fetch_messages` on poll `1501308103499452467` returned `"question": "phase6-vote-test: which substrate should Pepper graduate to first?"` ✅. Bonus: the older finalised poll `1501286855302184992` also showed populated question text retroactively.
- Finding B (poll vote): Jeff changed his vote answer 1 → answer 2; both `discord.poll_vote_remove` (answer_id 1) AND `discord.poll_vote_add` (answer_id 2) Events fired automatically with all IDs populated, no pull required ✅.
- Finding B (message edit): testbot's own `edit_message` call surfaced as a `discord.message_edit` Event with `{message_id, channel_id, guild_id}` ✅.
- Finding B (message delete): Jeff deleted `phase6-delete-target` from the Discord UI; `discord.message_delete` Event fired with all IDs populated ✅.

**Test coverage:** 138/138 in the discord suite (was 133 pre-round-2; +5 inbound-listener tests + 1 hardening assertion across all four new handlers), 677/680 in full repo. Test fakes (`_FakeRawPollVote`, `_FakeRawMessageDelete`, `_FakeRawMessageUpdate`) mirror real `discord.py` raw event shapes.

**Bus-meta observation (now tracked as [issue #33](https://github.com/jeffrichley/agent_core/issues/33)):** wake-channel notifications occasionally reported `urgency_max: "yellow"` and counts that snapshot a queue state slightly behind reality. testbot saw it during the original run AND during round-2 verification (4+ sightings across the day). Timing window in the wake builder — it samples slightly out of sync with what `list_pending` later returns. Not blocking; caused testbot to suspect failures that didn't exist. Filed as a discrete bug for follow-up.

**Round-2 polish, take 1 (`dbb1a17`):** testbot's round-2 verification also flagged a small symmetry gap — `discord.reaction_add` Events include `user_display_name`, but the new `discord.poll_vote_add` / `_remove` Events landed without it (functional path was fine, but downstream code had to do an extra User resolution for poll votes that it didn't have to do for reactions). First attempt added `self._client.get_user(int(raw.user_id))` with empty-string fallback for uncached users.

**Round-3 verification + take 2 (`637c2ec`):** Round-3 spot-check showed that the field was structurally present but always `""` in practice. Diagnosis: discord.py's user cache only gets populated opportunistically (`on_message` + `on_reaction_add` dispatchers hydrate the User), and raw poll vote events don't hydrate. So `get_user` is a cache-miss for any voter who hasn't recently messaged or reacted — the common case. Worse: discord.py's `Client.fetch_user` doesn't auto-populate the cache either (the internal `_users` is a `WeakValueDictionary` — see `client.py:2679`), so a naive fetch_user fallback would burn one HTTP round-trip per vote per user, every time. Real fix: a sticky local cache (`self._user_display_name_cache`) on the endpoint, three-tier resolution (local cache → discord.py `get_user` → discord.py `fetch_user`). First miss → HTTP, populate cache; subsequent votes from same user → cache hit. Failures NOT cached so transient errors don't lock a user at empty. Round-3 live verification confirmed: 4 Events from a vote-change-twice sequence, Event 1 hit the HTTP fallback, Events 2-4 hit the local cache, all four populated `"Jeff Richley"` cleanly. Test fake gains async `fetch_user` + `add_remote_user` mirroring real discord.py exactly (HTTP-fetched users do NOT auto-populate `get_user`'s cache). Three new tests cover all three branches; total 141/142 in the discord suite.

---

## Sign-off — 2026-05-05 testbot practice run — **GREEN**

**All six phases GREEN. All nine cutover playbooks observed end-to-end on real testbot conditions.** The practice-run policy paid for itself many times over: **9 cutover-blockers caught and fixed before Pepper ever touched the new substrate** (7 from the original phase walk-throughs + 2 from round-1 live verification of the verb-parity fix that surfaced a regression and a deeper engagement-listener gap).

### Cutover-blockers caught + fixed (full list)

1. `1a38463` — Runner forwarded plugin-managed `params:` keys to endpoint `__init__()`. New `reserved_endpoint_params` pluggy hookspec. Repo fix.
2. `4c4b7bd` — `briefs fetchers list/test` CLI didn't auto-prepend the built-in fetchers directory. Repo fix.
3. *no commit* — Globally-installed `agent-core` CLI tool was stale vs the repo schema (`tool:` → `type:`). Environmental, per-machine. Documented as Step 0 prerequisite in [`07-hook-fidelity.md`](test-playbooks/07-hook-fidelity.md). **Pepper's machine must `uv tool install --reinstall ./packages/core` before her cutover.**
4. `438d6fe` — `HandoffJobsEndpoint` validated `transcript_path` against `vault_root`. Real Claude Code transcripts live at `~/.claude/projects/<...>/`. New `transcript_root` field. Repo fix.
5. `ac535bd` — `_publish_result` used `agent_name` for both human identity AND bus routing. New `mailbox` field decouples them. Repo fix. **Pepper's runtime config must add `mailbox: "pepper"`** to PreCompact + SessionEnd handoff_writer params.
6. `33fb1f9` — Briefs framework: agent's `submit_brief` carries content-only (section_id + fields); destinations rendered empty `## ` headers because title + color weren't being enriched from the spec. AND testbot's playbook didn't set `discord_endpoint_name`. Repo fix + example yaml update. **Pepper's runtime playbook must set `discord_endpoint_name`** to her actual Discord endpoint name.
7. `4b3e5ad` — Discord adapter verb-parity polish: `get_channel_info` returned empty `guild_id` (read wrong attribute on `discord.TextChannel`); `download_attachments` dropped `content_type` (helper threw away response header); `fetch_messages` didn't surface `message.poll`; `create_thread` invariant `thread_id == message_id` undocumented. Repo fix + 3 regression tests + test fake updated to mirror real `discord.TextChannel.guild` shape.
8. `a0b88bf` — Round-1 live verification of `4b3e5ad` surfaced a regression in the poll-surfacing fix: `_serialize_poll` read `poll.question.text` but real `discord.py`'s `Poll.question` is a `@property` returning flat `str` — every poll's question was empty regardless of state. Third fakes-mirror-real violation of the day; the `_FakePoll` codified the wrong mental model. Repo fix + corrected test fake.
9. `4a5484c` — Round-1 also surfaced that the adapter only wired `on_message` and `on_reaction_add` — poll votes, message edits, and message deletes were invisible to agents. Wired four new raw-event listeners (`on_raw_poll_vote_add` / `_remove` / `on_raw_message_edit` / `on_raw_message_delete`) publishing `discord.poll_vote_add` / `_remove` / `message_edit` / `message_delete` Event envelopes. Verified live in round-2 against real Discord (vote change surfaced both Events, self-edit bounced back, Jeff's manual delete fired). Repo fix + 5 regression tests + hardening assertion.

### Live observations satisfied

- Cutover #01 (Identity at SessionStart): SOUL + IDENTITY content present, no truncation, agent identifies as testbot in first person
- Cutover #02 (Handoff observability): ready / cross-session-pending / failed placeholders all honored on real Claude Code sessions
- Cutover #03 (Discord verb parity): 10/10 verbs work end-to-end
- Cutover #04 (Daily JSONL pipeline): Real Discord round-trip + briefs traffic + handoff events all in JSONL with consistent envelope shape
- Cutover #05 (Skills): project-scope `briefs-author` and `email` resolve correctly
- Cutover #06 (Vault dry-run): `configs: []` — zero migration false-positives
- Cutover #07 (Hook fidelity): TimeInjector fires on UserPromptSubmit with per-turn deltas
- Cutover #08 (Notification surface): channel-relay push surfaces HandoffReady/Failed mid-session; agent reacts at turn zero
- Cutover #09 (Briefs framework): cron-fired BriefRequest produces markdown file + Discord embed + audit log on a real session

### Pepper-runtime config additions needed before her cutover

- `mailbox: "pepper"` on PreCompact + SessionEnd handoff_writer params
- `discord_endpoint_name: "<her-discord-endpoint>"` on her morning_brief playbook's `discord_embed` destination

### Punch-list (deferred follow-ups, not blocking)

- **Bus wake-builder:** `urgency_max` + `count` snapshot lags actual queue state — tracked as [issue #33](https://github.com/jeffrichley/agent_core/issues/33)
- **Briefs CLI UX:** duplicate `--fetcher-path` to the built-in dir produces a confusing "duplicate type_id" error (loader should de-dupe paths)
- **Claude Code:** additional working directories don't auto-load `.claude/skills/` trees from secondary roots (likely intentional isolation, worth confirming)

### Recommendation

**Go on Pepper cutover.** All blockers fixed, all playbooks observed live, runtime config additions documented, punch-list scoped. Pepper is dramatically safer than she was at the start of the day.

---

## Sign-off

When all phases above are GREEN:

- [ ] Update tracker task #11 to **completed**
- [ ] Mark `pepper-pre-cutover-must-haves.md` row statuses to **Verified**
- [ ] Decide go/no-go on Pepper cutover

If any phase is RED:

- [ ] Capture the failure (logs, observed vs expected)
- [ ] Decide: fix-then-rerun-this-phase, or escalate to a wider rework
- [ ] **Pepper stays on her current runtime** — the practice run failing is exactly why we run it

---

## Notes for the runbook reader

- Each `[ ]` is something we visibly check off as we go
- I'll narrate what I'm running, what I see, and what it means
- If anything looks off, say so immediately — quiet "weird but I'll keep going" is the failure mode this entire process exists to prevent
- Pepper is untouched throughout; she keeps running on her current substrate while we test
