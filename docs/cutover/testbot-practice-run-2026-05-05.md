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

### 2.2 — Notification surface (#08) — **PARTIAL ✓ + 4th cutover-blocker fixed**

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

### 3.4 — Cron-fired BriefRequest in testbot's runtime (#09 Step 6 — the big one)

Fire the staged JobDef from §0.5. Approach decided at setup time — either:
- bump the cron expression to fire ~1 min from now and wait for it, or
- invoke `agent-core scheduler trigger <job-name>` if available (validates the same scheduler→bus path without wall-clock waiting).

- [ ] Trigger the morning_brief JobDef (whichever way we picked)
- [ ] Confirm three observables:
  1. `~/.testbot/Memory/daily/briefs/<date>-morning.md` written with the section structure
  2. Discord embed lands in the testbot channel (since #03 is in scope today)
  3. `~/.agent-core/briefs/audit.jsonl` shows the full `request_received → gather_completed → wake_published → submit_attempted → delivery_completed` chain

**Pass criteria:** all three. Markdown file is the most important — it's the canonical fallback destination and proves the chain works without Discord; Discord embed proves the bus-mediated destination path; audit log proves the deterministic plumbing recorded the chain end-to-end.

---

## Phase 4 — Vault dry-run (#06)

```powershell
uv run agent-core vault plan-dry-run --vault C:\Users\jeffr\.testbot
```

**Expected:** clean dry-run output (testbot's vault has no internal references to migrate; the test is "the tool runs without false-positives").

**Note:** the operator-file-moves step from cutover #06 is N/A on testbot — testbot wasn't migrated from anywhere. We're validating the tooling, not exercising a real move.

---

## Phase 5 — Skills smoke (#05)

In testbot's running session:

- [ ] Ask testbot to list available user-scope and project-scope skills
- [ ] Confirm `briefs-author` appears as project-scope (under `<agent_root>/.claude/skills/`)
- [ ] Confirm `disable-model-invocation` is honored where set
- [ ] If `email` skill copied: invoke `/email` and confirm it resolves

**Pass criteria:** project-scope skills resolve, project-scope wins on collision (per Claude Code's documented behavior).

---

## Phase 6 — Discord verb parity (#03)

Discord wired in §0.6.

- [ ] In testbot's session, drive each verb against the test channel: `send_discord_message`, `send_briefing` (briefing template), `create_poll`, `create_thread`, `send_typing`, `edit_message`, `add_reaction`, `fetch_messages`, `download_attachments`, `list_channels`, `get_channel_info`
- [ ] For each: confirm the action lands in the channel as expected

**Pass criteria:** every verb works end-to-end against testbot's existing Discord channels.

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
