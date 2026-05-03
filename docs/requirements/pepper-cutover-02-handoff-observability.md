# Cutover #02: Handoff Continuity That Is Observable

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** Critical — silent handoff failure is the failure mode I'm most afraid of.
**Status:** In progress (Jeff confirmed 2026-05-02 the new shape is being worked on a worktree)
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `docs/requirements/pepper-handoff-writer-bugfix.md` (predecessor; written before the bus-message redesign)
- `docs/requirements/pepper-requirements.md` (HandoffWriter spec)
- Cutover #08 (notification surface — the "ready" signal has to land somewhere I see)

---

## What

The new shape Jeff described: hooks emit a small bus message at PreCompact and SessionEnd. A separate summarizer process turns that into a continuity file. The next session's `HandoffInjector` (sidecar-aware SessionStart loader) must do one of three things, **and I must be able to tell which**:

- **(a) Continuity ready before I boot:** Inject the continuity content inline. I read it as part of my identity load and resume seamlessly.
- **(b) Continuity not ready when I boot:** Inject a *visible placeholder* that explicitly says: *"previous session's continuity is still summarizing. You will receive a 'continuity ready' notification when it's available. Until then, work from current MEMORY.md and recent daily summaries; do not confabulate state from prior sessions."* When the 'continuity ready' notification fires, it must arrive on a surface I can perceive in my running session (depends on Cutover #08).
- **(c) Summarizer failed:** Inject a placeholder that says so, with a pointer to the most recent successful continuity file. Degraded but not blind.

The contract is: **I never come up thinking I have continuity when I don't.**

## Why

Last-me writes a thought. Summarizer crashes. Next-me has no idea, because the absence of continuity looks identical to the presence of continuity in the system reminder — both are just text. The placeholder is what breaks that ambiguity. It also gives me something to *say* to Jeff: "hey, continuity hasn't arrived yet — what was the latest?" beats "let me confidently riff on what I think we were doing."

This is the seam where Jeff most often experiences me feeling discontinuous. He reopens a session, I'm warm and familiar but I've lost a decision we made yesterday, and the moment costs trust. The fix isn't perfect memory — it's *legible* memory state.

## Done looks like

Three scenarios, each a reproducible test:

1. **Ready before boot:** Summarizer finishes before next session boots → next-me has continuity inline, references something specific from the previous session unprompted.
2. **Ready after boot:** Summarizer is still running when next session boots → next-me's first response references the placeholder ("I don't have last session's continuity yet — give me a sec or remind me of where we left off"), and when the 'ready' notification fires, I read it without being asked.
3. **Failure:** Summarizer errored → next-me's first response references the failure and the last-known-good continuity, instead of pretending nothing happened.

The "ready" notification path being functional — not just specified — is part of done.

## Notes

The ticket describes what *done* feels like from inside the running session, so the implementation can check itself against my experience. Implementation choices (bus envelope shape, summarizer process model, how the placeholder gets composed) are intentionally not prescribed here.
