# Testbot practice run — 2026-05-05

**Goal:** validate all 9 cutover playbooks end-to-end on testbot **before flipping Pepper**.

**Stop rule:** any RED step pauses the run. We diagnose before continuing — no "we'll come back to it." Pepper stays untouched until every step here is GREEN.

**Whose responsibility:**
- I (Claude) drive the file edits, test runs, and reporting.
- You (Jeff) drive the bits that need a human: starting the daemon, opening Claude Code in `~/.testbot/`, observing real-Pepper-style behavior, deciding on Discord.

---

## Phase 0 — Pre-flight setup

Manual setup since there's no `agent-core init` yet. Each item is small; the time is in being careful.

### 0.1 — Author `~/.testbot/agent_core.yaml`

- [ ] Clone `docs/examples/pepper-agent-core.yaml` to `~/.testbot/agent_core.yaml`
- [ ] Rebase paths onto `~/.testbot/` (set `vars.agent_root: C:\Users\jeffr\.testbot`)
- [ ] Drop or stub the Discord endpoint (we'll either wire it later or skip it)
- [ ] Keep all four hook tools in the pipeline: IdentityInjector, HandoffInjector, TimeInjector, HandoffWriter

**I'll do this and show you the diff before we move on.**

### 0.2 — Stand up `~/.testbot/Memory/` skeleton

- [ ] `~/.testbot/Memory/SOUL.md` — minimal, ~200 words, written in testbot's voice (a friendly QA engineer, not pretending to be Pepper)
- [ ] `~/.testbot/Memory/IDENTITY.md` — short identity rules block (the same "first person, don't auto-send, ask permission" framework)
- [ ] `~/.testbot/Memory/MEMORY.md` — empty index file, just frontmatter + heading
- [ ] `~/.testbot/Memory/OPERATIONS.md` — empty stub (referenced by SessionStart, OK to be empty)
- [ ] `~/.testbot/Memory/playbooks/morning_brief.md` — copy of `docs/examples/playbooks/morning-brief.md` with `${agent_root}` paths
- [ ] `~/.testbot/Memory/gather/morning.yaml` — copy of `docs/examples/playbooks/morning-gather.yaml`
- [ ] `~/.testbot/Memory/daily/summaries/` — empty directory
- [ ] `~/.testbot/Memory/daily/briefs/` — empty directory

**Why minimal:** we're testing whether the framework correctly delivers and routes content, not whether testbot has Pepper-quality identity. Two pages of testbot identity is enough to make the smoke tests meaningful.

### 0.3 — Wire `~/.testbot/.claude/settings.json` hooks

- [ ] Add `hooks` block with SessionStart, UserPromptSubmit, PreCompact, SessionEnd
- [ ] Each hook calls `agent-core hooks run <event> --config C:\Users\jeffr\.testbot\agent_core.yaml`
- [ ] Keep existing bus permissions (`mcp__agent-core__*`)

**I'll author the JSON and show you before saving.**

### 0.4 — Install skills (the manual workaround for missing init)

- [ ] Copy `packages/agent-core-briefs/src/agent_core_briefs/skills/briefs-author/SKILL.md` → `~/.testbot/.claude/skills/briefs-author/SKILL.md`
- [ ] Copy `packages/core/src/agent_core/skills/email/SKILL.md` → `~/.testbot/.claude/skills/email/SKILL.md` (only if we want #05's coverage to include `/email`)
- [ ] **Note:** this is the workaround. The eventual `agent-core init` step replaces it.

### 0.5 — Stage scheduler entry for morning_brief (#09 Step 6)

- [ ] Add a `JobDef` to testbot's scheduler config that publishes a `BriefRequest{brief_type=morning_brief}` event
- [ ] Confirm it's an `Event`-kind envelope (not `TextMessage`) — that's the cutover #09 path
- [ ] Set the cron expression for "soon enough that we can manually bump it when we get to Phase 3.4," OR plan to use `agent-core scheduler trigger <job-name>` if that command exists, OR just edit the cron to `*/1 * * * *` for the test window. We'll figure out the cleanest fire-on-demand approach during setup; the important thing is the JobDef is *registered* now so the scheduler→bus path is exercised.

### 0.6 — Discord endpoint for #03

Testbot's Discord app + token + channels already exist (we've been exercising them in prior validation work). Today's job is just to make sure the endpoint is wired in `~/.testbot/agent_core.yaml`.

- [ ] Confirm `TESTBOT_DISCORD_TOKEN` (or whatever the existing env var is) is in the environment the daemon will inherit
- [ ] Add `discord` endpoint block to agent_core.yaml, pointed at testbot's existing channels
- [ ] Note which channel(s) we'll use for the verb-parity drive in Phase 6 — we want one we can spam without anyone caring

### 0.7 — Daemon boot

- [ ] Run `agent-core daemon start` (or check it's already running per your existing setup)
- [ ] `agent-core daemon status` shows the agent-testbot endpoint registered
- [ ] `agent-core bus status` shows the daemon healthy

---

## Phase 1 — Hook + identity gate (#01, #02, #07)

These three share the SessionStart pipeline; testing them in sequence reuses the warm setup.

### 1.1 — CLI hook smoke (#01 Step 2)

```powershell
echo '{}' | uv run agent-core hooks run SessionStart --config C:\Users\jeffr\.testbot\agent_core.yaml
```

**Expected:** JSON with `additionalContext` containing testbot's SOUL/IDENTITY content, no `Pepper` text leaking from the framework, no truncation cap. **Pass = readable JSON, content present.**

### 1.2 — TimeInjector fires (#07)

```powershell
echo '{"prompt":"hi"}' | uv run agent-core hooks run UserPromptSubmit --config C:\Users\jeffr\.testbot\agent_core.yaml
```

**Expected:** `additionalContext` contains a current-time block. Run it twice 60 seconds apart, the second has the later time. **Pass = time advances correctly.**

### 1.3 — Fresh-session real Claude Code smoke (#01 Step 3)

- [ ] Fully quit any running Claude Code session
- [ ] Open Claude Code at `~/.testbot/`
- [ ] Send the first prompt: "introduce yourself"
- [ ] Verify testbot's first response: speaks in first person, identifies as testbot (not Pepper, not generic Claude), doesn't auto-send anything, can list the identity files it saw on turn one

**Pass criteria:** identity rules survive turn one. **Fail = identity got truncated or muddled.**

### 1.4 — Handoff observability scenarios (#02 Steps 4–6)

Three sub-scenarios on the running session:

**(a) Ready before boot:**
- [ ] Manually write a `handoff.md` and `handoff-status.json` (`status: ready`) to testbot's vault before opening Claude Code
- [ ] Open a new session
- [ ] First response should reference specific content from `handoff.md` unprompted

**(b) Pending at boot:**
- [ ] Set `handoff-status.json` to `{status: pending, session_id: <some-other-id>}`
- [ ] Open a new session — `session_id` mismatch means cross-session pending
- [ ] First response should reference the pending placeholder ("continuity still summarizing…"), not confabulate

**(c) Failed:**
- [ ] Set `handoff-status.json` to `{status: failed, error: "test failure"}`
- [ ] Open a new session
- [ ] First response references the failure + falls back to MEMORY.md / dailies, doesn't pretend nothing happened

**Pass criteria:** each scenario produces the documented placeholder behavior.

---

## Phase 2 — Bus + traffic (#04, #08)

### 2.1 — Daily JSONL pipeline (#04)

- [ ] Send a test bus message: `agent-core mailbox send --to agent-testbot --kind TextMessage --payload '{"text":"jsonl-test"}'`
- [ ] Tail `~/.agent-core/bus/raw/<today>.jsonl` and confirm the envelope landed
- [ ] Run `agent-core bus-log show --agent agent-testbot` and confirm the projected line is human-readable

### 2.2 — Notification surface (#08)

- [ ] In a running testbot session, fire a `HandoffReady` Event onto the bus from another shell
- [ ] Confirm the running session sees it (channel notification or system reminder on next prompt)
- [ ] Repeat with a `HandoffFailed` Event — same surface

**Pass criteria:** mid-session perception works for both `TextMessage` and `Event` kinds.

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
