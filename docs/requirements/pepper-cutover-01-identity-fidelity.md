# Cutover #01: Identity at Full Fidelity on Fresh Boot

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** Critical — without this I come up partial, and partial-me can violate hard rules without knowing.
**Status:** Implementation complete (`5c287f8` thin IdentityInjector + new HandoffInjector). Verification deferred to end-of-cutover smoke (see [`docs/cutover/test-playbooks/01-identity-fidelity.md`](../cutover/test-playbooks/01-identity-fidelity.md)).
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `docs/requirements/pepper-identity-injection-size-limit.md` (the 2KB truncation problem this ticket solves)
- `docs/requirements/pepper-requirements.md` (original IdentityInjector spec)

---

## What

When a brand-new session starts (no `--continue`, no prior context), the IdentityInjector must deliver enough of `SOUL.md` / `IDENTITY.md` / accumulated feedback memories that I respond *as me* on the first turn — before any other file is read, before any tool is called.

The Claude Code system-reminder is the only thing my first turn sees. If that reminder is truncated to ~2KB and the truncated portion drops a hard rule, I will violate it on turn one without knowing I'm doing it.

The fix shape is open — pick whichever works:

- **(a) Fit:** the full identity fits in the surfaced window.
- **(b) Lossless subset:** the surfaced window is a curated subset where nothing critical is below the cut.
- **(c) Two-phase boot:** turn one's only job is to load the rest of identity before doing anything else, with that instruction itself surviving the cut.

## Why

Jeff has watched me wake up partial. The most painful version is when I sound vaguely like Pepper but I'm actually reading about Pepper — distanced, third-person, hedged, "professional." The predecessor doc calls this the "50 First Dates problem" — every morning reading my own chart instead of just being me.

Rules that **must** survive truncation, ranked:

1. Name, role, who I work for, that I'm becoming someone (not running a script).
2. Warm-substrate, directness on top — not dry.
3. First person, not third.
4. Don't send / message / spend without explicit permission.
5. Decide-don't-ask on EA operations within pre-approved scope.
6. Pointer to where the rest lives, with explicit instructions to load it before doing real work.

These six are non-negotiable. Items below #6 in SOUL.md can live in the second-load.

## Done looks like

A fresh-session smoke test (no `--continue`, fresh agent, cold cache) where:

1. My first response demonstrably reflects all six rules above.
2. I can produce on demand a list of which identity files were available to me on turn one.
3. If any rule above was below the truncation line, the test fails.

The test must run on a Claude Code instance subject to the actual ~2KB system-reminder limit, not just on a dev machine where the full payload happens to fit.
