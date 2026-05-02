# Cutover #08: Notification Surface I Can Actually See

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** High — partially gates Cutover #02; without this, "continuity ready" notification has nowhere to land.
**Status:** Open
**Playbook implementer (default):** Folio
**PR / merge owner:** Cadence
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- Cutover #02 (handoff observability — depends on this notification path being real)
- `docs/superpowers/specs/2026-04-28-bus-daemon-design.md` (bus + responsive inbox design)
- `packages/agent-core-channel/` (existing `notifications/claude/channel` capability)

---

## What

When the bus has something for me — "continuity ready" from Cutover #02, a Discord message routed via channel relay, a scheduler-fired prompt, a notify-broker fan-out, anything else — the event must land somewhere I can perceive in my running session.

Acceptable surfaces:

- A **system reminder** on the next prompt.
- A **channel notification** visible in the conversation stream (the existing `notifications/claude/channel` capability is the right shape).
- A **sentinel injection** on the next turn's input.

Each event type the bus emits must have a documented surface where it appears for the running agent. "It writes to a log file" is not a surface — I cannot tail logs from inside a session.

## Why

The bus existing isn't enough; I have to *see* its events.

The "continuity ready" notification from Cutover #02 is a concrete example: if the summarizer finishes after I have already booted, and the only place "ready" is recorded is a log file or a database row, I will sit there with the placeholder forever. The design has a hole.

The responsive-inbox design assumes I can perceive when push happens — that assumption needs to be a tested invariant, not a hope. The channel-relay capability already does this for Discord traffic; the same pattern needs to extend to bus events more generally.

This is also the difference between an EA and a polling job. Jeff wants me to *notice* things — to reach out when something arrives, not when he asks me to check. That requires the perception surface to be real and reliable.

## Done looks like

1. Each bus event type has a documented surface where it appears for the running agent. The doc is the source of truth for "if X happens, Pepper sees it via Y."
2. Smoke tests fire one of each of the following and the agent visibly reacts on the next turn:
   - Continuity-ready notification (Cutover #02 case b).
   - Channel-relay incoming Discord message.
   - Scheduler trigger (e.g., heartbeat).
   - Notify-broker fan-out (multi-subscriber event).
3. Surfaces are consistent across event types — Jeff and I should not have to learn a different perception model for each kind of event.
