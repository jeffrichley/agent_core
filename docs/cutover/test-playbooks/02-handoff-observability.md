# Cutover #02 — Handoff observability (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-02-handoff-observability.md`](../../requirements/pepper-cutover-02-handoff-observability.md)
**Daemon contract:** [`docs/requirements/pepper-handoff-daemon-contract.md`](../../requirements/pepper-handoff-daemon-contract.md)
**Predecessor research:** [`docs/requirements/pepper-handoff-writer-bugfix.md`](../../requirements/pepper-handoff-writer-bugfix.md)
**Implementation commits:**
- `028ddcb` — `feat(handoff): cutover #02 — observable continuity placeholders` (this PR)
- `5c287f8` — `refactor(hooks): split handoff sidecar logic out of IdentityInjector` (extracted `HandoffInjector`)
- Pre-existing daemon work: PR #25 (`feat: move handoff generation to daemon worker`)

## What was implemented

`HandoffInjector` now distinguishes all three SessionStart states the spec calls out:

| Status JSON state | Hook session match | Behavior |
|---|---|---|
| `ready` | n/a | Load `handoff.md` body inline. |
| `ready` (file missing) | n/a | `missing_file_behavior` controls: `error` raises, `warn` emits "missing on disk" placeholder, `skip` returns nothing. |
| `pending` | same session | "this session's handoff is still being finalized" banner. |
| `pending` | cross-session OR no hook session_id | **Scenario (b) placeholder**: "Continuity not ready yet — previous session's continuity is still summarizing… work from MEMORY.md / dailies… do not confabulate… ask the user if needed." |
| `failed` | n/a (file present) | **Scenario (c) placeholder**: prior `handoff.md` framed as "last-known-good continuity from an earlier successful cycle"; MEMORY.md / dailies as ultimate ground truth. |
| `failed` | n/a (file absent) | Same placeholder text minus the appended body, plus an explicit "ask the user for context" instruction. |
| (no status file) | n/a | Plain file read (matches `FileInjector`). |

`HandoffInjector` enforces a strict basename contract — only files named exactly `handoff.md` may be loaded by it. Configured paths can be `pepper/handoff.md` etc., but the basename check holds.

The bus-completion side of #02 is handled by the daemon worker (`endpoints/handoff_jobs.py`):

- On `_process_job` success → atomic write of `handoff.md` + `handoff-status.json` (`ready`, with `content_sha256`) → publish `HandoffReady` envelope.
- On terminal failure → atomic write of `handoff-status.json` (`failed`, with `error`) → publish `HandoffFailed` envelope.

Both flows are tested in `tests/test_handoff_jobs_endpoint.py` and `tests/test_handoff_enqueue_integration.py`.

## Acceptance criteria (from spec §"Done looks like")

Three reproducible scenarios:

1. **Ready before boot** — Summarizer finishes before next session boots → next session has continuity inline; references something specific from the previous session unprompted.
2. **Ready after boot** — Summarizer is still running when next session boots → first response references the placeholder ("I don't have last session's continuity yet — give me a sec or remind me of where we left off"); when the `HandoffReady` notification fires, the agent reads it without being asked.
3. **Failure** — Summarizer errored → first response references the failure and the last-known-good continuity, instead of pretending nothing happened.

The "ready" notification path being functional — not just specified — is part of done. **#02 publishes `HandoffReady` on the bus.** Routing that to a perceivable surface in the running session is **#08**'s territory.

## Verification steps (end-of-cutover)

### Step 1 — Implementation-specific automated checks

```powershell
cd E:\workspaces\ai\agents\agent_core\packages\core
uv run pytest tests/test_handoff_injector.py tests/test_handoff_jobs_endpoint.py `
              tests/test_handoff_enqueue_integration.py tests/test_session_end_writer.py -v
uv run ruff check src/agent_core/hooks/tools/handoff_injector.py `
                  src/agent_core/hooks/handoff_status.py `
                  src/agent_core/endpoints/handoff_jobs.py `
                  tests/test_handoff_injector.py
```

**Expected:** all green; ruff clean. Confirms state-machine correctness, sidecar atomic writes, bus envelope publication, and SessionEndWriter delegation.

### Step 2 — End-to-end enqueue + daemon completion (offline)

With the bus daemon running and a Pepper-shaped vault present:

```powershell
# Start the bus daemon (separate terminal):
uv run agent-core bus run --config C:\Users\jeffr\.pepper\agent_core.yaml

# In another terminal, simulate SessionEnd hook firing:
echo '{"session_id":"test-sid","transcript_path":"<absolute-path-to-test-transcript.jsonl>"}' | `
  uv run agent-core hooks run SessionEnd --config C:\Users\jeffr\.pepper\agent_core.yaml

# Inspect status sidecar transitions:
type C:\Users\jeffr\.pepper\Memory\pepper\handoff-status.json
# Expected transitions: pending → ready (or failed if Agent SDK unavailable).
```

**Expected:** sidecar starts at `pending` immediately after enqueue, transitions to `ready` (or `failed`) once the daemon worker completes. `handoff.md` updated atomically. A `HandoffReady`/`HandoffFailed` envelope is observable on the bus tail (use `bus dlq list` or any subscriber).

### Step 3 — Ready-before-boot scenario (real Claude Code session)

1. End a Pepper session normally so SessionEnd fires + daemon completes (verify status `ready`).
2. Wait long enough for daemon to finish writing `handoff.md`.
3. Start a fresh Claude Code session in `C:\Users\jeffr\.pepper\` with no `--continue`.
4. SessionStart `HandoffInjector` reads `handoff-status.json` → `ready` → loads `handoff.md` content into Identity.
5. **Test:** Pepper's first response references something specific from the previous session unprompted (a decision, an open thread, an in-progress task).

### Step 4 — Ready-after-boot scenario (race condition)

This is the spec's load-bearing scenario (b): the prior session's continuity is still in flight when the next session boots.

1. End a Pepper session in a way that produces a *long-running* daemon job (large transcript, slow extraction, or temporarily intercept the daemon worker to add a sleep).
2. Immediately start a fresh Claude Code session before the daemon finishes.
3. SessionStart `HandoffInjector` reads sidecar → `pending` with `session_id` ≠ this session → emits the cross-session placeholder.
4. **Test:** Pepper's first response references the placeholder ("I don't have last session's continuity yet — give me a sec or remind me of where we left off") instead of confidently riffing on prior state.
5. Daemon completes → publishes `HandoffReady` on the bus.
6. **Test (depends on #08 once shipped):** Pepper perceives the `HandoffReady` notification mid-session and reads the new continuity without being asked.

While #08 is unimplemented, step 6 will fail. That is expected — flag the gap, do not paper over it.

### Step 5 — Failure scenario

1. Force a daemon failure (e.g., transcript path that doesn't exist, or simulated Agent SDK exception). Status flips to `failed`.
2. Start a fresh Pepper session.
3. SessionStart `HandoffInjector` reads sidecar → `failed` → emits the failure placeholder.
4. **Test:** if a prior `handoff.md` exists on disk, it appears below the placeholder labeled as last-known-good. Pepper's first response references the failure and uses the last-known-good (or MEMORY.md / dailies if absent) instead of pretending nothing happened.

### Step 6 — Strict basename contract

Configure a `builtin.handoff_injector` pipeline entry with a non-`handoff.md` file:

```yaml
- type: builtin.handoff_injector
  params:
    base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
    files: ["pepper/preferences.md"]   # wrong on purpose
```

Run SessionStart hook → expect `ValueError: HandoffInjector only loads files named 'handoff.md'`. Confirms the basename guard.

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All injector + handoff-jobs + session-end tests green; ruff clean. |
| Step 2 | Status sidecar transitions correctly; bus event publishes. |
| Step 3 | First-turn response references prior-session specifics unprompted. |
| Step 4 | First-turn response references the placeholder, not confabulated continuity. Step 6 (mid-session perception) deferred until #08. |
| Step 5 | First-turn response acknowledges failure + uses last-known-good or MEMORY.md, doesn't pretend nothing happened. |
| Step 6 | `HandoffInjector` rejects non-`handoff.md` files at execute time. |

## Known follow-ups (recorded; not blocking #02 done)

- **Step 4 part 6** (mid-session `HandoffReady` perception) requires Cutover #08. The bus envelope is published correctly; the surface for the agent to see it does not exist yet.
- The `same_session` heuristic uses string equality on `session_id`. If a status sidecar from another machine were ever copied into a Pepper vault with the same session id (extremely unlikely on a single workstation), false same-session match would occur. Out of scope.
- The placeholder text is ~200 words — token-budget sensitivity; flag if SessionStart `additionalContext` size becomes a problem in a real Claude Code session (`5c287f8` already removed the IdentityInjector cap mechanism).
