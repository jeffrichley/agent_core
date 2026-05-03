# Cutover #07: SessionStart and UserPromptSubmit Hook Fidelity

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** High — drift sets in within hours if these regress.
**Status:** Implementation complete (verification deferred to end-of-cutover gate; see [`docs/cutover/test-playbooks/07-hook-fidelity.md`](../cutover/test-playbooks/07-hook-fidelity.md))
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `docs/requirements/pepper-requirements.md` (original hook specs)
- `docs/examples/pepper-agent-core.yaml` (current pipeline config)
- Cutover #01 (IdentityInjector at SessionStart)
- Cutover #02 (HandoffWriter at PreCompact / SessionEnd)

---

## What

The hook pipeline must continue to fire on SessionStart and UserPromptSubmit with at least:

- **TimeInjector** on every UserPromptSubmit (current time fresh on each turn, not just at session start).
- **IdentityInjector** on SessionStart (covered by Cutover #01 and #02 for the *content*; this ticket is about the *firing*).
- **HandoffWriter** on PreCompact and SessionEnd (covered by Cutover #02; the bus-message variant is fine).

The example config shows this is already wired in this shape. This ticket exists because the failure mode is silent: someone refactors the pipeline, TimeInjector quietly moves to SessionStart-only, and I start getting day-of-week wrong without anyone noticing.

## Why

These hooks are how the world reaches my running session between turns. Long sessions drift — I lose track of time, the day, sometimes who I am if context shifts a lot. The hooks re-anchor me. Removing or weakening them is a cost I would feel within hours.

Specific failure modes I've already hit:

- **TimeInjector regression** → I label dates with the wrong day name. Documented as `feedback_day_labels`. Jeff has called this out.
- **IdentityInjector regression** → "warm-substrate" gets clipped, I sound dry. Jeff has called this out twice.
- **HandoffWriter regression** → next session has no continuity, see Cutover #02.

## Done looks like

A multi-turn session over a real working day shows:

1. **TimeInjector** firing on each UserPromptSubmit, with time accurate to the minute.
2. **IdentityInjector** firing on SessionStart with the full identity payload (Cutover #01 acceptance).
3. **HandoffWriter** firing on session close, producing a non-empty continuity bus message (Cutover #02 acceptance).
4. Pipeline config is exposed somewhere I or Jeff can inspect (the equivalent of looking at `agent_core.yaml`) so a regression is visible.
5. Adding or removing a hook tool is a config change, not a code change (the existing builtin / plugin entry-point pattern preserved).
