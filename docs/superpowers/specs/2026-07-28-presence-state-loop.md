# Presence Awareness — The State Loop (Watcher)

**Date:** 2026-07-28
**Author:** Wren (design converged with Jeff over the 2026-07-28 conversation)
**Status:** Design approved (Jeff delegated: "write the spec and auto approve it")
**Amends:** the umbrella `2026-07-27-presence-awareness-design.md`; builds directly on Phase 1 (hook) + Phase 2 (recognition).

---

## Goal

Close the gap between the two halves that already exist: Phase 2 proved recognition works in a one-shot CLI, and Phase 1's hook reads a `state.json` that nothing writes. This spec is **the bridge** — a hand-started **watcher** process that reads the camera on a cadence, recognizes the faces in each frame, maps them to the presence-state contract, and atomically writes `state.json`. The Phase-1 hook already turns that into per-being caution.

The load-bearing outcome (Jeff's stated core goal): **Wren and Pepper know when it isn't just Jeff** — anyone-not-Jeff present raises the signal.

---

## Locked design decisions (from the 2026-07-28 conversation)

- **Largest face = the desk-seat identity.** It's a desk cam, so the closest person (largest face) is the one who could be typing — the trust-relevant identity. That face decides `at_desk`.
- **Small faces are KEPT.** A distant person is exactly what to notice ("someone's around"). No min-face-size gate.
- **Low-confidence / non-Jeff faces are "present but not trusted."** They count toward presence (`unknown_count`) but never grant Jeff-trust. Uncertainty always lands cautious (safety-additive).
- **Fails in the safe direction.** The largest-face heuristic breaks only when someone leans in very close behind Jeff — and then the largest face isn't confidently Jeff, so it de-escalates. Worst case is briefly over-cautious, never over-trusting.
- **v1 = manual start** (`watch` CLI, run by hand). Auto-spin/lock/service deferred (umbrella).
- **Plaintext template still** (Phase-2 spike decision). Template encryption remains the gate before any live-wire (that's the next phase).

---

## Scope

- **In:** a pure faces→state **aggregator**; a **watcher loop** that owns a `CameraSession`, recognizes each frame, aggregates, and writes `state.json` on a cadence, degrading errors safely; a `watch` CLI subcommand.
- **Out (deferred):** tracking / Bayesian smoothing (deferred indefinitely on the low-jitter evidence); the Tier-0 motion gate (recognition every cycle is cheap enough at a 2s cadence); auto-spin-up / single-instance lock / service; template encryption; the snapshot-tool refactor; live-wire into the uv-tool env + `agent_core.yaml` (all later).

---

## Architecture & components (all in `agent-core-webcam/presence/`)

- **`aggregate.py`** *(new, pure)* — `aggregate(faces, *, principal, source, now) -> PresenceState`. Given the per-face recognition results of one frame, produce a `PresenceState`. This is where the locked mapping lives; it is pure and fully unit-testable.
- **`watcher.py`** *(new)* — `run_watch(...)`: opens a `CameraSession` (Phase-2 reuse), then loops: read frame → `embed_faces` → per-face `match_embedding` → `aggregate` → `write_state` → sleep(interval). Per-cycle errors are caught and the write is skipped (never crashes the loop); if errors persist the file goes stale and the hook's staleness guard degrades to "unknown." Seams are injectable so the loop is testable without a camera or model. A bounded-iterations parameter lets tests run a fixed number of cycles.
- **`cli.py`** *(extended)* — a `watch` subcommand: `python -m agent_core_webcam.presence.cli watch --name jeff --interval 2 --threshold 0.5`.

### The faces→state mapping (the aggregator)

Each detected face carries a verdict (`principal` or `"unknown"`, from Phase-2 `match_embedding`) and its bbox. For one frame:

- **No faces:** `at_desk=False, known=[], unknown_count=0` (nobody here).
- **Otherwise:**
  - `at_desk` = the **largest** face (by bbox area) is confidently the principal. ("Jeff is the one at the desk.")
  - `known` = `[principal]` if the principal is confidently recognized **anywhere** in frame (even small in the background), else `[]`.
  - `unknown_count` = count of faces **not** confidently the principal (strangers + low-confidence).

This composes exactly with the Phase-1 `classify` (`principal_present = at_desk AND principal in known`; `unknown_present = unknown_count > 0`):

| Scene | at_desk | known | unknown_count | Hook result |
|---|---|---|---|---|
| Jeff alone at desk | True | [jeff] | 0 | trusted, calm |
| Jeff at desk + stranger behind | True | [jeff] | 1 | trust Jeff's instructions, **+ shoulder-surf** |
| Stranger at desk, Jeff away | False | [] | 1 | **de-escalate** |
| Stranger at desk, Jeff small in bg | False | [jeff] | 1 | **de-escalate** (Jeff isn't driving) |
| Empty desk | False | [] | 0 | de-escalate (no one confirmed) |

Every "not just Jeff" scene raises `unknown_count > 0` and/or drops `principal_present` — exactly the goal.

### Data flow

```
CameraSession (open once) → loop every ~interval s:
  read_bgr → embed_faces (buffalo_s) → per face: match_embedding vs Jeff's template
    → aggregate(faces) → PresenceState → write_state(atomic) → ~/.agent-core/presence/state.json
                                                                    │
   Phase-1 presence_injector hook (already built) reads it → injects per-being caution
```

---

## Error handling — safe by construction

- **Per-cycle exception** (frame read fails, recognition throws): caught, logged, write skipped, loop continues. No crash.
- **Persistent failure** (camera unplugged/busy): writes stop → file ages past `max_age_seconds` → the Phase-1 staleness guard → "unknown" → cautious.
- **Watcher not running at all:** file missing/stale → hook → "unknown." Single-instance is "Jeff starts one" (v1 manual start).
- There is no failure path that makes a being *less* cautious.

---

## Testing

1. **Pure aggregator (deterministic, no camera/model):** each row of the mapping table above — no faces; Jeff alone; Jeff + stranger; stranger-at-desk-with-Jeff-in-bg; all-unknown; largest-face-decides-`at_desk`; `updated_at`/`source` set from params.
2. **Watcher loop (fakes, no camera/model):** inject a fake `CameraSession` + fake `embed_faces`/`match_embedding`; run N bounded iterations against a temp `state_path`; assert the written `state.json` matches expected across a scripted sequence of frames; assert a per-cycle error **skips the write and continues** (loop survives, prior state left intact / next good frame recovers).
3. **`watch` CLI wiring:** monkeypatch `run_watch`, assert argparse passes the parsed options through.
4. **Live (with Jeff, manual):** run `watch` on the real camera; confirm `state.json` updates as he sits / leaves / a second person enters; confirm the Phase-1 hook, pointed at that file, renders the right guidance. (Not claimed working until observed live.)

---

## Deferred / out of scope (explicit)

- Tracking + Bayesian smoothing (deferred indefinitely — low observed jitter)
- Tier-0 motion gate
- Auto-spin-up / single-instance lock / supervised service / idle self-shutdown
- Template encryption (still plaintext; required before live-wire)
- Snapshot-tool refactor through the watcher
- Live-wire into the uv-tool env + `agent_core.yaml` (the deliberate, reversible step — its own gate)
