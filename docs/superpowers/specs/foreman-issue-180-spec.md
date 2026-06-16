# Spec: gate Discord `on_reaction_add` through the access gate (issue #180)

## Goal

Stop the Discord endpoint from publishing `discord.reaction_add` Events for
reactions in channels the access gate would deny for inbound messages. The
fix mirrors the gate check already present in `on_message` so a reaction in
a non-allowlisted channel is silently dropped — matching the symmetry the
issue calls out and the live 2026-06-16 over-routing observation (Wren
received `data.channel_id = "1488680018077945978"` for a reaction in
`#pepper-chat`, a channel Wren is not subscribed to). See issue
[#180](https://github.com/jeffrichley/agent_core/issues/180).

## Acceptance criteria

- `_make_on_reaction_add_handler` in
  `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
  (currently lines 1138-1171; the issue body's line pointers `1051-1084`
  reflect a pre-refactor numbering — use the current line numbers when
  editing) gains a `gate_message` check inserted **after** the existing
  self-user / other-bot guard (current line 1141) and the ack-emoji guard
  (current line 1146), and **before** the `data: dict[str, Any] = {...}`
  envelope-build at current line 1151. When the gate denies, the handler
  returns early and **does not** call `self._handle.publish(...)` or
  `self._record_inbound(...)`.
- The `InboundContext` built inside `on_reaction_add` uses:
  - `is_dm = reaction.message.guild is None` (mirrors `on_message` line
    1014).
  - `author_id = str(user.id)` — the **reactor's** id, not the original
    message author's id. The reactor is who the gate's "should this bot
    react to this principal?" question is about; the original message
    author is irrelevant for that decision.
  - `channel_id = str(reaction.message.channel.id)`.
  - `is_bot = bool(getattr(user, "bot", False))` — pipe through verbatim
    for semantic clarity even though the `user.bot` short-circuit at
    line 1141 means this is always `False` at runtime today. Matches the
    `is_bot=bool(getattr(message.author, "bot", False))` pattern in
    `on_message` line 1019.
- When the gate denies, the handler logs at `log.debug` in the same shape
  as the existing on_message deny log at endpoint.py:1024:
  ```python
  log.debug(
      "discord(%s): gate denied reaction_add from %s in channel %s",
      self.name,
      user.id,
      reaction.message.channel.id,
  )
  ```
  Use `log.debug` (not `log.info`) to match the on_message tier — a busy
  guild with a strict allowlist would otherwise spam INFO on every
  reaction.
- A new test `test_on_reaction_add_dropped_when_gate_denies_channel` is
  added to
  `packages/agent-core-discord/tests/test_endpoint_inbound.py` (the file
  where every other `test_on_reaction_add_*` lives — the issue body
  suggests `test_endpoint_hardening.py` "or equivalent", and
  `test_endpoint_inbound.py` is the equivalent). Shape:
  ```python
  @pytest.mark.asyncio
  async def test_on_reaction_add_dropped_when_gate_denies_channel(
      monkeypatch, tmp_path
  ):
      """Channel-allowlist gate must apply to reactions, not just messages
      (issue #180). Live repro: Wren received a reaction Event for
      #pepper-chat on 2026-06-16 because the gate ran for on_message
      but not for on_reaction_add."""
      import json
      access = tmp_path / "access.json"
      access.write_text(
          json.dumps({"dmPolicy": "open", "channels": {"200": {}}}),
          encoding="utf-8",
      )
      ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
      fake.add_channel(FakeChannel(id="200"))
      fake.add_channel(FakeChannel(id="999"))
      msg = FakeMessage(id="m-out", channel_id="999")
      msg.author = fake.user
      msg.guild = type("G", (), {"id": "guild-1"})()
      msg.channel = fake.get_channel("999")
      user = FakeUser(id="100", name="alice", display_name="Alice")
      reaction = FakeReaction(emoji="👍", message=msg)
      try:
          await fake.fire("on_reaction_add", reaction, user)
          assert handle.published == []
      finally:
          await ep.stop()
  ```
  The access-config JSON shape matches the existing
  `test_on_message_respects_channel_allowlist` at test_endpoint_inbound.py
  line 242. The `FakeReaction` / `FakeMessage` / `FakeUser` / `FakeChannel`
  imports are already present in this file (lines 10, 280-283).
- A second new test
  `test_on_reaction_add_publishes_when_channel_in_allowlist` is added
  immediately below it (positive control): same access config, but the
  reaction fires in channel `200` and the test asserts
  `len(handle.published) == 1` and
  `env.payload.data["channel_id"] == "200"`. Without this companion test
  the gate-deny test could pass for the wrong reason (e.g., a typo that
  short-circuits all reactions).
- A third new test
  `test_on_reaction_add_dm_follows_dm_policy_deny` is added: access config
  is `{"dmPolicy": "deny"}`, the reaction fires on a DM message
  (`msg.guild = None`), assert `handle.published == []`. This pins the
  DM-policy side of the gate so a future refactor that only checks
  `channels` (not `dm_policy`) fails CI. The companion positive case for
  DM (`dm_policy: "open"`) is already covered by the existing
  `test_on_reaction_add_dm_context` (line 370) — that test passes today
  because the default access config is `dm_policy="open"` with empty
  `channels`, which `gate_message` allows.
- All five existing `test_on_reaction_add_*` tests in
  `test_endpoint_inbound.py` (lines 287, 315, 333, 351, 370) continue to
  pass unchanged. They use the default empty access config
  (`channels={}`, `dm_policy="open"`) which `gate_message` treats as
  "allow all guild channels" and "allow all DMs", so their assertions
  hold after the gate is wired in.
- `tests/test_endpoint_hardening.py::test_handlers_registered_via_add_listener`
  (line 156-180) continues to pass — listener wiring is not touched by
  this spec, only the handler body.
- The diff at `git log -p` for the fix commit shows the same
  `InboundContext(...) + gate_message(self._access, ctx) + return` pattern
  in `_make_on_reaction_add_handler` that already exists in
  `_make_on_message_handler` lines 1014-1030. A reviewer can eyeball
  the parity without reading the test.
- A towncrier news fragment is added at
  `packages/agent-core-discord/changelog.d/180.fixed.md`
  (`changelog.d/` does not yet exist for this package — create it; the
  type slug `fixed` is registered in
  `packages/agent-core-discord/towncrier.toml` line 30-33). Content:
  *"`discord.reaction_add` Events now respect the same channel allowlist
  and `dmPolicy` as inbound messages. Reactions in non-allowlisted
  channels are silently dropped, matching `on_message` semantics
  (#180)."*
- `just check` (lint + typecheck + tests) exits zero and
  `new_failures_count == 0` against the package's test suite.

## Approach

`gate_message` is **not** message-shaped — it only consumes
`InboundContext` fields (`is_dm`, `author_id`, `channel_id`, `is_bot`)
and never touches `message.content`, attachments, embeds, or any
message-only data. Direct reuse from `on_reaction_add` is correct;
extracting a helper would be unmotivated until a third caller appears.
(Issue #170's spec, see Open questions, does propose such a helper for
its broader scope — defer the helper extraction to that ticket.)

**Mirror `on_message` exactly.** The on_message handler at endpoint.py
lines 1014-1030 builds an `InboundContext` and calls `gate_message`
immediately after the self-filter, before any side effects (ack
reaction, attachment metadata, envelope publish). The on_reaction_add
handler follows the same shape: self/other-bot/ack guards first, then
the gate, then envelope build + publish. Placing the gate after the
ack-emoji guard (not before it) preserves a small optimization: the bot's
own 👀 acks are dropped without paying the gate's cost, and the test
`test_on_reaction_add_drops_ack_emoji` (line 351) still passes regardless
of access config.

**Pattern naming.** No GoF pattern fits this — it's a straightforward
guard-clause addition mirroring an existing guard-clause. The relevant
Google-style principle is **DRY at the policy layer**: the question
"may this Discord event reach the bus?" must answer the same way for
messages and reactions or the policy is incoherent. The fix is one
function call, not a structural change.

**Why pass `author_id=str(user.id)` (the reactor) and not the original
message author's id.** The gate's `dm_policy: "allowlist"` branch
consults `author_id` to decide "is this principal allowed to DM the
bot?". For a reaction, the principal whose action triggered the event
is the reactor — they're the one whose 👍 is being routed to the bot's
bus. Routing on the original message author's id would mean: "a third
party reacts to a message I (the bot's owner) sent in your DM, and that
reaction is allowed because *I'm* in the allowlist." That's wrong. The
reactor's id is the right key.

**Why `is_bot=bool(getattr(user, "bot", False))` even though
line 1141 already filtered bots.** Two reasons. First, semantic clarity:
the InboundContext carries the truth of what the gate is reasoning about,
and `is_bot=False` is the truth here. Hardcoding `is_bot=False` would be
a lie that the next refactor could trip on (precedent: on_message
hardcoded `is_bot=False` from 2026-04 until PR #158 caught the dead
gate-branch in 2026-06-07 — see endpoint.py:1009-1013 comment). Second,
forward compatibility: if a follow-up loosens the "drop all other-bot
reactions" line at 1141 (for example to honor `allowed_bot_ids` on
reactions, mirroring messages — see issue #170's Out of scope), the
gate will already receive the truthful `is_bot`. No second edit needed.

**Why log at `debug`, not `info`.** Matches the existing on_message deny
log at line 1024. Reactions are higher-volume than messages in active
guilds; a strict allowlist could otherwise spam INFO on every
non-allowlisted reaction. Operators who want visibility have the gate's
existing logging machinery available.

**Conventions.** Inline gate call sites land in
`_make_on_message_handler` (endpoint.py:1015-1030) — replicate that
shape verbatim. Tests using a temp JSON access config follow the pattern
in `test_on_message_respects_channel_allowlist` (test_endpoint_inbound.py
line 242). Test naming uses the `test_on_<event>_<scenario>` convention
already in use (line 56, 78, 287, etc.). News fragments under
`<package>/changelog.d/<issue>.<type>.md` follow the towncrier
convention in `packages/agent-core-discord/towncrier.toml`.

## Sub-requests (topologically sorted)

1. In
   `packages/agent-core-discord/src/agent_core_discord/endpoint.py`,
   inside `_make_on_reaction_add_handler` (current lines 1138-1171),
   insert the gate check between the existing ack-emoji guard (after
   current line 1147) and the existing `message = reaction.message`
   line (current line 1150). The exact insertion:

   ```python
           # 3. Run the access gate. Same gate as on_message — a
           # reaction in a non-allowlisted channel must be dropped
           # exactly like a message would be. Issue #180; live verified
           # 2026-06-16 (Wren received a reaction Event from
           # #pepper-chat, a channel outside its allowlist).
           message = reaction.message
           is_dm = message.guild is None
           ctx = InboundContext(
               is_dm=is_dm,
               author_id=str(user.id),
               channel_id=str(message.channel.id),
               is_bot=bool(getattr(user, "bot", False)),
           )
           if not gate_message(self._access, ctx):
               log.debug(
                   "discord(%s): gate denied reaction_add from %s in channel %s",
                   self.name,
                   user.id,
                   message.channel.id,
               )
               return
   ```

   The pre-existing `message = reaction.message` assignment at current
   line 1150 is **deleted** (it moves into the gate block above) and the
   downstream `data` dict at current lines 1151-1158 keeps referencing
   the same `message` name without change. Verify no other reads of
   `message` happen between the guards and the new gate block.
   Renumber the inline `# 3. Build the Event envelope.` comment at
   current line 1149 to `# 4. Build the Event envelope.` so the inline
   numbering stays consistent.

2. In the same file, confirm no import changes are needed: `gate_message`
   and `InboundContext` are already imported at endpoint.py line 38
   (`from agent_core_discord.access import AccessConfig, InboundContext,
   gate_message, load_access_config`).

3. In `packages/agent-core-discord/tests/test_endpoint_inbound.py`, add
   the three new tests immediately after `test_on_reaction_add_dm_context`
   (after current line 386, before line 389's "Engagement-event
   listeners" comment block). The three tests in order:

   - `test_on_reaction_add_dropped_when_gate_denies_channel` (the
     primary regression pin; shape in Acceptance criteria above).
   - `test_on_reaction_add_publishes_when_channel_in_allowlist` (the
     positive companion; same access config, channel `200` instead of
     `999`, assert one envelope published with
     `env.payload.data["channel_id"] == "200"`).
   - `test_on_reaction_add_dm_follows_dm_policy_deny` (DM-side
     coverage; access config `{"dmPolicy": "deny"}`, reaction on a DM
     message with `msg.guild = None`, assert
     `handle.published == []`).

   Each test follows the `_start_endpoint(monkeypatch, access_path=...)`
   wiring pattern from `test_on_message_respects_channel_allowlist`
   (line 242).

4. Create `packages/agent-core-discord/changelog.d/180.fixed.md`
   (and the `changelog.d/` directory; it does not yet exist for this
   package). One-line content per Acceptance criteria.

5. Run, from the repo root:
   ```bash
   uv run pytest \
     packages/agent-core-discord/tests/test_endpoint_inbound.py \
     packages/agent-core-discord/tests/test_endpoint_hardening.py \
     packages/agent-core-discord/tests/test_access.py -v
   ```
   Confirm: the three new tests pass; the eight existing reaction tests
   pass; the on_message gate tests still pass; the access-gate unit
   tests still pass.

6. Run `just check` from the repo root and confirm zero exit.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | In `_make_on_reaction_add_handler` (lines 1138-1171), insert the `InboundContext` + `gate_message` + early-return block + `log.debug` deny line after the ack-emoji guard and before the envelope-build. Move the `message = reaction.message` assignment up into the gate block. No other functions touched. No imports added (already present). |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | Add three new tests after `test_on_reaction_add_dm_context` (line 386): `test_on_reaction_add_dropped_when_gate_denies_channel` (primary regression pin), `test_on_reaction_add_publishes_when_channel_in_allowlist` (positive companion), `test_on_reaction_add_dm_follows_dm_policy_deny` (DM-policy coverage). |
| `packages/agent-core-discord/changelog.d/180.fixed.md` | New file (and new `changelog.d/` directory). One-line towncrier `fixed` entry. |

## Alternatives considered

- **Adopt issue #170's `_should_route_event` helper proactively.** The
  spec for issue #170 (already merged at
  `docs/superpowers/specs/foreman-issue-170-spec.md`) introduces a
  shared helper that wraps `InboundContext + gate_message` for
  reactions, message edits, and message deletes. Building #180 on
  that helper would shrink the eventual #170 diff but pre-commits to a
  design choice (helper vs. inline) that #170 hasn't merged yet, and
  the issue text for #180 explicitly asks for the inline pattern
  ("Run the same access gate inside `on_reaction_add` immediately
  after the bot-self and ack-emoji drops"). Defer to #170 to introduce
  the helper across all three handlers when its implementation lands.
- **Extract a `gate_inbound_channel` helper now (the issue's "or" branch).**
  The issue text hedges: *"If `gate_message` is genuinely message-shaped
  (e.g., reads message content), pull the channel-gate logic into a
  smaller helper."* `gate_message` is not message-shaped — it consumes
  only `InboundContext` fields. The hedge does not trigger. Direct
  reuse is correct and avoids a second public surface in `access.py`.
- **Hardcode `is_bot=False` in the InboundContext.** Technically
  correct today (the `user.bot` short-circuit at line 1141 means we
  only reach the gate with `is_bot=False`), but lies to a future
  reader and makes the gate's `allowed_bot_ids` branch structurally
  unreachable from reactions. The on_message handler made exactly this
  mistake until PR #158 caught it (see endpoint.py:1009-1013 comment).
  Pipe the real value through.
- **Use `author_id=str(message.author.id)` (the original message author)
  instead of `str(user.id)` (the reactor).** Would route the reaction
  Event through the message author's gate disposition rather than the
  reactor's. Wrong for `dm_policy: "allowlist"`: a third-party reactor
  whose id is not in `allowFrom` would slip through if the original
  message author's id happened to be in `allowFrom`. The reactor is
  the principal taking the action; the reactor's id is the right key.
- **Drop reactions to bot-sent DMs entirely (paranoid).** Breaks the
  symmetry argument: a user in the bot's DM can react to the bot's
  message and expect the bot to notice, exactly as for the original
  TextMessage. Honor `dm_policy` like on_message does.
- **Log gate denials at `info`, not `debug`.** A guild bot with a
  strict allowlist could see hundreds of dropped reactions per day from
  channels it doesn't subscribe to. INFO-spamming the operator
  log on every one is worse than the silent-drop story. Match the
  existing on_message debug tier.
- **Do nothing and accept the leak.** Rejected: the 2026-06-16 Wren
  observation is a confirmed live cross-namespace bleed that the
  Pepper/Wren split was supposed to prevent. The issue is concrete,
  reproducible, and one-line-ish to fix.

## Open questions

- **Coordination with issue #171's spec (not yet implemented).** Issue
  #171 (spec merged 2026-06-13 as commit `8a88a66`, implementation not
  yet merged) replaces the cached `on_reaction_add` listener with a
  raw `on_raw_reaction_add` listener — `reaction.message.channel.id`
  becomes `raw.channel_id`, `user.id` becomes `raw.user_id`, etc. If
  #171's implementation lands **before** this fix, the Worker will
  need to translate the gate insertion into the raw-handler shape:
  the gate logic is identical, only the field accesses change
  (`raw.guild_id` / `raw.channel_id` / `raw.user_id`, and the bot
  filter at #171's lines 297-309 + the cache lookup at 317-320 replace
  the current line-1141 filter). The Worker should check git log on
  `packages/agent-core-discord/src/agent_core_discord/endpoint.py` at
  implementation time; if `_make_on_raw_reaction_add_handler` exists,
  apply the gate there instead. The acceptance test
  `test_on_reaction_add_dropped_when_gate_denies_channel` would in
  that case fire `await fake.fire("on_raw_reaction_add",
  FakeRawReaction(...))` instead.
- **Relationship to issue #170's spec (also not yet implemented).**
  Issue #170 covers reactions **and** lifecycle events (edits, deletes)
  through one shared `_should_route_event` helper. If #170's
  implementation lands first, this fix is **already done** (the
  reaction-handler gate is part of #170's scope) and the Worker for
  #180 should close the issue with no diff, citing #170. If #180 lands
  first, #170's eventual implementation will refactor this inline call
  into its shared helper — a clean follow-up, no scope creep here.
  Recommend the Reviewer flag this overlap on the impl PR so neither
  worker silently doubles the work.

## Out of scope

- Lifecycle events (`on_raw_message_edit`, `on_raw_message_delete`).
  Issue #170 owns those; this spec does not touch
  `_make_on_raw_message_lifecycle_handler` (endpoint.py around
  line 1260).
- Poll-vote events (`on_raw_poll_vote_add`, `on_raw_poll_vote_remove`).
  Issue #170 explicitly defers these ("file separately if it
  surfaces"); same posture here.
- The `_should_route_event` shared helper. Defer to #170, where three
  callers motivate its existence.
- Migrating `on_reaction_add` to `on_raw_reaction_add`. Issue #171's
  spec already owns that; this fix stays on the current cached
  dispatcher and ships independently. See Open questions for the
  coordination plan.
- Loosening the line-1141 "drop all other-bot reactions" filter to
  honor `allowed_bot_ids` (PR #158's pattern for messages). Issue
  #143's spec explicitly defers reaction-side bot allowlisting; do not
  expand here.
- The Wren TaskList #222 "Discord-wren reactions don't route to bus AT
  ALL for subscribed channels" tracking item — that's the **inverse**
  symptom (under-routing for allowlisted channels), and the issue
  body for #180 explicitly excludes it.
- Any redesign of `AccessConfig` / `gate_message` / `InboundContext`.
  The existing gate semantics are correct for this fix; the bug is
  that the gate was never called, not that the gate is wrong.
- Outbound metadata cleanup (#221) — unrelated.
