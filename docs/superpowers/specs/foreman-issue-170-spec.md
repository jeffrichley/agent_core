# Spec: gate Discord reaction/edit/delete events through channel allowlist (issue #170)

## Goal

Bring the `DiscordEndpoint` reaction / message-edit / message-delete handlers
under the same channel-allowlist + `dm_policy` gate that already filters inbound
`TextMessage`s. Today those handlers publish `Event` envelopes unconditionally,
so a bot sitting in a guild receives lifecycle events from channels it has no
allowlist entry for — see issue
[#170](https://github.com/jeffrichley/agent_core/issues/170) and the live
observation of a `#pepper-chat` `discord.message_edit` reaching Wren's inbox on
2026-06-10.

## Acceptance criteria

- `_make_on_reaction_add_handler` in
  `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (~line 1138)
  drops the envelope when `gate_message` would deny it. The existing self-user,
  other-bot, and ack-emoji short-circuits stay; the new gate check runs AFTER
  those guards and BEFORE envelope construction.
- `_make_on_raw_message_lifecycle_handler` in the same file (~line 1260, wired
  for both `on_raw_message_edit` and `on_raw_message_delete` at lines 514-521)
  drops the envelope when `gate_message` would deny it. The check runs at the
  top of the inner `on_raw_message_lifecycle` coroutine, before envelope
  construction.
- A single private helper `DiscordEndpoint._should_route_event(*, channel_id,
  guild_id, author_id="", is_bot=False) -> bool` centralises the
  `InboundContext` construction + `gate_message` call so the three handlers
  share one seam.
- Gate-denied events do NOT call `self._handle.publish(...)` and do NOT call
  `self._record_inbound(...)`. Gate-denied events log a `log.debug(...)` line
  in the same shape as the existing on_message deny log at endpoint.py:1024
  ("gate denied <event_type> from <channel_id>").
- DM lifecycle events route through the configured `dm_policy` the same way
  `gate_message` already does for TextMessages: `open` → pass, `deny` → drop,
  `allowlist` → drop (because reaction/edit/delete handlers pass an empty
  sentinel `author_id` for the raw lifecycle events — documented in the helper
  docstring as intentional, since the original author is not on the raw event
  and "DM allowlist" semantics for a lifecycle event with no author can't be
  satisfied; same outcome as a non-allowlisted human author). The reaction
  handler DOES have a `user`, so it passes `author_id=str(user.id)` and the DM
  allowlist works exactly as it does for TextMessages.
- New red tests in `packages/agent-core-discord/tests/test_endpoint_inbound.py`:
  1. `test_on_reaction_add_dropped_when_channel_not_in_allowlist` —
     access.json with `channels: {"200": {}}`, reaction in channel `999`,
     assert `handle.published == []`.
  2. `test_on_raw_message_edit_dropped_when_channel_not_in_allowlist` — same
     access.json, raw edit event with `channel_id=999`, assert no publish.
  3. `test_on_raw_message_delete_dropped_when_channel_not_in_allowlist` —
     same access.json, raw delete event with `channel_id=999`, assert no
     publish.
- New green tests in the same file:
  4. `test_on_reaction_add_publishes_when_channel_in_allowlist` — access.json
     with `channels: {"200": {}}`, reaction in channel `200`, assert one
     `discord.reaction_add` envelope published with the existing
     `user_display_name` payload shape preserved.
  5. `test_on_raw_message_edit_publishes_when_channel_in_allowlist` —
     access.json with `channels: {"200": {}}`, raw edit event with
     `channel_id=200`, assert one `discord.message_edit` envelope published.
  6. `test_on_raw_message_delete_publishes_when_channel_in_allowlist` —
     access.json with `channels: {"200": {}}`, raw delete event with
     `channel_id=200`, assert one `discord.message_delete` envelope published.
- New DM-policy coverage in the same file:
  7. `test_on_reaction_add_dm_follows_dm_policy_deny` — access.json with
     `dmPolicy: "deny"`, reaction on a DM message (`guild=None`), assert no
     publish. (Open dm_policy is already covered by the existing
     `test_on_reaction_add_dm_context`.)
  8. `test_on_raw_message_edit_dm_follows_dm_policy_deny` — access.json
     with `dmPolicy: "deny"`, raw edit event with `guild_id=None`, assert no
     publish.
  9. `test_on_raw_message_edit_dm_dropped_when_dm_policy_allowlist` —
     access.json with `dmPolicy: "allowlist"`, `allowFrom: ["100"]`, raw edit
     event with `guild_id=None`; assert no publish (documents the
     sentinel-author behaviour: lifecycle edit/delete with no author cannot
     satisfy a DM allowlist).
- The existing reaction/edit/delete tests in `test_endpoint_inbound.py`
  (lines 287, 603, 620, 370) continue to pass unchanged. They use the default
  empty access config (`channels = {}`, `dm_policy = "open"`) which the gate
  treats as "allow all guild channels", so the existing assertions hold.
- The existing tests in `test_endpoint_hardening.py` continue to pass; no
  changes expected there.
- `just check` (lint + typecheck + tests) exits zero and `new_failures_count
  == 0` against the package's test suite.

## Approach

Reuse `gate_message` rather than build a parallel gate — that's the whole
point: "should this bot see this message?" and "should this bot see lifecycle
events about this message?" must answer with one function so they can never
drift. The issue's "minimal" shape #1 (small helper, three call sites) is the
right fit here; the "slightly cleaner" split into `gate_dm` / `gate_guild_channel`
is an orthogonal refactor that would touch every existing on_message test, and
the value-add over a 6-line helper is small. Defer the split.

**Where the helper lives.** Add `DiscordEndpoint._should_route_event` as a
private instance method on the endpoint, next to the existing handler
factories. It wraps `InboundContext(...)` + `gate_message(self._access, ctx)`
so the three handlers don't each have to repeat the same six lines. Keeping
the helper on the endpoint (rather than as a module-level function in
`access.py`) matches where `self._access` lives and avoids dragging
endpoint-specific concerns into `access.py`'s narrow public surface.

**Why a sentinel author for edit/delete.** `discord.RawMessageUpdateEvent`
and `discord.RawMessageDeleteEvent` carry only IDs (message_id, channel_id,
guild_id); they do NOT carry the original message author. For guild channels
this is fine — `gate_message` only consults `author_id` in the `is_dm` +
`dm_policy == "allowlist"` branch, and the channel allowlist branch ignores
it entirely. For DMs the sentinel `author_id=""` will fail an "allowlist"
`dm_policy`, which is the conservative outcome we want: a DM edit/delete with
no resolvable author cannot prove it satisfies the allowlist, so it gets
dropped. `open` still passes and `deny` still drops — symmetry with the
TextMessage path is preserved for the two policies where author identity
isn't necessary. Reactions DO have a `user` so they pass the real
`author_id`, and DM reactions with `dmPolicy: "allowlist"` work correctly.

**Why `is_bot=False` for raw lifecycle events.** Same reasoning: the raw
event has no author, so we can't truthfully set `is_bot`. Passing `False`
means the gate's "default-deny bots unless allowlisted" guard is skipped,
and we fall straight through to the channel/DM check — which is the only
information we actually have. Reactions, again, have a real `user.bot` and
the existing `if user.bot: return` short-circuit at line 1141 already drops
other-bot reactions before the gate even runs, so what we pass for `is_bot`
in the reaction path is moot — pipe the real value through for consistency
with `_make_on_message_handler` line 1019.

**Why no separate handler-level "bot reaction" filter change.** The existing
`if user == self._client.user or user.bot: return` at endpoint.py:1141
stays. It's strictly stronger than the gate (drops all bot reactions, not
just non-allowlisted ones), and broadening reaction routing to honour
`allowedBotIds` is explicitly listed as Out of scope for issue #143's v1
(see foreman-issue-143-spec.md Out of scope section). Holding that line.

**Why log denials at debug, not info.** The existing on_message deny log at
line 1024 is `log.debug` and the on_message path is the high-traffic one;
lifecycle events are lower-volume but the symmetry argument wins, and a
busy guild with strict allowlist could otherwise spam INFO logs on every
edit/reaction. Match the existing tier.

## Sub-requests (topologically sorted)

1. Add `_should_route_event` helper to `DiscordEndpoint` in
   `packages/agent-core-discord/src/agent_core_discord/endpoint.py`. Signature:
   `def _should_route_event(self, *, channel_id: str, guild_id: str,
   author_id: str = "", is_bot: bool = False) -> bool`. Body builds
   `InboundContext(is_dm=not guild_id, author_id=author_id,
   channel_id=channel_id, is_bot=is_bot)` and returns
   `gate_message(self._access, ctx)`. Docstring documents the sentinel
   `author_id=""` and `is_bot=False` defaults and explains they're for the
   raw lifecycle path where the original author isn't available.
2. In `_make_on_reaction_add_handler` (endpoint.py around lines 1138-1171):
   after the existing self-user/other-bot guard (line 1141) and the
   ack-emoji guard (line 1146), AND BEFORE the `message = reaction.message`
   line, add:

   ```python
   message = reaction.message
   if not self._should_route_event(
       channel_id=str(message.channel.id),
       guild_id=str(message.guild.id) if message.guild else "",
       author_id=str(user.id),
       is_bot=bool(getattr(user, "bot", False)),
   ):
       log.debug(
           "discord(%s): gate denied reaction_add in channel %s",
           self.name,
           message.channel.id,
       )
       return
   ```

   The existing `message = reaction.message` line on the happy path moves up
   above the gate; the data-dict construction and envelope publish stay
   exactly as today. Confirm no other reads of `message` happen between the
   guards and the gate.
3. In `_make_on_raw_message_lifecycle_handler` (endpoint.py around lines
   1260-1289): at the top of the inner `on_raw_message_lifecycle`
   coroutine, before the `data = {...}` dict literal, add:

   ```python
   guild_id_str = str(raw.guild_id) if raw.guild_id else ""
   if not self._should_route_event(
       channel_id=str(raw.channel_id),
       guild_id=guild_id_str,
   ):
       log.debug(
           "discord(%s): gate denied %s in channel %s",
           self.name,
           event_type,
           raw.channel_id,
       )
       return
   ```

   Then reuse `guild_id_str` in the existing `data` dict so the string is
   computed once.
4. Add the nine new tests enumerated in Acceptance criteria to
   `packages/agent-core-discord/tests/test_endpoint_inbound.py`. Place them
   alongside the existing reaction and lifecycle tests (after the
   `test_on_reaction_add_dm_context` block at line 370 and after the
   `test_on_raw_message_delete_publishes_event_envelope` block at line 620
   respectively). Use the existing `_start_endpoint(monkeypatch,
   access_path=str(access))` pattern from
   `test_on_message_respects_channel_allowlist` (line 242) for access-config
   wiring, and the existing `FakeReaction` / `FakeRawMessageUpdate` /
   `FakeRawMessageDelete` classes for event construction.
5. Run `uv run pytest packages/agent-core-discord/tests/test_endpoint_inbound.py
   packages/agent-core-discord/tests/test_endpoint_hardening.py
   packages/agent-core-discord/tests/test_access.py -v` and confirm zero
   failures and zero new failures vs main.
6. Run `just check` and confirm it exits zero.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Add `_should_route_event` helper. Insert gate check + debug-log in `_make_on_reaction_add_handler` after the self/bot/ack guards. Insert gate check + debug-log at the top of `_make_on_raw_message_lifecycle_handler`'s inner coroutine. |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | Add nine new tests: three red (non-allowlisted guild channel), three green (allowlisted guild channel), three DM-policy coverage. Existing reaction/edit/delete tests unchanged. |

No changes expected in `access.py` (the gate is reused as-is), in
`test_access.py` (no public-API change to `gate_message`), in
`test_endpoint_hardening.py` (listener wiring assertions still hold), or in
non-test docs.

## Alternatives considered

- **Split `gate_message` into `gate_dm` + `gate_guild_channel` and call the
  appropriate half from each handler.** Cleaner factoring long-term but a
  larger refactor — `gate_message` is the public surface that `on_message`,
  every gate test in `test_access.py`, and the access.py docstring all
  reference. Re-evaluate when a third reason to want the split shows up.
- **Inline the four-line `InboundContext` + `gate_message` call at each of
  the three handlers without a helper.** Less code, but three copies of
  the same sentinel-default reasoning to keep in sync; the helper's
  docstring is a useful one-place explanation of why edit/delete pass an
  empty `author_id`. Marginal call.
- **Build a parallel `gate_event` function in `access.py` instead of
  reusing `gate_message`.** Defeats the entire premise of the issue — the
  whole point is that the channel-allowlist answer must be identical for
  TextMessage and lifecycle events. Two functions invite drift.
- **Drop lifecycle events for DMs always (option #3 in the issue's "design
  call needed").** Paranoid and breaks the symmetry argument: a user in a
  bot's DM should be able to edit / react to their own messages and have
  the bot notice, exactly as for the original TextMessage routing.
- **Always-allow lifecycle events in DMs regardless of `dm_policy` (option
  #2 in the issue's "design call needed").** Tempting because DMs are 1:1
  with the bot's principal, but it makes `dm_policy: "deny"` mean
  "TextMessages denied, but edits/reactions on those (denied) messages
  still routed" — a counterintuitive carve-out the operator did not opt
  into.
- **Do nothing and accept the leak.** Rejected: 2026-06-10 Wren observation
  confirms it's actively misrouting in production, and the privacy upside
  (no message content in envelopes) does not paper over the routing-
  discipline inconsistency the issue calls out.

## Open questions

- None blocking. The "design call needed" the issue surfaced (DM-context
  reaction/edit policy) is resolved here by mirroring `gate_message`'s
  existing DM semantics, which is the issue's own preferred option #1.

## Out of scope

- `on_raw_poll_vote_add` / `on_raw_poll_vote_remove` at endpoint.py line
  ~1209 — the issue explicitly says "file separately if it surfaces". Do
  not add a gate to those handlers in this PR.
- `discord.reaction_remove` — the issue says "verify before scoping". A
  quick `Grep` for `on_reaction_remove` / `on_raw_reaction_remove` in
  `endpoint.py` returns zero hits, so it isn't currently wired and there's
  nothing to gate. If a future PR wires it, this spec's helper is the
  natural reuse point.
- Broadening the `allowedBotIds` opt-in to reactions
  (`_make_on_reaction_add_handler`). Out of scope per issue #143's spec —
  reactions stay messages-only-for-now. The existing "drop all other-bot
  reactions" line at endpoint.py:1141 stays.
- Splitting `gate_message` into `gate_dm` / `gate_guild_channel` (the
  issue's alternative shape #2). Orthogonal refactor; file separately if
  the helper grows enough callers to motivate it.
- The Wren TaskList #222 "Discord-wren reactions don't route to bus"
  question — that's the inverse symptom (under-routing) and the issue
  notes confirmation will happen during impl. If it turns out to be the
  same root cause, the Worker can mention it in the impl PR body; do NOT
  expand spec scope.
- Cross-bot allowlist tuning (foreman#231 territory per the issue's
  Out-of-scope list).
- Surfacing the original message author on raw edit/delete events (e.g.,
  by fetching from `raw.cached_message` if present). Would let DM
  `dmPolicy: "allowlist"` work for lifecycle events but adds a partial-
  resolution code path with its own edge cases; not in v1.
