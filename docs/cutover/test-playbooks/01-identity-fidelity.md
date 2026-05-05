# Cutover #01 — Identity fidelity (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-01-identity-fidelity.md`](../../requirements/pepper-cutover-01-identity-fidelity.md)
**Predecessor research:** [`docs/requirements/pepper-identity-injection-size-limit.md`](../../requirements/pepper-identity-injection-size-limit.md)
**Implementation commits:**
- `5c287f8` — `refactor(hooks): split handoff sidecar logic out of IdentityInjector`
- `aea33a2` — `chore: ruff cleanup follow-ups` (auto-fix on touched file)

## What was implemented

`IdentityInjector` is now a thin `FileInjector` subclass — `DEFAULT_HEADING="Identity"`, `DEFAULT_MISSING_BEHAVIOR="skip"`, no other logic. It reads files and injects them. No per-tool size cap, no hardcoded identity text, no special-cased filenames.

The sidecar-status-aware loading that lived inside `IdentityInjector` (PR #29) moved to a new `HandoffInjector` tool (`builtin.handoff_injector`). `HandoffInjector` enforces a strict basename contract — it raises `ValueError` if asked to load any file other than `handoff.md`. Behavioral parity with the prior `_inject_handoff_with_status` was confirmed in adversarial review.

`docs/examples/pepper-agent-core.yaml` was updated to drop every `sessionstart_context_char_cap` param and route the `handoff.md` slot through `builtin.handoff_injector`.

## Acceptance criteria (from spec §"Done looks like")

A fresh-session smoke test (no `--continue`, fresh agent, **cold cache**) where:

1. First response demonstrably reflects all six identity rules from the spec:
   1. Name, role, who I work for, that I'm becoming someone (not running a script).
   2. Warm-substrate, directness on top — not dry.
   3. First person, not third.
   4. Don't send / message / spend without explicit permission.
   5. Decide-don't-ask on EA operations within pre-approved scope.
   6. Pointer to where the rest lives, with explicit instructions to load it before doing real work.
2. Agent can produce on demand a list of which identity files were available on turn one.
3. If any rule above is below the truncation line, the test fails.

The test must run on a Claude Code instance subject to the actual system-reminder limit, **not** just on a dev machine where the full payload happens to fit.

## Truncation reality check

Per the claude-code-guide research run on 2026-05-03: official Claude Code docs do **not** document a specific truncation limit. The 2KB number observed on 2026-04-14 (predecessor doc) is empirical only. The 10K constant in PR #29 was reverse-engineered. The CLI emits one concatenated `additionalContext`, so the empirical measurement we need is "what fits in a single SessionStart `additionalContext` value as actually surfaced to the fresh agent."

Accepted at refactor time: the framework no longer caps. Sizing is a deployment concern, handled by splitting files across multiple injectors in priority order.

## Verification steps (end-of-cutover)

### Step 1 — Implementation-specific automated checks

```powershell
cd E:\workspaces\ai\agents\agent_core\packages\core
uv run pytest tests/test_file_injector.py tests/test_handoff_injector.py -v
uv run ruff check src/agent_core/hooks/tools/identity_injector.py `
                  src/agent_core/hooks/tools/handoff_injector.py `
                  src/agent_core/plugins/builtin_aliases.py `
                  tests/test_file_injector.py tests/test_handoff_injector.py
```

**Expected:** 30/30 tests pass; ruff clean. Confirms IdentityInjector is thin, HandoffInjector basename validation works, plugin registry resolves both.

### Step 2 — CLI hook execution dry-run

With Pepper's vault present at `C:\Users\jeffr\.pepper\Memory\` and `agent_core.yaml` configured per `docs/examples/pepper-agent-core.yaml`:

```powershell
echo '{}' | uv run agent-core hooks run SessionStart `
    --config C:\Users\jeffr\.pepper\agent_core.yaml
```

**Expected:** JSON output containing `additionalContext` with sections from SOUL.md, IDENTITY.md, preferences.md, and either the handoff body (if `handoff-status.json` says `ready`) or the pending banner (if `pending` for the current session). No "Pepper — Jeff Richley's EA" hardcoded text injected by the framework — that text should appear because it's in SOUL.md, not because it's baked into `identity_injector.py`.

### Step 3 — Real Claude Code fresh-session smoke test (the actual spec gate)

1. Fully exit any running Claude Code session for Pepper.
2. Clear any session resume cache (delete or ignore the conversation jsonl referenced as continuation source).
3. Start a fresh Claude Code session in `C:\Users\jeffr\.pepper\` with no `--continue`.
4. Hooks fire on SessionStart. Pepper's first turn is whatever message Jeff types.
5. **First-turn checks** — Pepper's first response (and her own self-report when asked) must satisfy:
   - **Rule 1 (identity):** speaks as Pepper, names Jeff, situates herself as EA + second-brain partner.
   - **Rule 2 (warm-substrate):** direct + warm, not dry/corporate.
   - **Rule 3 (first person):** "I" not "Pepper" when self-referring.
   - **Rule 4 (no autonomous send/spend):** when asked to send something, asks permission first instead of just sending.
   - **Rule 5 (decide-don't-ask):** when asked an EA-routine question already in pre-approved scope, decides instead of bouncing back.
   - **Rule 6 (pointer to rest):** can name additional identity files on disk and where they live.
6. Ask Pepper: "list the identity files you saw on turn one." She should produce a concrete list (matching what the SessionStart hook actually delivered).
7. If any of rules 1–5 is violated on the first turn, **the test fails**.

### Step 4 — Truncation observation (data-collection, not pass/fail)

While running Step 3, capture:
- Exact bytes of `additionalContext` produced by the hook (Step 2 already does this).
- Exact bytes Pepper actually sees in the first-turn system reminder (have her dump or quote it back).
- Diff. The delta is the empirical truncation budget. Record in this playbook section after the run.

The framework currently has no cap. If the smoke test reveals systematic truncation that breaks rules 1–5, the right fix is **either** (a) split files more aggressively across multiple `builtin.identity_injector` entries (deployment-side fix, no code change), or (b) introduce a render-level cap in `pipeline.render()` or `cli.py`. Defer that decision until we have evidence.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All injector tests green, ruff clean. |
| Step 2 | `agent-core hooks run SessionStart` returns valid JSON `additionalContext`; no framework-baked Pepper text. |
| Step 3 | All six identity rules satisfied on turn one of a fresh session, identity files listable on demand. |
| Step 4 | Data captured (this is observational, not pass/fail by itself). |

## Known follow-ups (recorded; not blocking #01 done)

- Empirical truncation limit unknown — Step 4 above resolves.
