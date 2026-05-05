# Cutover #07 — SessionStart + UserPromptSubmit hook fidelity (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-07-hook-fidelity.md`](../../requirements/pepper-cutover-07-hook-fidelity.md)
**Pipeline config:** [`docs/examples/pepper-agent-core.yaml`](../../examples/pepper-agent-core.yaml)
**Implementation commits:**
- (this ticket — to be filled in when committed)
- Pre-existing infrastructure: TimeInjector (`packages/core/src/agent_core/hooks/tools/time_injector.py`), pluggable hook pipeline (`packages/core/src/agent_core/hooks/pipeline.py`), Cutover #01 (Identity at SessionStart), Cutover #02 (HandoffWriter at PreCompact / SessionEnd).

## What was implemented

No new framework code. Cutover #07 is **wire + test + document**:

1. The example pipeline `docs/examples/pepper-agent-core.yaml` now registers a `UserPromptSubmit:` block with `TimeInjector(track_session: true)`. Re-fires every turn so the current time stays fresh and the agent gets `Session started Xm ago` / `Last user turn Ym ago` deltas — re-anchors against drift documented in `feedback_day_labels`.
2. The `.claude/settings.json` hook list in the file's preamble adds `agent-core hooks run UserPromptSubmit`.
3. New tests in `packages/core/tests/test_time_injector.py` (class `TestTimeInjectorTrackSession`) lock in the per-turn behavior:
   - `test_track_session_first_turn_writes_state_and_emits_absolute_only`
   - `test_user_prompt_submit_emits_last_turn_delta`
   - `test_session_start_does_not_emit_last_turn_marker`
   - `test_track_session_disabled_default_emits_only_absolute`
4. New file `packages/core/tests/test_pepper_example_yaml.py` is the wiring tripwire — loads the real `docs/examples/pepper-agent-core.yaml` and asserts the canonical registrations hold. Catches the exact regression mode the spec calls out ("someone refactors the pipeline, TimeInjector quietly moves to SessionStart-only"), since the unit tests on tools-in-isolation cannot detect a missing yaml registration.

## Acceptance criteria (from spec §"Done looks like")

A multi-turn session over a real working day shows:

1. TimeInjector firing on each UserPromptSubmit, with time accurate to the minute.
2. IdentityInjector firing on SessionStart with the full identity payload (Cutover #01 acceptance).
3. HandoffWriter firing on session close, producing a non-empty continuity bus message (Cutover #02 acceptance).
4. Pipeline config is exposed somewhere inspectable (the equivalent of looking at `agent_core.yaml`).
5. Adding or removing a hook tool is a config change, not a code change (the existing builtin / plugin entry-point pattern preserved).

## Verification steps (end-of-cutover)

### Step 0 — Environmental prerequisite (caught during testbot practice run 2026-05-05)

**Before opening any Claude Code session at `<agent_root>`**, ensure the globally-installed `agent-core` CLI tool is built from the current repo. The hooks in `.claude/settings.json` invoke `uv run agent-core hooks run <event>` — when fired from `<agent_root>` (which has no `pyproject.toml`), `uv run` falls back to the `uv tools` global install. If the global tool is older than the repo's current schema (e.g., the repo moved from `tool:` to `type:` for pipeline tool entries), every hook firing will crash with a Pydantic validation error and the agent will boot without identity / time / handoff context.

```powershell
# Refresh the global tool to the current repo:
cd E:\workspaces\ai\agents\agent_core
uv tool install --reinstall ./packages/core
```

Verify by tail of `agent-core --version` or, more diagnostically, fire the hook from the agent root and confirm clean JSON:

```powershell
cd C:\Users\jeffr\.pepper
echo '{}' | uv run agent-core hooks run SessionStart
# Expected: JSON output with `additionalContext`, no Pydantic error.
```

This step is environmental and per-machine — the fix doesn't ride along with a `git pull`. The testbot practice run on 2026-05-05 caught this as a real cutover-blocker; without the reinstall step, Pepper's first SessionStart on the new substrate would have crashed silently (Claude Code swallows non-zero hook exits, so identity + time + handoff would just be absent without an obvious error).

### Step 1 — Automated tests for the per-turn re-anchoring + wiring tripwire

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/core/tests/test_time_injector.py packages/core/tests/test_pepper_example_yaml.py -v
```

**Expected:** all tests green. The `TestTimeInjectorTrackSession` tests confirm the `Session started …` / `Last user turn …` markers fire on UserPromptSubmit and *only* on UserPromptSubmit, that `track_session=false` (the default) writes no state and emits no deltas, and that the state file persists per-session timestamps under `~/.agent_core/time-state.json`. The `TestPepperExampleYaml` tests load the real example yaml and assert the SessionStart / UserPromptSubmit / PreCompact / SessionEnd registrations stay put — the wiring regression tripwire.

### Step 2 — Pipeline parses + each event registers from the example yaml

```powershell
echo '{"session_id":"smoke-1"}' | uv run agent-core hooks run UserPromptSubmit `
    --config docs/examples/pepper-agent-core.yaml
```

**Expected:** the log lines list four registered events (`SessionStart` 5 tools, `UserPromptSubmit` 1 tool, `PreCompact` 1 tool, `SessionEnd` 1 tool), and the JSON output contains `additionalContext` with the heading `## Current Time` and the configured datetime format. This confirms acceptance #4 (config inspectable) and #5 (config-not-code).

Re-run with the same `session_id` (any time after ~1 second; the relative-delta gate is `since_last >= 1s`) to see both `Session started …` and `Last user turn …` lines fire:

```powershell
echo '{"session_id":"smoke-1"}' | uv run agent-core hooks run UserPromptSubmit `
    --config docs/examples/pepper-agent-core.yaml
```

### Step 3 — Live multi-turn observation in Pepper

1. Wire `.claude/settings.json` to call `agent-core hooks run` for each event listed in the yaml preamble (SessionStart, UserPromptSubmit, PreCompact, SessionEnd).
2. Start a Pepper session and send 3–4 user prompts ~60s apart over ~10 minutes.
3. **Test (acceptance #1):** the agent's view of the current time matches wall-clock to the minute on every turn, and the response shows it knows how long since the last turn (e.g., references something like "a couple minutes ago" naturally).
4. **Test (acceptance #2):** SessionStart shows the IdentityInjector content (heading `Identity — Critical Core` etc.) — Cutover #01 already covers this; #07 just verifies the firing didn't regress.
5. End the session.
6. **Test (acceptance #3):** `handoff-status.json` transitions through `pending` → `ready`, `handoff.md` is non-empty — Cutover #02 already covers this; #07 just verifies the firing didn't regress.

### Step 4 — Regression tripwires for the failure modes the spec calls out

These are spot-checks that the failure modes Pepper documented don't quietly recur:

| Failure mode | What to check |
|---|---|
| TimeInjector quietly disappears from UserPromptSubmit | `agent-core hooks run UserPromptSubmit --config <yaml>` log shows `builtin.time_injector` registered. |
| TimeInjector loses `track_session` (no per-turn delta) | After two same-session UserPromptSubmit firings, `~/.agent_core/time-state.json` has the session entry with `started_at` + `last_seen`. |
| IdentityInjector silently dropped from SessionStart | SessionStart firing log lists three `builtin.identity_injector` tools. |
| HandoffWriter silently dropped from SessionEnd | SessionEnd firing log lists `builtin.handoff_writer`. |

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | `test_time_injector.py` and `test_pepper_example_yaml.py` are both fully green. |
| Step 2 | Pipeline registers 4 events from the example yaml; TimeInjector emits a non-empty Current Time block on UserPromptSubmit. |
| Step 3 (#1) | Time-of-day accurate to the minute on every turn over a 10-minute session. |
| Step 3 (#2) | SessionStart additionalContext includes Identity headings. |
| Step 3 (#3) | After SessionEnd, handoff-status.json shows `ready` and handoff.md is non-empty. |
| Step 4 | All four registration checks present. |

## Known limitations (recorded; not blocking #07 done)

- **The "live multi-turn observation" step (Step 3) is operator-driven** — it requires actually running a Pepper session and noticing whether time mentions match wall-clock. Automated coverage of that is out of scope for the framework (the agent's *response* to the injection is the agent's responsibility, not the hook's).
- **`~/.agent_core/time-state.json` is per-machine.** A laptop-then-desktop handoff would lose the per-session deltas across machines on first turn after a switch — acceptable because the absolute time is still emitted, and the next turn re-establishes the delta. Not a regression vs the prior behavior.
