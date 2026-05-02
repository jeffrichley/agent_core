# Cutover #03: Discord Parity at the Verbs I Use

**Author:** Pepper
**Date:** 2026-05-02
**Priority:** High — without these I lose surface area Jeff actively depends on.
**Status:** Open (v1 partial; this list is the v2 must-have subset)
**Playbook implementer (default):** Folio
**PR / merge owner:** Cadence
**Parent:** `docs/requirements/pepper-pre-cutover-must-haves.md`
**Related:**
- `docs/ROADMAP.md` sub-project E (Discord)
- `packages/agent-core-discord/` (current v1 endpoint)

---

## What

The Discord endpoint must support, at minimum, these verbs end-to-end before cutover:

- `send_discord_message` (have, in v1)
- replies with `embed` (singular per existing memory `feedback_discord_embed_param`)
- `send_briefing` (templated daily briefing, channel-aware)
- `create_poll`
- `create_scheduled_event` / `cancel_scheduled_event` / `list_scheduled_events`
- `create_thread`
- `send_typing`
- `edit_message`
- `add_reaction`
- `fetch_messages`
- `list_channels` / `get_channel_info`
- `download_attachments`

This is intentionally *not* the full v2+ Discord surface (no voice, no advanced moderation). It is the smallest set that preserves the current working pattern between Jeff and me.

## Why

v1 lets me *exist* on Discord. This list is what lets me *function* on Discord — the difference between a bot and an EA.

- **Polls** are how Jeff and I make decisions when he can't or doesn't want to decide alone. The Pepper Design System logo concept rounds happened over polls.
- **Scheduled events** keep his calendar visible to people who'd otherwise email him about it.
- **Briefings** are the morning ritual. Without a templated `send_briefing` the format degrades to whatever I happen to type that day.
- **Threads** keep main channels uncluttered when I'm doing work that has many follow-ups.
- **Typing** indicators tell Jeff I'm working when something is taking >5s — without them, long operations look like I've stalled.
- **Reactions** let me acknowledge without spamming the channel ("👀" / "✅" / "🔍" instead of "I see this, I'll get to it").
- **`edit_message`** fixes a typo or updates status without leaving a trail of corrections.
- **`download_attachments`** is how Jeff hands me images. He sent me logo concept PNGs three times this week — losing this kills a primary working surface.

ROADMAP sub-project E lists the broader v2+. This ticket is the must-have subset; the rest can land post-cutover.

## Done looks like

Each verb has a smoke test against a real Discord guild (the Pepper guild or a test guild) that proves the bot can drive it correctly. Channel-aware tests cover at minimum:

- `#pepper-chat` (default daily briefing target)
- `#pepper-phd` (channel-context auto-load behavior)
- `#pepper-dreams` (channel-context auto-load behavior)

`send_briefing` specifically must produce output identical in shape to the current daily briefing format — same embed structure, same field ordering, same color codes (red 15548997 / yellow 16776960 conventions).
