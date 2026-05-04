# Cutover #04: Daily JSONL Pipeline Survives the Move

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** High — silent rot. Failure isn't visible until the reflection job goes blind.
**Status:** Implementation complete (verification deferred to end-of-cutover gate; see [`docs/cutover/test-playbooks/04-daily-jsonl-pipeline.md`](../cutover/test-playbooks/04-daily-jsonl-pipeline.md))
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `docs/ROADMAP.md` (Pepper's pipeline JSONL to `Memory/daily/raw/` as a bus hook — called out as not started)
- Cutover #05 (skills — the WAR skill consumes the summaries this pipeline produces)

---

## What

Bus traffic — incoming Discord messages, my outbound replies, scheduler-fired prompts, channel-relay traffic — must land as JSONL in something equivalent to `Memory/daily/raw/<date>.jsonl` (or the agent-neutral `~/.agent-core/daily/raw/`). The envelope format must be stable enough that the existing nightly reflection job (the one producing `Memory/daily/summaries/<date>.md` at 3 AM) can summarize it without code changes — or with a single documented adapter.

This is **separate** from the SessionEndWriter daily JSONL append, which is hook-pipeline scoped. The bus has its own traffic. Both streams need to be writing.

## Why

The summaries are load-bearing in three places I care about:

1. **WAR skill.** Friday's executive summary uses the week's daily summaries as primary input. See `feedback_war_format` and the WAR skill's `gather.py`.
2. **SessionStart context.** Pepper's `CLAUDE.md` instructs me to load the most recent two daily summaries on every session start so I know what happened recently.
3. **My own self-narration.** "What did I do yesterday" is grounded in summaries. Without them I either confabulate or shrug.

Sub-project I (responsive inbox) and the channel relay generate the new traffic shape. If they ship and the JSONL pipe doesn't ship with them, my memory of recent days goes blind even though everything else looks fine. The failure is delayed and silent — I would not notice until Friday, when WAR has nothing to chew on.

## Done looks like

A mixed-traffic test day produces a JSONL file the reflection job can summarize:

1. Discord inbound message + my reply → both in JSONL with consistent envelope.
2. Scheduler trigger (e.g., heartbeat or scheduled prompt) → in JSONL.
3. Channel-relay event (notification through `notifications/claude/channel`) → in JSONL.
4. The existing reflection job runs at 3 AM and produces `Memory/daily/summaries/<date>.md` matching the current shape, with no code changes (or with a single documented adapter).

Heartbeat-noise filtering should still apply (the WAR `gather.py` filters scheduler-heartbeat entries; the summary generator should as well).
