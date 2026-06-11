# Spec: Switch Discord reaction listener to `on_raw_reaction_add` so DM reactions reach the bus (issue #171)

## Goal

Fix the silent drop: when a user reacts to a bot-sent DM, the Discord
endpoint must publish a `discord.reaction_add` Event to the bus, exactly
as it does for guild reactions today. The structural fix is to wire the
listener against discord.py's *raw* dispatch point (`on_raw_reaction_add`)
instead of the cached `on_reaction_add`, matching the established pattern
already used for poll-vote and message-lifecycle events in the same file.
See issue
[#171](https://github.com/jeffrichley/agent_core/issues/171).

## Acceptance criteria

- `DiscordEndpoint` registers a listener for `"on_raw_reaction_add"` and
  no longer registers one for `"on_reaction_add"`. Verified by an updated
  `tests/test_endpoint_hardening.py::test_endpoint_registers_all_listeners`
  assertion (the test at line 168-178 — change the
  `assert "on_reaction_add" in fake._handlers` line to
  `assert "on_raw_reaction_add" in fake._handlers` and document the rename
  in the existing "Engagement listeners — wired against raw dispatch
  points so they fire even after the underlying message has been evicted
  from the client's message cache" comment).
- A new private factory
  `def _make_on_raw_reaction_add_handler(self):` in
  `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
  (placed where `_make_on_reaction_add_handler` lives today, lines
  1138-1171) returns an `async def on_raw_reaction_add(raw: Any) -> None`
  coroutine that publishes a `kind="Event"` envelope with
  `payload.type == "discord.reaction_add"` and the same payload shape as
  the current handler:
  `{"emoji": str, "channel_id": str, "message_id": str, "guild_id": str,
  "user_id": str, "user_display_name": str}`. `guild_id` is `""` when
  `raw.guild_id` is None (DM case), mirroring the pattern in
  `_make_on_raw_poll_vote_handler` at line 1243 and
  `_make_on_raw_message_lifecycle_handler` at line 1275.
- The new handler drops the bot's own reactions by ID, using the exact
  pattern from `_make_on_raw_poll_vote_handler` (lines 1221-1225):
  ```python
  self_user = self._client.user if self._client else None
  self_id = getattr(self_user, "id", None) if self_user is not None else None
  if self_id is not None and str(getattr(raw, "user_id", "")) == str(self_id):
      return
  ```
- The new handler drops the ack emoji, mirroring the current handler's
  step 2 (line 1144-1147) but reading the emoji off `raw.emoji`:
  ```python
  ack_emoji = self._access.ack_reaction
  if ack_emoji and str(raw.emoji) == ack_emoji:
      return
  ```
  `str(raw.emoji)` works for both unicode and custom emoji because
  `discord.PartialEmoji.__str__` returns the canonical form
  (unicode char for unicode, `"<:name:id>"` for custom).
- The new handler drops *other* bot users opportunistically via the
  client's local user cache (no HTTP). This preserves today's
  `user.bot` filter (line 1141) without adding latency: lookup the user
  via `client.get_user(raw.user_id)` (synchronous, cache-only) and if
  the user is cached AND `getattr(user, "bot", False)`, return without
  publishing. If the user is NOT in the cache (cache miss), publish
  anyway — same posture as `_make_on_raw_poll_vote_handler`, and the
  access gate's `allowed_bot_ids` field (PR #158 / agent_core#143) is
  the structural place for bot filtering. Document this stance in a
  comment on the cache lookup line so a future reader doesn't
  re-introduce an HTTP fetch_user round-trip for every reaction.
- The new handler resolves the reactor's display name via
  `self._resolve_user_display_name(int(raw.user_id))`, exactly as
  `_make_on_raw_poll_vote_handler` does at line 1234.
- The wiring line at endpoint.py:499
  `self._add_listener(self._make_on_reaction_add_handler(), "on_reaction_add")`
  is replaced with
  `self._add_listener(self._make_on_raw_reaction_add_handler(), "on_raw_reaction_add")`.
  The old `_make_on_reaction_add_handler` method is deleted (no dead code
  retention — it's unreachable and would silently rot).
- A new `FakeRawReaction` class is added to
  `packages/agent-core-discord/tests/test_endpoint_inbound.py`,
  positioned alongside the existing `FakeRawPollVote`/`FakeRawMessageDelete`/
  `FakeRawMessageUpdate` (around line 396-442). Shape mirrors
  `discord.RawReactionActionEvent` (`raw_models.py:303`-ish):
  ```python
  class FakeRawReaction:
      """Mirrors ``discord.RawReactionActionEvent`` shape.

      Attributes match real discord.py: only IDs + emoji, no resolved
      Message or User. ``guild_id`` is ``Optional[int]`` (None for DMs).
      ``emoji`` is the canonical-string form used by discord.py's
      ``str(PartialEmoji)``.
      """

      def __init__(
          self,
          *,
          message_id: int,
          channel_id: int,
          user_id: int,
          guild_id: int | None,
          emoji: str,
      ) -> None:
          self.message_id = message_id
          self.channel_id = channel_id
          self.user_id = user_id
          self.guild_id = guild_id
          self.emoji = emoji
  ```
- The five existing `test_on_reaction_add_*` tests (lines 287-386:
  `test_on_reaction_add_publishes_event_envelope`,
  `test_on_reaction_add_drops_self_reactions`,
  `test_on_reaction_add_drops_other_bots`,
  `test_on_reaction_add_drops_ack_emoji`,
  `test_on_reaction_add_dm_context`)
  are rewritten to drive the raw handler. Each test:
  - Renames to `test_on_raw_reaction_add_*` (preserves the test
    descriptor; the fixture and assertions remain).
  - Uses `await fake.fire("on_raw_reaction_add", FakeRawReaction(...))`
    instead of `fake.fire("on_reaction_add", FakeReaction, FakeUser)`.
  - For the *drops_other_bots* variant: seed the user into
    `fake._users` via `fake.add_user(FakeUser(id="999", bot=True))` so
    `client.get_user("999")` returns a bot user and the handler drops it.
  - For the *publishes_event_envelope* variant: seed the user via
    `fake.add_user(FakeUser(id="100", display_name="Alice"))` so
    `user_display_name == "Alice"` (covers the
    `_resolve_user_display_name` cache-hit path).
  - For the *dm_context* variant: pass `guild_id=None` and assert
    `env.payload.data["guild_id"] == ""`. This is the canonical
    regression for the live-verified bug.
  - The pre-existing `FakeReaction` class (lines 280-283) is deleted
    once nothing references it.
- Two new tests, both in `test_endpoint_inbound.py`, mirror the
  poll-vote-handler coverage so the reaction handler has parity:
  - `test_on_raw_reaction_add_falls_back_to_fetch_user_on_cache_miss`:
    parallels `test_on_raw_poll_vote_add_falls_back_to_fetch_user_on_cache_miss`
    (line 478). Seed user via `fake.add_remote_user` (HTTP-only), fire
    the raw event, assert `user_display_name == "Alice"` and
    `fake.fetch_user_call_count == 1`.
  - `test_on_raw_reaction_add_dm_bot_message_live_shape`:
    pins the exact live-verified repro (2026-06-10 11:45 ET, bot
    `discord-wren`, DM channel `1508863457762480289`).
    `raw = FakeRawReaction(message_id=..., channel_id=1508863457762480289,
    user_id=<jeff's id>, guild_id=None, emoji="🔥")`. Assert
    `len(handle.published) == 1`, `env.payload.type ==
    "discord.reaction_add"`, `env.payload.data["guild_id"] == ""`,
    `env.payload.data["channel_id"] == "1508863457762480289"`, and
    `env.payload.data["emoji"] == "🔥"`. The test is independent of the
    DM/guild filter discussion — it pins the bug's actual fingerprint
    so a future regression that re-introduces the cached-listener
    pattern fails CI.
- `tests/test_endpoint_hardening.py::test_endpoint_registers_all_listeners`
  (line 168-178) is updated: change the
  `assert "on_reaction_add" in fake._handlers` line at line 170 to
  `assert "on_raw_reaction_add" in fake._handlers`, and move it under
  the existing "Engagement listeners" comment block (lines 172-174) so
  the colocated documentation reflects that reactions are now also a
  raw-dispatched engagement event.
- One towncrier news fragment:
  `packages/agent-core-discord/changelog.d/171.fixed.md` (new file;
  `changelog.d/` directory does not yet exist, create it). One-line
  `fixed` entry: *"DM reactions to bot-sent messages now reach the bus.
  The reaction listener is wired against ``on_raw_reaction_add`` so it
  fires regardless of message-cache state — matching the existing
  pattern for poll votes and message edits/deletes (#171)."*
- `just check` (lint + typecheck + tests) exits zero. No
  `new_failures_count`.
- The Worker's PR body documents the investigation outcome (per the
  issue's "Investigation written in PR body" acceptance criterion):
  the issue offered two candidate root causes (missing intent, handler
  filter); both were falsified by inspection (the intents are correct,
  the handler has no DM-discriminating filter and the existing
  `test_on_reaction_add_dm_context` passes today); the actual root
  cause is the cache-dispatch behavior that the poll-vote handler
  fixed in the same way for the same reasons. No Discord developer
  portal toggle is required (and thus no runbook update — the issue's
  conditional acceptance criterion for that doc update does not
  trigger).

## Approach

The issue posits two candidate root causes and asks for disambiguation
first. Both are falsified by reading the current code:

**Candidate #1 (missing intent) is falsified.** `endpoint.py:488-491` sets
`intents = discord.Intents.default()`, then `intents.message_content =
True` and `intents.reactions = True`. `Intents.default()` enables every
non-privileged intent — including both `guild_reactions` (bit 10) and
`dm_reactions` (bit 13). The `intents.reactions = True` line is the
combined alias for those same two bits; it's redundant but not wrong.
`intents.dm_reactions` is already implicitly enabled. The Discord
developer-portal toggles for DIRECT_MESSAGE_REACTIONS and
GUILD_MESSAGE_REACTIONS are *not* privileged (only PRESENCE_INTENT,
GUILD_MEMBERS_INTENT, and MESSAGE_CONTENT_INTENT are privileged); they're
on by default for any bot that connects with those gateway intent bits
set, which we do. There is no portal toggle to fix.

**Candidate #2 (handler filters out DMs) is falsified.** The handler at
`endpoint.py:1138-1171` has zero DM-vs-guild discriminating logic. The
only filters are (1) drop self reactions, (2) drop other-bot reactions,
(3) drop the ack emoji. The `message.guild` access at line 1155 is
explicitly None-safe: `str(message.guild.id) if message.guild else ""`.
The existing test
`tests/test_endpoint_inbound.py::test_on_reaction_add_dm_context` (line
370) constructs a synthetic `FakeReaction` with `msg.guild = None` and
asserts `guild_id == ""`. That test passes today. The handler does the
right thing when invoked — the issue is that for DM reactions on
bot-sent messages, discord.py never invokes it.

**The actual root cause is the dispatch layer, not the handler.**
discord.py's `on_reaction_add` fires only when the reacted-to message
exists in the client's internal message cache
(`Client._connection._messages`). The cache is populated by inbound
MESSAGE_CREATE gateway events; messages the bot sends via
`channel.send()` are NOT auto-added to the cache from the HTTP response
path. For guild channels the bot typically observes a self-echo
MESSAGE_CREATE that populates the cache, so `on_reaction_add` fires for
reactions on its own messages. For DMs that echo does not arrive
reliably, the message is uncached, and discord.py's reaction parser
falls back to firing `on_raw_reaction_add` *only*. The cached
`on_reaction_add` listener silently never fires — exactly the symptom
the issue describes.

**This is the same bug class that `_make_on_raw_poll_vote_handler` and
`_make_on_raw_message_lifecycle_handler` already fix.** Both were
written in direct response to the same failure mode — the comment at
`endpoint.py:500-505` describes it: *"wire the raw dispatch points so we
always fire, even when the underlying message has been evicted from the
client's message cache (the common case for long-running agents).
Caught on testbot 2026-05-05 Phase 6 verification: a vote on a
bot-posted poll never reached the agent because no listener was wired
here."* Reactions are the outlier — they were left on
`on_reaction_add` for legacy reasons and never got the same treatment.
This spec brings reactions to parity with polls and lifecycle events.

**The fix is mechanical: replace `_make_on_reaction_add_handler` with
`_make_on_raw_reaction_add_handler` and rewire the listener.** The new
handler is a near-copy of the existing poll-vote handler structure,
with the payload shape preserved exactly so no downstream consumer
breaks. The only behavioral change is that `user.bot` filtering is now
best-effort (cache lookup, not the always-resolved User object the
cached event provides) — the access gate's `allowed_bot_ids` is the
right structural layer for that filter anyway (per PR #158), and the
poll-vote handler set the precedent. A regression that wants strict
"never publish other-bot reactions" should land that check at the gate
layer in a follow-up, not here.

**Why not keep both listeners.** discord.py will fire
`on_raw_reaction_add` for *every* reaction (cached or not) and
additionally fire `on_reaction_add` for cached messages. Keeping both
listeners would publish duplicate envelopes for guild reactions
(double counts). Replacing is correct.

**Why not document a Discord developer-portal toggle.** The issue's
acceptance criterion gates the runbook update on "if missing intent."
The intent isn't missing; that acceptance criterion does not trigger.
We do not introduce a documentation file or runbook for a non-fix.

**Why not touch `_make_on_raw_message_lifecycle_handler`.** The issue
notes *"Likely same DM-vs-guild routing question applies here — file a
follow-up if confirmed during impl."* The lifecycle handler is already
wired against the raw event (`on_raw_message_edit` /
`on_raw_message_delete`, endpoint.py:514-521) and is not subject to the
cache-dispatch problem. The follow-up would only be needed if a separate
bug surfaces; this spec does not pre-emptively touch it. Out of scope.

**Conventions.** Private handler factories live as instance methods
named `_make_on_<event>_handler` (matches `_make_on_message_handler`,
`_make_on_raw_poll_vote_handler`, `_make_on_raw_message_lifecycle_handler`).
Test fakes for raw events live as plain classes in
`test_endpoint_inbound.py` immediately above the tests that use them
(matches `FakeRawPollVote`, `FakeRawMessageDelete`, `FakeRawMessageUpdate`).
News fragments under `<package>/changelog.d/<issue>.<type>.md` follow the
towncrier convention declared in
`packages/agent-core-discord/towncrier.toml` — the `fixed` type is
registered at line 30-33.

## Sub-requests (topologically sorted)

1. In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`,
   delete the `_make_on_reaction_add_handler` method (lines 1138-1171
   inclusive). It will be replaced by the raw variant in the next step.
2. In the same file, immediately after the deleted block, add the new
   factory:

   ```python
   def _make_on_raw_reaction_add_handler(self):
       """Build a handler for ``on_raw_reaction_add``.

       We deliberately wire the *raw* dispatch point (not the cached
       ``on_reaction_add``) so reactions fire regardless of whether
       the underlying message is in discord.py's internal message
       cache. The cached variant misses reactions on bot-sent DMs
       because the bot's outbound DM messages are not auto-cached
       (issue #171, verified live 2026-06-10). Same pattern as
       ``_make_on_raw_poll_vote_handler`` and
       ``_make_on_raw_message_lifecycle_handler`` above.
       """

       async def on_raw_reaction_add(raw: Any) -> None:
           # 1. Drop the bot's own reactions by ID. Raw events carry
           # only user_id (no User object), so we compare against
           # ``client.user.id`` instead of the ``user == self._client.user``
           # equality check the cached handler used. Same pattern as
           # _make_on_raw_poll_vote_handler.
           self_user = self._client.user if self._client else None
           self_id = (
               getattr(self_user, "id", None)
               if self_user is not None
               else None
           )
           if self_id is not None and str(getattr(raw, "user_id", "")) == str(self_id):
               return

           # 2. Drop other bots opportunistically via the client's user
           # cache. No HTTP fetch_user — that would add a round-trip
           # per reaction. If the reactor isn't in the local cache,
           # we publish; the access gate's ``allowed_bot_ids`` is the
           # structural place for bot filtering (agent_core#143, PR
           # #158). Parity with _make_on_raw_poll_vote_handler.
           if self._client is not None:
               cached_user = self._client.get_user(int(raw.user_id))
               if cached_user is not None and getattr(cached_user, "bot", False):
                   return

           # 3. Drop the ack emoji. ``str(raw.emoji)`` returns the
           # canonical form for both unicode and custom emoji
           # (discord.PartialEmoji.__str__).
           ack_emoji = self._access.ack_reaction
           if ack_emoji and str(raw.emoji) == ack_emoji:
               return

           # 4. Resolve the reactor's display name with the sticky
           # local cache. First miss → HTTP fetch_user; subsequent
           # reactions from same user → cache hit. Same helper used
           # by the poll-vote handler.
           user_display_name = await self._resolve_user_display_name(
               int(raw.user_id)
           )

           # 5. Build the Event envelope. Payload shape preserved
           # exactly from the previous handler so downstream consumers
           # don't see a schema change.
           data: dict[str, Any] = {
               "emoji": str(raw.emoji),
               "channel_id": str(raw.channel_id),
               "message_id": str(raw.message_id),
               "guild_id": str(raw.guild_id) if raw.guild_id else "",
               "user_id": str(raw.user_id),
               "user_display_name": user_display_name,
           }
           env = Envelope(
               id=uuid.uuid4().hex,
               correlation_id=uuid.uuid4().hex,
               to=self.target,
               kind="Event",
               payload=EventPayload(type="discord.reaction_add", data=data),
               created_at=datetime.now(UTC),
           )
           assert self._handle is not None
           await self._handle.publish(env)
           self._record_inbound(env)

       return on_raw_reaction_add
   ```

3. In the same file, at line 499 (in the `start()` method, immediately
   after the `on_message` listener registration), replace
   `self._add_listener(self._make_on_reaction_add_handler(), "on_reaction_add")`
   with
   `self._add_listener(self._make_on_raw_reaction_add_handler(), "on_raw_reaction_add")`.
   Update the comment block immediately above (lines 500-505) to
   include reactions in the "raw dispatch points" rationale — change
   the wording from *"Engagement events — wire the raw dispatch points
   so we always fire, even when the underlying message has been
   evicted..."* to add *"This applies to reactions (#171), poll votes
   (testbot 2026-05-05), and message edits/deletes — all share the
   cache-miss failure mode."*
4. In `packages/agent-core-discord/tests/test_endpoint_inbound.py`,
   add the `FakeRawReaction` class definition immediately after the
   existing `FakeRawMessageUpdate` block (after line 442), using the
   shape in the Acceptance criteria. Place it before the `@pytest.mark.asyncio`
   block of `test_on_raw_poll_vote_add_publishes_event_envelope` so all
   `FakeRaw*` classes cluster together (current layout convention).
5. In the same test file, delete the existing `FakeReaction` class
   (lines 280-283). It will be unused after the rewrites below.
6. In the same test file, rewrite the five existing reaction tests
   (lines 287-386) to drive the raw handler:
   - `test_on_reaction_add_publishes_event_envelope` →
     `test_on_raw_reaction_add_publishes_event_envelope`. Setup seeds
     a user via `fake.add_user(FakeUser(id="100", display_name="Alice"))`,
     fires `await fake.fire("on_raw_reaction_add", FakeRawReaction(
     message_id=1, channel_id=200, user_id=100, guild_id=300,
     emoji="👍"))`, then asserts the same payload-shape invariants the
     original test asserted (kind, EventPayload, `discord.reaction_add`,
     emoji, message_id, channel_id, user_id, user_display_name).
   - `test_on_reaction_add_drops_self_reactions` →
     `test_on_raw_reaction_add_drops_self_reactions`. Fire the raw
     event with `user_id` equal to `int(fake.user.id)` (the fake's
     bot user id, by default `"bot-1"` — convert appropriately, or
     adjust `FakeDiscordClient.user.id` to a numeric id in the test
     setup to match the raw-event integer semantics).
   - `test_on_reaction_add_drops_other_bots` →
     `test_on_raw_reaction_add_drops_other_bots`. Seed
     `fake.add_user(FakeUser(id="999", name="other-bot", bot=True))`,
     fire the raw event with `user_id=999`, assert `handle.published == []`.
   - `test_on_reaction_add_drops_ack_emoji` →
     `test_on_raw_reaction_add_drops_ack_emoji`. Fire the raw event with
     `emoji="👀"`, assert `handle.published == []`.
   - `test_on_reaction_add_dm_context` →
     `test_on_raw_reaction_add_dm_context`. Fire the raw event with
     `guild_id=None`, assert `env.payload.data["guild_id"] == ""` and
     `env.payload.data["channel_id"]` matches the input.
   Each rewritten test still uses the existing `_start_endpoint` helper
   (line 25-ish) and the existing `handle.published` assertion pattern.
7. In the same test file, add the two new tests immediately after the
   rewritten `test_on_raw_reaction_add_dm_context`:
   - `test_on_raw_reaction_add_falls_back_to_fetch_user_on_cache_miss`:
     copy the structure from
     `test_on_raw_poll_vote_add_falls_back_to_fetch_user_on_cache_miss`
     (line 477-500); seed via `fake.add_remote_user`, fire the raw
     reaction event, assert `user_display_name == "Alice"` and
     `fake.fetch_user_call_count == 1`.
   - `test_on_raw_reaction_add_dm_bot_message_live_shape`:
     uses the live-verified IDs from the issue (DM channel
     `1508863457762480289`); fires
     `FakeRawReaction(message_id=12345, channel_id=1508863457762480289,
     user_id=42, guild_id=None, emoji="🔥")`; asserts
     `len(handle.published) == 1`, `env.payload.type ==
     "discord.reaction_add"`, `env.payload.data["guild_id"] == ""`,
     `env.payload.data["channel_id"] == "1508863457762480289"`. This
     test is the structural pin for the bug; any future change that
     reverts to the cached listener fails it.
8. In `packages/agent-core-discord/tests/test_endpoint_hardening.py`,
   update `test_endpoint_registers_all_listeners` (line 158-180):
   change the line at 170 from
   `assert "on_reaction_add" in fake._handlers` to
   `assert "on_raw_reaction_add" in fake._handlers`. Move it below the
   "Engagement listeners" comment block (lines 172-174) so it cluster-
   documents the raw-dispatch rationale alongside polls and lifecycle
   events.
9. Create `packages/agent-core-discord/changelog.d/171.fixed.md` (new
   file; create the `changelog.d/` directory in the same step). Content
   is one line per the Acceptance criteria.
10. Run `just check` from the repo root and confirm zero exit. The
    full test suite matters because (a) the listener rename could break
    a hardening assertion you didn't expect, and (b) the
    `_resolve_user_display_name` cache path is shared with poll votes —
    a refactor mistake in step 2 could regress that.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Delete `_make_on_reaction_add_handler` (lines 1138-1171). Add `_make_on_raw_reaction_add_handler` in its place, modeled on `_make_on_raw_poll_vote_handler`. Swap the wire-up at line 499 from `on_reaction_add` to `on_raw_reaction_add`. Extend the listener-registration comment block (lines 500-505) to name reactions as a raw-dispatched engagement event. |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | Delete `FakeReaction` class. Add `FakeRawReaction` class clustered with the other `FakeRaw*` classes. Rename + rewrite the five `test_on_reaction_add_*` tests to drive `on_raw_reaction_add`. Add `test_on_raw_reaction_add_falls_back_to_fetch_user_on_cache_miss` (parity with poll-vote handler) and `test_on_raw_reaction_add_dm_bot_message_live_shape` (regression pin for the live-verified bug). |
| `packages/agent-core-discord/tests/test_endpoint_hardening.py` | In `test_endpoint_registers_all_listeners` (lines 158-180): change the `on_reaction_add` listener assertion to `on_raw_reaction_add`, and move it under the "Engagement listeners" comment block so its rationale is colocated. |
| `packages/agent-core-discord/changelog.d/171.fixed.md` | New file. One-line towncrier `fixed` entry. Creates the `changelog.d/` directory. |

## Alternatives considered

- **Add `intents.dm_reactions = True` explicitly and update the
  developer-portal runbook.** Rejected: `Intents.default()` already
  enables `dm_reactions` (it's a non-privileged intent; only
  presences/members/message_content are excluded), and the code
  already sets the combined `intents.reactions = True` alias which
  covers the same bit. There is no portal toggle to add (DM reactions
  is not privileged). The fix wouldn't change runtime behavior, and
  documenting a non-fix is worse than no doc.
- **Add a DM-aware filter in `on_reaction_add`.** Rejected: the
  handler has no DM-discriminating filter and the existing
  `test_on_reaction_add_dm_context` test passes today. There is
  nothing to filter; the handler never fires for DMs on bot-sent
  messages because discord.py's dispatch layer drops the
  cached-event variant when the message isn't in cache.
- **Keep both `on_reaction_add` and `on_raw_reaction_add` listeners.**
  Rejected: would publish duplicate envelopes for guild reactions
  (cached path AND raw path both fire), which is a worse regression
  than the bug we're fixing.
- **Use `on_raw_reaction_add` AND resolve the full `Message` and
  `User` via HTTP fetches for shape parity with the cached event.**
  Rejected: adds 1-2 HTTP round-trips per reaction (per-reaction
  latency + Discord rate limit budget). The payload shape needed by
  downstream consumers is just IDs + emoji + display name; the
  `_resolve_user_display_name` helper already gives us the only
  resolved field that mattered, with sticky caching.
- **File reactions as a separate engagement-events refactor that
  unifies polls, reactions, edits, and deletes under one factory.**
  Rejected as out of scope. The poll-vote and message-lifecycle
  handlers have meaningfully different payload shapes (poll votes
  carry `answer_id`; lifecycle events don't carry users at all);
  forcing them into one factory would generalize prematurely. The
  parity is in the dispatch-layer choice (raw vs cached), not in
  the handler bodies.
- **Move bot-author filtering for reactions into the access gate
  (`gate_message` / a new `gate_event`) as part of this PR.**
  Rejected as scope creep. The current spec preserves the
  best-effort cache-only filter (mirroring poll votes) so behavior
  doesn't regress; a structural gate-layer bot-filter for events is
  a separate, larger ticket (it would also touch poll votes and
  lifecycle events).

## Open questions

None blocking. The Reviewer may wish to confirm:

- That `client.get_user(int(raw.user_id))` is the right cache lookup
  on `FakeDiscordClient` (it's defined at
  `testing/fakes.py:403-405` and returns the cached user or `None`,
  matching real discord.py). The `add_user(FakeUser(bot=True))`
  primitive at line 407 is what the *drops_other_bots* test needs.

## Out of scope

- Discord developer-portal documentation. The root cause is not a
  missing intent, so the issue's conditional acceptance criterion
  for the runbook update does not trigger. We do not introduce
  documentation for a non-fix.
- The over-routing bug (agent_core#170). The spec's reaction
  handler still publishes every (non-self, non-bot, non-ack) DM
  reaction unconditionally — the DM-policy gate for reactions is
  #170's scope. `gate_message` / `gate_event` integration for
  reactions is explicitly deferred to that ticket.
- Touching `_make_on_raw_message_lifecycle_handler` (endpoint.py:1260).
  It's already wired against the raw event and not subject to the
  cache-dispatch problem this spec fixes. The issue note about
  filing a follow-up was conditional ("if confirmed during impl"); no
  separate bug has been confirmed.
- Resolving the full `Message` and `User` objects on the raw event
  for shape parity with the deleted cached handler. The payload
  shape published to the bus stays exactly the same; downstream
  consumers see no schema change.
- A structural `gate_event` access-gate layer for reactions / poll
  votes / lifecycle events. Today reactions and poll votes both rely
  on the same cache-best-effort bot filter; the structural fix
  belongs in a separate ticket alongside #170.
- Changing the `discord.reaction_add` event-payload shape.
  Downstream consumers (Wren's TaskList, agent reaction handlers)
  depend on the existing keys; the spec preserves them.
- `on_raw_reaction_remove` wiring. Today reactions only fire on
  ADD; the bus has no consumer for REMOVE. If a consumer surfaces,
  a follow-up adds the listener via the same factory.