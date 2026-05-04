# Cutover #03 — Discord verb parity (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-03-discord-verb-parity.md`](../../requirements/pepper-cutover-03-discord-verb-parity.md)
**Implementation commits:**
- `ac3cbd0` feat(discord): cutover #03 verb parity — cherry-pick PR #31 (briefing.py canonical embed builder)
- `d97fc8e` feat(discord): cutover #03 verb parity — dispatch + tests + fakes (companion files for the cherry-pick)
- `954b589` fix(discord): apply cutover #03 review feedback — Z-parse + channel-type guard + validation

## What was implemented

The Discord endpoint now dispatches the full verb set the cutover #03 spec calls out. Every verb routes through `DiscordEndpoint._dispatch` in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, with pydantic args validation in `packages/agent-core-discord/src/agent_core_discord/args.py`.

**Verbs reachable via `_dispatch` (13 total):**

- `send` — outbound text/embed message (existing v1 surface).
- `edit` — replace content / embeds on a prior message.
- `react` — add a reaction emoji.
- `fetch` — fetch recent messages with optional `before` cursor.
- `download_attachments` — write attachment bytes to the configured per-endpoint dir.
- `list_channels` — guild's accessible channels.
- `get_channel_info` — single-channel metadata.
- `send_briefing` — canonical embed shape used for daily briefings.
- `create_poll` — multi-answer poll (≥2 answers, capped duration).
- `create_scheduled_event` / `cancel_scheduled_event` / `list_scheduled_events` — guild events for `external` / `voice` / `stage`.
- `create_thread` — public thread off a parent message.
- `send_typing` — bounded typing indicator (0.5–10s).

**Pepper-facing aliases (`_TOOL_ALIASES` in `endpoint.py`):**

| Pepper-facing name        | Internal dispatcher key |
|---------------------------|-------------------------|
| `send_discord_message`    | `send`                  |
| `edit_message`            | `edit`                  |
| `add_reaction`            | `react`                  |
| `fetch_messages`          | `fetch`                  |

`_canonical_tool` resolves the alias before lookup; the four other Pepper-named verbs (`send_briefing`, `create_poll`, `create_thread`, `send_typing`, plus the three scheduled-event verbs) match their internal names directly.

**Canonical briefing embed shape (`build_briefing_embeds` in `briefing.py`):**

- Header embed (date_line + focus + calendar) — neutral.
- Critical-items embed — color `BRIEFING_COLOR_CRITICAL = 15548997` (red), surfaces only when `critical_items` is non-empty.
- Warning-items embed — color `BRIEFING_COLOR_WARNING = 16776960` (yellow), surfaces only when `warning_items` is non-empty.
- Discord's per-message 6000-char total cap enforced inside the endpoint via `_embed_char_count` before send; over-cap surfaces a `_ToolError` rather than a discord.py `HTTPException`.

**Validation surface (pydantic models in `args.py`):**

- `_CreatePollArgs.answers`: `min_length=2`, `max_length=10`.
- `_CreatePollArgs.duration_hours`: `ge=1, le=168`.
- `_SendTypingArgs.duration_seconds`: `ge=0.5, le=10.0`.
- `_CreateScheduledEventArgs._entity_fields` cross-field validator: `external` requires non-empty `location` + `end_time`; `stage` / `voice` require `channel_id`.
- The dispatcher's `_v(model, raw)` wraps pydantic `ValidationError` as `_ToolError`, which surfaces back to the bus as an `error: ...` Acknowledgment with `urgency=yellow`.

**Channel-type guard (added in `954b589`):** `_create_scheduled_event` rejects `entity_type="stage"` against a non-`stage_voice` channel and `entity_type="voice"` against a non-`voice` channel before the API call, with a `_ToolError` message naming the actual vs expected `discord.ChannelType` enum name.

**Trailing-Z ISO parser (tightened in `954b589`):** `_parse_iso_datetime` rewrites only a *trailing* `Z` to `+00:00`. The earlier blanket `str.replace("Z", "+00:00")` mangled inputs that legitimately contained `Z` elsewhere; well-formed ISO inputs are unaffected.

## Acceptance criteria (from spec §"Done looks like")

> Each verb has a smoke test against a real Discord guild (the Pepper guild or a test guild) that proves the bot can drive it correctly. Channel-aware tests cover at minimum:
>
> - `#pepper-chat` (default daily briefing target)
> - `#pepper-phd` (channel-context auto-load behavior)
> - `#pepper-dreams` (channel-context auto-load behavior)
>
> `send_briefing` specifically must produce output identical in shape to the current daily briefing format — same embed structure, same field ordering, same color codes (red 15548997 / yellow 16776960 conventions).

## Verification steps (end-of-cutover)

### Step 1 — Automated unit + integration tests

```powershell
cd E:\workspaces\ai\agents\agent_core
uv run pytest packages/agent-core-discord/tests -q
```

**Expected:** all tests green. Baseline going into this playbook is **130 passed, 1 skipped** (the skip is `test_real_bot_send_react_edit_via_bus`, gated on a real-token env var). The 130 covers every verb listed above with happy-path + validation-error + channel-type-guard + ISO-parse cases.

Specific files / suites that pin parts of #03:

- `tests/test_endpoint_outbound.py` — happy-path coverage for every verb in `_dispatch`, plus the trailing-Z parser, the stage/voice channel-type guard, and the validator-rejection contracts (poll, typing, external-without-location, stage-without-channel_id).
- `tests/test_endpoint_outbound.py::test_build_briefing_embeds_order_and_colors` — locks the canonical embed shape (header + critical-red + warning-yellow ordering) and the spec-mandated color codes.
- `tests/test_endpoint_outbound.py::test_send_briefing_sends_embeds` — drives `_dispatch` end-to-end through `_send_briefing` → `_send` and asserts the embeds list lands on the channel.
- `tests/test_endpoint_outbound.py::test_send_discord_message_alias` — exercises the Pepper-facing alias path through `_canonical_tool`.

### Step 2 — Real-guild smoke (cutover-window verification)

This step requires a live Discord token + guild access and is therefore deferred to the cutover window itself, mirroring the pattern in [`04-daily-jsonl-pipeline.md`](04-daily-jsonl-pipeline.md) (live mixed-traffic day) and [`06-vault-continuity.md`](06-vault-continuity.md) (operator vault move). With `agent-core` running and the Pepper guild (or a test guild) reachable, drive each verb and observe the result:

| # | Verb | Smoke check |
|---|------|-------------|
| 1 | `send_discord_message` | Send "ping" to `#pepper-chat`. Message lands. |
| 2 | `send` with single embed | Send `{"title":"hi","description":"world"}` to `#pepper-chat`. Embed renders. |
| 3 | `send_briefing` | Send to `#pepper-chat` with non-empty `critical_items` and `warning_items`. **Operator compares the rendered output side-by-side against Pepper's legacy renderer's output** (this is the byte-parity check the spec calls for; see "Known limitations" for why this lands here rather than in a unit test). |
| 4 | `create_poll` | 3 answers, 24h duration. Poll renders, votable. |
| 5 | `create_scheduled_event` | `entity_type="external"`, `location="https://meet.example/x"`, `end_time` 1h after start. Event appears in the guild's Events tab. |
| 6 | `cancel_scheduled_event` | Pass the id from #5. Event status shifts to `cancelled` (or disappears from active list). |
| 7 | `list_scheduled_events` | Returns ≥1 entry covering #5 (before #6) and 0 entries covering #5 (after #6). |
| 8 | `create_thread` | From a recent message in `#pepper-chat`. Thread appears. |
| 9 | `send_typing` | `duration_seconds=5.0`. Typing indicator visible for ~5s. |
| 10 | `edit_message` | Edit text on a prior message. Update lands without a "(edited)" race. |
| 11 | `add_reaction` | `emoji="🎉"`. Emoji appears on the target message. |
| 12 | `fetch_messages` | `limit=5`. Returns the 5 most recent. |
| 13 | `list_channels` | Guild channels enumerated. |
| 14 | `get_channel_info` | Pass a known channel id. Returns dict with `id`, `name`, `type`. |
| 15 | `download_attachments` | Against a message Jeff sent with an image. Bytes land at `~/.agent-core/attachments/<endpoint>/<message-id>/...`. |

### Step 3 — Channel-context auto-load behavior

The spec calls out `#pepper-phd` and `#pepper-dreams` as channels with channel-context auto-load behavior. `packages/agent-core-discord/src/agent_core_discord/access.py` owns the access config that wires this. In each channel, send a message to Pepper and confirm the right context loads (this is mostly an integration smoke against the access config that already shipped pre-#03; the cutover #03 changes do not touch `access.py`).

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | 130+ green in `packages/agent-core-discord/tests` (baseline 130 + 1 skip; cherry-pick added 8, review-feedback added 10). |
| Step 2 | Every row in the verbs table renders the expected behavior in the live guild; `send_briefing` matches the legacy daily-briefing shape on visual inspection. |
| Step 3 | A message in `#pepper-phd` and `#pepper-dreams` loads the right channel-context per `access.py`. |

## Known limitations (recorded; not blocking #03 done)

- **`send_briefing` byte-for-byte parity vs Pepper's legacy renderer is unverifiable until cutover.** The legacy renderer source lives in Pepper's runtime tree (`~/.pepper/`), a separate repo. Day-1 visual compare during the cutover window is the validation; reconcile any drift in a follow-up. Color codes (`BRIEFING_COLOR_CRITICAL=15548997` / `BRIEFING_COLOR_WARNING=16776960`) and embed ordering are pinned in unit tests against the spec's documented values.
- **Real-guild smoke requires live credentials.** Deferred to the cutover window per the same pattern as #04 (live mixed-traffic day) and #06 (operator vault move).
- **Voice / stage scheduled events not exercised end-to-end in unit tests beyond the type-guard error path and the channel-pass-through happy path.** discord.py's API surface for these channel types is well-documented; the endpoint resolves the right `discord.abc.GuildChannel` and passes it through to `discord.Guild.create_scheduled_event(channel=...)`. Real voice/stage event creation is a Step-2 smoke item.
- **Event datetime handling is ISO-8601 only; no relative-time parsing ("tomorrow at 3pm").** Pepper has a separate skill for relative-time → ISO conversion before the call reaches the bus.
