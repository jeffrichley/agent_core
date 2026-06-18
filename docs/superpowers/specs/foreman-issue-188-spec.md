# Spec: centralize channel-gate across all Discord `on_*` handlers (issue #188)

## Goal

Eliminate the structural failure mode in `agent-core-discord` where every
new `on_*` event handler must remember to call the channel-allowlist gate.
Introduce one `DiscordEndpoint._gate_inbound(...)` decision function that
every handler (current and future) routes through, then wire every existing
inbound event factory to use it. The result: there is no path to add a new
event handler that silently leaks events from non-subscribed channels.

This ticket supersedes three open in-flight spec PRs (#170, #171, #180)
that each patched one handler. See issue
[#188](https://github.com/jeffrichley/agent_core/issues/188).

## Acceptance criteria

- `DiscordEndpoint._gate_inbound(*, event_kind: str, channel_id: int | str,
  author_id: int | str | None = None, is_dm: bool = False,
  is_bot: bool = False) -> bool` method exists on `DiscordEndpoint` in
  `packages/agent-core-discord/src/agent_core_discord/endpoint.py`. It
  returns `True` when the event should reach the bus, `False` when the
  caller must drop it. Internally it constructs an `InboundContext`
  threading the caller's `is_bot` flag (defaulting to `False`) so the
  `allowed_bot_ids` branch in `gate_message` (access.py:116) keeps
  working for the `on_message` call site — see Approach for why this is
  a keyword-only parameter with a `False` default rather than required.
  Delegates the actual policy check to the existing
  `gate_message(self._access, ctx)` in
  `packages/agent-core-discord/src/agent_core_discord/access.py:91`. The
  method docstring states the contract verbatim:
  *"Centralized for every on_* event handler. New handlers added in the
  future automatically inherit the gate — there is no path to forget it."*
- When `_gate_inbound` returns `False`, the caller's handler emits a
  `log.debug` line in the shape already used by `on_message` at
  endpoint.py:1024:
  ```python
  log.debug(
      "discord(%s): gate denied %s in channel %s (author=%s)",
      self.name,
      event_kind,
      channel_id,
      author_id if author_id is not None else "<none>",
  )
  ```
  Tier is `debug`, not `info`, to match the existing on_message deny log
  — a strict allowlist in a busy guild could otherwise spam the operator
  log on every dropped event.
- Every existing inbound event handler in `endpoint.py` calls
  `_gate_inbound` immediately AFTER the author-side filters (self-user
  drop, ack-emoji drop, bot-id drop) and BEFORE any side effects (envelope
  build, `self._handle.publish`, `self._record_inbound`,
  `_track_pending_ack`, attachment persistence, typing task spawn). The
  audited handlers:
  - `_make_on_message_handler` (currently endpoint.py:995-1136) — replaces
    the existing inline `gate_message(self._access, ctx)` call at
    endpoint.py:1023 with `if not self._gate_inbound(...)`. Preserves the
    full existing context (`author_id=str(message.author.id)`,
    `is_dm=message.guild is None`). The downstream envelope still pipes
    `is_bot` into `metadata["discord"]["is_bot"]` exactly as today
    (endpoint.py:1102) — the gate isn't the only place `is_bot` is read,
    so the handler still computes it locally.
  - `_make_on_raw_reaction_add_handler` (NEW, replaces
    `_make_on_reaction_add_handler` at endpoint.py:1138-1171). The swap
    from cached `on_reaction_add` to raw `on_raw_reaction_add` folds in
    issue #171's intent (DM-reaction coverage). Gate call uses
    `channel_id=raw.channel_id`, `author_id=raw.user_id`,
    `is_dm=raw.guild_id is None`.
  - `_make_on_raw_message_lifecycle_handler` (endpoint.py:1260-1289,
    wired for both `on_raw_message_edit` and `on_raw_message_delete` at
    endpoint.py:514-521). Gate call uses `channel_id=raw.channel_id`,
    `author_id=None` (raw lifecycle events do not carry an author),
    `is_dm=raw.guild_id is None`.
  - `_make_on_raw_poll_vote_handler` (endpoint.py:1209-1258, wired for
    both `on_raw_poll_vote_add` and `on_raw_poll_vote_remove` at
    endpoint.py:506-513). Gate call uses `channel_id=raw.channel_id`,
    `author_id=raw.user_id`, `is_dm=raw.guild_id is None`. Poll-vote
    gating was not in the original ticket text but falls naturally out
    of the "every on_*" rule and closes the same class of leak.
- Listener wiring in `endpoint.py:498-521` is updated to swap
  `on_reaction_add` for `on_raw_reaction_add`. No other listener names
  change (`on_message`, `on_raw_message_edit`, `on_raw_message_delete`,
  `on_raw_poll_vote_add`, `on_raw_poll_vote_remove`, and `on_ready`
  stay).
- `tests/test_endpoint_hardening.py::test_handlers_registered_via_add_listener`
  (currently endpoint_hardening.py:155-180, asserts `on_reaction_add`)
  is updated to assert `"on_raw_reaction_add" in fake._handlers` instead
  and to drop the assertion for `"on_reaction_add"`. The accompanying
  "Engagement listeners" comment is extended to cover the reaction
  swap rationale.
- A new fake `FakeRawReaction` is added to
  `tests/test_endpoint_inbound.py` (next to the existing `FakeReaction`
  at line 280-284, the `FakeRawPollVote` at line 396-416, and the
  `FakeRawMessageDelete` at line 419-427) that mirrors discord.py's
  `RawReactionActionEvent` shape: attributes `message_id`, `channel_id`,
  `user_id`, `guild_id` (Optional, `None` for DMs), `emoji` (a
  `FakePartialEmoji`-shaped object whose `__str__` returns the unicode
  char or `<:name:id>`). One companion `FakePartialEmoji` class with
  `__str__` is added in the same file.
- Per-handler regression tests are added or updated in
  `packages/agent-core-discord/tests/test_endpoint_inbound.py`:
  1. `test_on_message_dropped_when_channel_not_in_allowlist` — existing
     `test_on_message_respects_channel_allowlist` (line 242) already
     covers the negative case; no new test needed. Rename is NOT
     required.
  2. `test_on_raw_reaction_add_dropped_when_channel_not_in_allowlist`
     — access.json with `channels: {"200": {}}`, raw reaction event with
     `channel_id=999`, assert `handle.published == []`.
  3. `test_on_raw_reaction_add_publishes_when_channel_in_allowlist` —
     positive companion to #2. Same access.json, `channel_id=200`,
     assert one envelope published with the new payload shape
     (`emoji`, `channel_id`, `message_id`, `guild_id`, `user_id`,
     `user_display_name`).
  4. `test_on_raw_reaction_add_dm_follows_dm_policy_deny` —
     access.json `{"dmPolicy": "deny"}`, raw reaction with
     `guild_id=None`, assert no publish.
  5. `test_on_raw_message_edit_dropped_when_channel_not_in_allowlist` —
     access.json `channels: {"200": {}}`, raw edit event with
     `channel_id=999`, assert no publish.
  6. `test_on_raw_message_delete_dropped_when_channel_not_in_allowlist`
     — same access.json, raw delete with `channel_id=999`, assert no
     publish. Pinning the asymmetry observation from the issue: deletes
     and edits don't carry author identity, so `channel_id` is the only
     meaningful filter and the gate must apply.
  7. `test_on_raw_poll_vote_add_dropped_when_channel_not_in_allowlist`
     — access.json `channels: {"200": {}}`, raw poll vote with
     `channel_id=999`, assert no publish.
  8. `test_on_raw_message_edit_dm_follows_dm_policy_deny` — access.json
     `{"dmPolicy": "deny"}`, raw edit with `guild_id=None`, assert no
     publish. Documents the DM-side coverage for lifecycle events.
- Asymmetry-regression test
  `test_on_raw_reaction_add_drops_bot_self_reactions_before_gate` is
  added (or carried forward from the existing
  `test_on_reaction_add_drops_self_reactions` at line 315). It asserts
  that even with a permissive access config, a reaction from the bot's
  own user is dropped by the author-side filter (i.e. the gate is not
  the only line of defence). This is the "asymmetry-coverage" criterion
  the issue called out.
- Existing tests in `test_endpoint_inbound.py` that were author-shaped
  for the cached `on_reaction_add` listener
  (`test_on_reaction_add_publishes_event_envelope` line 287,
  `test_on_reaction_add_drops_self_reactions` line 315,
  `test_on_reaction_add_drops_other_bots` line 333,
  `test_on_reaction_add_drops_ack_emoji` line 351,
  `test_on_reaction_add_dm_context` line 370) are rewritten against the
  raw fake. Names change from `on_reaction_add` to
  `on_raw_reaction_add`; assertion bodies stay the same shape (envelope
  kind = `Event`, payload type = `discord.reaction_add`, payload data
  fields unchanged). The handler-internal logic that ack-emoji and
  bot-self drops happen BEFORE the gate is preserved, so these tests
  continue to pass with the default access config.
- `_gate_inbound` is exported as a private method (leading underscore).
  No `__all__` change. No external callers expected.
- A towncrier news fragment lands at
  `packages/agent-core-discord/changelog.d/188.changed.md` (use the
  `changed` slug — the gate change affects observable behaviour by
  closing leaks, but the user-visible API surface stays identical;
  `fixed` would also be defensible — pick `changed` to signal that
  operators should re-check their `channels` allowlist coverage now
  that ALL event types respect it). Content:
  *"All Discord inbound event handlers now route through a single
  channel-allowlist + dm_policy gate (`DiscordEndpoint._gate_inbound`).
  Reactions, message edits, message deletes, and poll votes in
  non-allowlisted channels are now silently dropped — matching
  `on_message` semantics. Closes the leaks tracked in #170, #171, #180.
  Operators with strict channel allowlists may see fewer Event
  envelopes; this is the intended behaviour. (#188)"*
- `just check` (lint + typecheck + tests) exits zero on the repo root.
  `new_failures_count == 0` against the full suite.
- The diff includes deletion of `_make_on_reaction_add_handler`; the
  rename in the listener-wiring block; the new `_gate_inbound` method;
  and edits to each of the four other handler factories that thread
  `_gate_inbound` into their bodies. Reviewer can eyeball the parity
  by reading one method (`_gate_inbound`) and confirming each handler
  calls it.

## Approach

**Pattern naming.** No GoF pattern fits exactly. The relevant Google
engineering principle is **"make the right thing easy, and the wrong
thing hard"** — today, forgetting the gate is a one-line omission with no
compiler or test signal; centralising the gate doesn't make forgetting it
impossible, but it makes the omission visible (a new handler factory that
doesn't reference `_gate_inbound` is conspicuous in code review). This is
also a small instance of **Single Responsibility / DRY at the policy
layer**: the policy "may this Discord event reach the bus?" lives in one
place, not five.

**Why a method on `DiscordEndpoint`, not a module-level helper.** The
gate needs `self._access`, which is endpoint-scoped state loaded at
`start()` time (endpoint.py:481). Promoting to a free function would mean
threading `access` through every call site, defeating the centralisation
goal. Promoting to a `staticmethod` on the class doesn't help either —
same threading problem. An instance method is the right shape.

**Why the gate signature is `(channel_id, author_id, is_dm)` and not
`InboundContext`.** Different event types build the context from
different sources (`message.channel.id` vs `raw.channel_id`,
`message.guild is None` vs `raw.guild_id is None`, etc.) and some
events have no author at all (raw deletes). Passing an
already-constructed `InboundContext` would either force every caller
to know about it (leaking the access-module type into endpoint code that
otherwise doesn't import it) or force the gate to accept a heterogeneous
union of shapes. Keyword-only primitives are simpler and let
`_gate_inbound` own the `InboundContext` construction internally.

**Why `is_bot` IS a parameter of `_gate_inbound` (with a `False` default).**
Bot-self filtering is an author-shaped concern that needs access to the
discord.py object (`message.author.bot`, `user.bot`, the raw event's
local-cache lookup pattern from `_make_on_raw_poll_vote_handler`
lines 1221-1225). Per-handler bot-self drops happen BEFORE the gate
runs (matching today's `on_message` ordering at endpoint.py:1006-1007),
so by the time `_gate_inbound` is called, non-message handlers have
already discarded bots upstream and pass the default `is_bot=False`.
The `on_message` handler is the exception: its existing `gate_message`
call threads the real `is_bot` flag through to the `allowed_bot_ids`
branch in `access.py:116`, which is the regression guard PR #158 /
2026-06-07 added (and which the
`test_on_message_allows_bots_in_allowed_bot_ids` test at
test_endpoint_inbound.py:97 pins). To preserve that semantics WITHOUT
making the on_message call site reach around the gate, `_gate_inbound`
accepts `is_bot` as an optional keyword-only parameter with a `False`
default. The on_message factory passes its locally-computed flag
through; reaction / lifecycle / poll-vote factories accept the default
because they've already filtered bots author-side. The default keeps
the non-message call sites terse and forces a caller to opt in if
they ever need bot-aware policy — which today they don't.

  This also addresses an inconsistency the issue calls out: edits and
  deletes don't have a bot-self filter today (and don't get one in this
  spec — see Out of scope). That asymmetry is preserved because raw
  lifecycle events don't carry author identity and the rate of
  bot-self edits/deletes is empirically negligible. The channel gate
  catches the leak that matters.

**Why swap `on_reaction_add` → `on_raw_reaction_add` here (folding in
#171).** Issue #171's spec already justifies the swap independently
(DM reactions, cache-evicted messages, parity with poll-vote and
lifecycle listeners). Doing both edits in one PR is cheaper than
sequencing them: the test rewrite from cached fakes to raw fakes
happens once, the handler factory is renamed once, the listener
registration is touched once. Sequencing would mean either (a) two
churn-y PRs that touch the same lines twice, or (b) implementing #171
first and then immediately rewriting the same code for #188. The
issue text explicitly asks for the fold-in.

**Why NOT extend the swap to `on_message_edit` and `on_message_delete`.**
The issue body lists those as acceptance criteria, but the codebase
already wires the raw variants (`on_raw_message_edit`,
`on_raw_message_delete` at endpoint.py:514-521 via
`_make_on_raw_message_lifecycle_handler`). The issue body was authored
against a stale mental model of the file. No swap is needed for those
two — only the gate insertion.

**Why poll-vote gating is included.** The issue text says "every `on_*`
event handler" but only enumerates message / reaction / edit / delete in
the test matrix. Poll-vote handlers are structurally identical
(`on_raw_poll_vote_add`, `on_raw_poll_vote_remove` at
endpoint.py:506-513) and have the same leak: a vote on a poll in a
non-allowlisted channel routes to the bus today. Gating them here
follows the "no path to forget" goal — leaving them out would re-create
the asymmetry the spec exists to eliminate. The cost is one extra line
in each handler and three extra tests.

**Conventions.** Inline gate call sites follow the
`_make_on_message_handler` ordering (endpoint.py:1006-1030):
author-side guards first, gate second, side effects third. Tests use
the `_start_endpoint(monkeypatch, access_path=...)` pattern from
test_endpoint_inbound.py:28-42 and the access.json shape from
test_endpoint_inbound.py:242. Test naming uses the existing
`test_on_<event>_<scenario>` convention. The towncrier fragment goes
under `packages/agent-core-discord/changelog.d/<issue>.<type>.md` per
the package's `towncrier.toml`.

## Sub-requests (topologically sorted)

1. In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`,
   add the new `_gate_inbound` method on `DiscordEndpoint`. Place it
   immediately above `_make_on_message_handler` (around line 994) so
   it lives next to the handlers it serves:

   ```python
   def _gate_inbound(
       self,
       *,
       event_kind: str,
       channel_id: int | str,
       author_id: int | str | None = None,
       is_dm: bool = False,
       is_bot: bool = False,
   ) -> bool:
       """Return True if the event should be routed to the bus.

       Centralized for every on_* event handler. New handlers added in
       the future automatically inherit the gate — there is no path to
       forget it. Delegates to `gate_message` in `access.py`. Bot-self
       filtering is the caller's job (per-handler author-side drops
       happen before this method is called); the `is_bot` keyword is
       threaded into the gate's internal `InboundContext` so callers
       that need bot-aware policy (currently only `_make_on_message_handler`
       via the `allowed_bot_ids` branch at access.py:116) can opt in.
       Non-message handlers should accept the `False` default because
       they've already discarded bot events upstream.
       """
       ctx = InboundContext(
           is_dm=is_dm,
           author_id=str(author_id) if author_id is not None else "",
           channel_id=str(channel_id),
           is_bot=is_bot,
       )
       if not gate_message(self._access, ctx):
           log.debug(
               "discord(%s): gate denied %s in channel %s (author=%s)",
               self.name,
               event_kind,
               channel_id,
               author_id if author_id is not None else "<none>",
           )
           return False
       return True
   ```

2. Replace the inline gate call in `_make_on_message_handler` at
   endpoint.py:1014-1030. The new shape:

   ```python
   is_dm = message.guild is None
   is_bot = bool(getattr(message.author, "bot", False))
   if not self._gate_inbound(
       event_kind="message",
       channel_id=message.channel.id,
       author_id=message.author.id,
       is_dm=is_dm,
       is_bot=is_bot,
   ):
       return
   ```

   `is_bot` is computed locally (and threaded through to the gate)
   because endpoint.py:1102 also reads it into
   `metadata["discord"]["is_bot"]` for downstream consumers, AND
   because the gate's underlying `gate_message` routes through the
   `allowed_bot_ids` branch at access.py:116 when `is_bot=True`. The
   InboundContext that today threads `is_bot` into the gate
   (endpoint.py:1015-1020) collapses into the `_gate_inbound` call
   above; the policy outcome is identical.

   **REQUIRED follow-up edit at endpoint.py:1102.** Deleting the
   `ctx = InboundContext(...)` block at endpoint.py:1015-1020 leaves
   a dangling reference to `ctx.is_bot` in the `metadata` dict at
   endpoint.py:1102 (it populates `metadata["discord"]["is_bot"]`
   from the about-to-be-removed local). Update line 1102 from
   `"is_bot": ctx.is_bot,` to `"is_bot": is_bot,` so the metadata
   dict reads the new local variable bound at the top of the new
   shape above. Without this edit the handler raises `NameError` at
   the first inbound message. Surrounding context for pattern
   match:

   ```python
   metadata: dict[str, Any] = {
       "discord": {
           "channel_id": str(message.channel.id),
           "message_id": str(message.id),
           "guild_id": str(message.guild.id) if message.guild else "",
           "author_id": str(message.author.id),
           "author_display_name": getattr(message.author, "display_name", "") or "",
           "is_dm": is_dm,
           # is_bot piped through so downstream beings can tell "this
           # is from another agent-core being" vs "this is from Jeff"
           # without inspecting bot id maps. Pairs with the
           # allowed_bot_ids gate (agent_core#143).
           "is_bot": is_bot,  # was ctx.is_bot — ctx no longer exists.
       },
   }
   ```

   **`allowed_bot_ids` regression note.** This is exactly why
   `_gate_inbound`'s signature in Sub-request #1 carries `is_bot` as
   a keyword-only parameter rather than hardcoding `False` internally.
   Moving the on_message call site to `_gate_inbound(is_bot=False)`
   (or omitting the kwarg and accepting the default) would silently
   re-introduce the 2026-06-07 bug from PR #158:
   bots-from-`allowed_bot_ids` would be admitted without ever
   consulting the allowlist. The
   `test_on_message_allows_bots_in_allowed_bot_ids` test at
   test_endpoint_inbound.py:97 pins this path; passing `is_bot=is_bot`
   here keeps it green. The reaction / lifecycle / poll-vote call
   sites legitimately omit the `is_bot` kwarg (accepting the `False`
   default) because their per-handler author-side filter has already
   dropped bot events upstream.

3. Rename `_make_on_reaction_add_handler` →
   `_make_on_raw_reaction_add_handler`. Rewrite the inner coroutine to
   accept a single `raw: Any` parameter (per discord.py
   `RawReactionActionEvent` shape). Mirror the bot-self filter from
   `_make_on_raw_poll_vote_handler` lines 1221-1225 (lookup by user id
   against `self._client.user.id`); preserve the ack-emoji drop with
   `str(raw.emoji)`; preserve the local-cache lookup pattern for
   opportunistic other-bot filtering (use `self._client.get_user(...)`
   with `getattr(user, "bot", False)`). After the author-side drops,
   call:

   ```python
   if not self._gate_inbound(
       event_kind="reaction_add",
       channel_id=raw.channel_id,
       author_id=raw.user_id,
       is_dm=raw.guild_id is None,
   ):
       return
   ```

   Then build the Event envelope. Resolve `user_display_name` with the
   existing `await self._resolve_user_display_name(int(raw.user_id))`
   helper (endpoint.py:1173-1207) — same pattern poll-vote uses to
   maintain parity with cached events.

4. Update listener wiring at endpoint.py:499. Change:

   ```python
   self._add_listener(self._make_on_reaction_add_handler(), "on_reaction_add")
   ```

   to:

   ```python
   self._add_listener(
       self._make_on_raw_reaction_add_handler(), "on_raw_reaction_add"
   )
   ```

5. In `_make_on_raw_message_lifecycle_handler` (endpoint.py:1260-1289),
   add the gate as the FIRST step of the inner coroutine, before the
   `data` dict construction at line 1272:

   ```python
   async def on_raw_message_lifecycle(raw: Any) -> None:
       if not self._gate_inbound(
           event_kind=event_type,  # "discord.message_edit" or "discord.message_delete"
           channel_id=raw.channel_id,
           author_id=None,  # raw lifecycle events have no author
           is_dm=raw.guild_id is None,
       ):
           return
       data: dict[str, Any] = {
           "message_id": str(raw.message_id),
           ...
       }
   ```

6. In `_make_on_raw_poll_vote_handler` (endpoint.py:1209-1258), add the
   gate AFTER the existing bot-self drop (lines 1221-1225) and BEFORE
   the `_resolve_user_display_name` call at line 1234 (so the gate
   short-circuits the HTTP fetch for a denied vote):

   ```python
   if str(getattr(raw, "user_id", "")) == str(self_id):
       return

   if not self._gate_inbound(
       event_kind=event_type,  # "discord.poll_vote_add" or "discord.poll_vote_remove"
       channel_id=raw.channel_id,
       author_id=raw.user_id,
       is_dm=raw.guild_id is None,
   ):
       return

   user_display_name = await self._resolve_user_display_name(...)
   ```

7. In `packages/agent-core-discord/tests/test_endpoint_inbound.py`,
   add `FakePartialEmoji` and `FakeRawReaction` classes near the
   existing fake event classes (after the `FakeReaction` at line 280
   or near the `FakeRawMessageDelete` at line 419):

   ```python
   class FakePartialEmoji:
       """Mirrors discord.PartialEmoji __str__ semantics."""

       def __init__(self, *, name: str, id: int | None = None) -> None:
           self.name = name
           self.id = id

       def __str__(self) -> str:
           if self.id is not None:
               return f"<:{self.name}:{self.id}>"
           return self.name


   class FakeRawReaction:
       """Mirrors discord.RawReactionActionEvent shape."""

       def __init__(
           self,
           *,
           message_id: int,
           channel_id: int,
           user_id: int,
           guild_id: int | None,
           emoji: FakePartialEmoji,
       ) -> None:
           self.message_id = message_id
           self.channel_id = channel_id
           self.user_id = user_id
           self.guild_id = guild_id
           self.emoji = emoji
   ```

8. Rewrite the five existing reaction tests at
   test_endpoint_inbound.py:287 (`test_on_reaction_add_publishes_event_envelope`),
   :315 (`_drops_self_reactions`), :333 (`_drops_other_bots`), :351
   (`_drops_ack_emoji`), :370 (`_dm_context`). For each:
   - Rename `on_reaction_add` to `on_raw_reaction_add` in the
     `fake.fire(...)` call.
   - Replace `FakeReaction(emoji="👍", message=msg)` and the trailing
     `user` argument with a single `FakeRawReaction(...)` that carries
     the same emoji/channel/user/guild ids.
   - For `_drops_other_bots`, register the bot user via
     `fake.add_user(FakeUser(id="999", name="other-bot", bot=True))`
     so the handler's `client.get_user(raw.user_id)` synchronous
     lookup finds the bot flag. This matches the pattern documented
     in `_resolve_user_display_name` (endpoint.py:1196-1199).
   - **Seed the test user via `fake.add_user(...)` for the publish-
     shape tests.** The cached `on_reaction_add` listener received
     the `user` object directly as a fire argument and could read
     `user.display_name` synchronously. The raw handler instead
     resolves display name via
     `self._resolve_user_display_name(int(raw.user_id))`, which calls
     `client.get_user(raw.user_id)` first and only falls through to
     async `client.fetch_user(...)` on a miss. `FakeDiscordClient.get_user`
     (fakes.py:403-405) returns `None` for unseeded ids and
     `fetch_user` (fakes.py:410-424) raises `LookupError` when the
     user wasn't also added via `add_remote_user`, which the handler
     swallows and substitutes `""` for the display name. Concretely:
       - In `test_on_reaction_add_publishes_event_envelope`
         (line 287), add
         `fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))`
         AFTER the existing `fake.add_channel(...)` line and BEFORE
         the `fake.fire(...)` call. This keeps the existing
         `env.payload.data["user_display_name"] == "Alice"`
         assertion green via the synchronous cache hit.
       - In `test_on_reaction_add_dm_context` (line 370), do the
         same — its assertions don't check `user_display_name`
         today but the seeding keeps the handler path noise-free
         (no spurious `fetch_user` LookupError + warning log per
         test run).
   - For `_drops_self_reactions`, `_drops_ack_emoji`: no extra
     seeding required — the author-side drop short-circuits before
     `_resolve_user_display_name` ever runs.
   - The envelope-shape assertions stay the same (kind=Event, type
     `discord.reaction_add`, six-field payload data).

   The same seeding rule applies to the NEW
   `test_on_raw_reaction_add_publishes_when_channel_in_allowlist`
   in sub-request #9 below: include
   `fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))`
   so the publish assertion sees `"Alice"`, not `""`.

9. Add the new regression tests in `test_endpoint_inbound.py` after
   the rewritten reaction tests:

   - `test_on_raw_reaction_add_dropped_when_channel_not_in_allowlist`
   - `test_on_raw_reaction_add_publishes_when_channel_in_allowlist`
   - `test_on_raw_reaction_add_dm_follows_dm_policy_deny`
   - `test_on_raw_message_edit_dropped_when_channel_not_in_allowlist`
   - `test_on_raw_message_delete_dropped_when_channel_not_in_allowlist`
   - `test_on_raw_message_edit_dm_follows_dm_policy_deny`
   - `test_on_raw_poll_vote_add_dropped_when_channel_not_in_allowlist`

   Each follows the `_start_endpoint(monkeypatch, access_path=...)`
   pattern with a temp-file access config. Shapes mirror existing
   patterns (see test_endpoint_inbound.py:242 for the channel-allowlist
   pattern, :223 for the dm-policy-deny pattern).

10. Update
    `tests/test_endpoint_hardening.py::test_handlers_registered_via_add_listener`
    (endpoint_hardening.py:155-180). Change line 170 from
    `assert "on_reaction_add" in fake._handlers` to
    `assert "on_raw_reaction_add" in fake._handlers`. Extend the
    inline comment block (lines 172-174) to mention the reaction-swap
    reason ("on_raw_reaction_add so DM reactions reach the bus —
    discord.py omits message.guild on DM reactions in the cached
    dispatcher").

11. Create `packages/agent-core-discord/changelog.d/188.changed.md`
    with the one-paragraph content from Acceptance criteria.

12. Run from the repo root:
    ```bash
    uv run pytest packages/agent-core-discord/tests -v
    just check
    ```
    Confirm zero failures and zero lint/typecheck errors.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | (1) Add `_gate_inbound(*, event_kind, channel_id, author_id=None, is_dm=False, is_bot=False) -> bool` method on `DiscordEndpoint`. (2) Replace inline `gate_message` call in `_make_on_message_handler` with `_gate_inbound`; pass `is_bot` through. (3) Rename `_make_on_reaction_add_handler` → `_make_on_raw_reaction_add_handler`; rewrite inner coroutine against `raw: Any`; mirror raw-event bot-filter pattern from `_make_on_raw_poll_vote_handler`; insert `_gate_inbound` call between author-side filters and envelope build. (4) Add `_gate_inbound` call at top of `_make_on_raw_message_lifecycle_handler`'s inner coroutine. (5) Add `_gate_inbound` call in `_make_on_raw_poll_vote_handler` after the bot-self drop, before `_resolve_user_display_name`. (6) Listener wiring at endpoint.py:499 renames `"on_reaction_add"` → `"on_raw_reaction_add"`. No new imports — `gate_message` and `InboundContext` already imported at line 38. |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | (1) Add `FakePartialEmoji` and `FakeRawReaction` classes. (2) Rewrite five existing `test_on_reaction_add_*` tests to fire `on_raw_reaction_add` with the new fake shape. (3) Add seven new regression tests (channel-deny and dm-policy-deny coverage for reaction, edit, delete, poll_vote). (4) The existing on_message gate tests at line 223 and 242 are unchanged. |
| `packages/agent-core-discord/tests/test_endpoint_hardening.py` | Update `test_handlers_registered_via_add_listener` to assert `"on_raw_reaction_add"` instead of `"on_reaction_add"`. |
| `packages/agent-core-discord/changelog.d/188.changed.md` | New file. One-paragraph towncrier `changed` entry per Acceptance criteria. |

## Alternatives considered

- **Land #170, #171, #180 separately as originally specced.** Would
  produce three impl PRs over a week. Each touches the same handler
  factories and the same test file. The fold-in proposed here ships
  one PR with one rewrite of the reaction tests (cached → raw) and
  one new gate-call pattern reused four times. The fold-in also closes
  the structural gap (no path to forget the gate on the NEXT handler)
  that the three sub-specs leave open. Rejected: the cumulative
  diff is smaller and the structural fix matters.
- **Implement `_gate_inbound` as a free function in `access.py`.**
  Would need `access` threaded through every call. Rejected — the
  method form keeps endpoint state local and lets future per-endpoint
  policy hooks land here without re-plumbing.
- **Make `is_bot` part of the gate signature with no default.** Forces
  every caller to think about bot semantics, which is exactly what
  this spec is trying to AVOID (the gate is about channel + dm_policy;
  bot filtering is upstream). Rejected — defaulting `is_bot=False` and
  documenting that bot-side filtering happens upstream is the
  least-surprise shape for handler authors.
- **Replace the existing per-handler bot drops with a centralized
  bot-filter helper too.** Tempting (same DRY argument), but bot
  detection differs per event source: `message.author.bot` for
  cached messages, `user.bot` for cached reactions, opportunistic
  local-cache lookup for raw events that only carry IDs. The
  asymmetry is real (edits/deletes don't have authors at all). Out
  of scope here; file a follow-up if the asymmetry causes pain.
- **Extend the gate to per-event-type allowlists** (e.g. "allow
  edits but deny reactions in this channel"). The issue explicitly
  marks this Out of scope and the existing allowlist semantics are
  channel-uniform. Defer until a concrete need surfaces.
- **Add a runtime assertion that every registered listener calls
  `_gate_inbound` at least once** (e.g. wrap `_add_listener` in a
  decorator that traces gate calls). Would catch the "new handler
  forgot the gate" defect mechanically, but the implementation is
  fiddly (async generators, recursion through `gate_message`, false
  positives for handlers that legitimately don't need gating).
  Rejected — the code-review surface of a five-line `_gate_inbound`
  call is small enough that visual review is sufficient. Revisit if
  the leak class recurs.
- **Do nothing — close #188 and let #170/#171/#180 land separately.**
  Rejected: the issue explicitly cites three patches across three
  PRs as evidence that the per-handler approach is structurally
  wrong. The centralized gate is the fix the codebase has been
  asking for since at least #170.

## Open questions

- **Coordination with the in-flight specs #170, #171, #180.** Their
  spec PRs are merged on this branch (commits 9502291, 8a88a66,
  a4cd635) but no impl PRs have landed. After #188's impl PR merges,
  the daemon will close #170, #171, #180 as superseded. The Reviewer
  on the impl PR should confirm the impl PR's commit message
  references all three issue numbers so GitHub's cross-reference
  graph stays clean. (Foreman's `merge_impl_pr` action handles the
  issue-close routing — see project CLAUDE.md and the foreman#63
  notes — so the `pr_body` does NOT use closing keywords. The closes
  happen automatically when the impl PR's lifecycle advances past the
  reviewer gate.)
- **Should `is_bot` also gain a default in `gate_message` itself?**
  Currently `InboundContext.is_bot` is non-optional (access.py:45),
  forcing every caller to think about it. The spec's
  `_gate_inbound(is_bot=False)` default papers over this. Tightening
  `gate_message` is out of scope — flagged for a follow-up if the
  pattern repeats elsewhere.

## Out of scope

- Configurable per-event-type gates (issue Out of scope: explicit).
- Bot-self filtering for edits/deletes (issue Out of scope: explicit).
- Allowlist editing UI/API (issue Out of scope: explicit).
- Refactoring `access.py` to expose a thinner `gate_channel(...)`
  function. `gate_message` already only consumes `InboundContext`
  fields and is the right seam.
- Touching outbound tool dispatch or any `_deliver_*` paths.
- Closing #170/#171/#180 manually — Foreman's lifecycle handles it
  via `merge_impl_pr` after the impl PR lands.
- Adding tests for `on_ready`, `on_disconnect`, or other
  non-inbound-event lifecycle hooks. They don't carry channel-scoped
  data and the gate concept doesn't apply.
- A migration note about the `on_reaction_add` → `on_raw_reaction_add`
  swap in any operator-facing docs. The package's CHANGELOG entry
  (via towncrier) is the source of truth; no additional doc needed.
