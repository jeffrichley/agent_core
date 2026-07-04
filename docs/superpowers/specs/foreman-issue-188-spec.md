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
- When `_gate_inbound` returns `False`, the method itself emits a
  `log.debug` line in the shape already used by `on_message` (the deny log
  inside `_gate_inbound`):
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
  - `_make_on_message_handler` — replaces the existing inline
    `gate_message(self._access, ctx)` call and its preceding
    `ctx = InboundContext(...)` block with a `_gate_inbound(...)` call.
    Preserves the full existing context (`author_id=str(message.author.id)`,
    `is_dm=message.guild is None`). The downstream envelope still pipes
    `is_bot` into `metadata["discord"]["is_bot"]` exactly as today
    — the gate isn't the only place `is_bot` is read, so the handler
    still computes it locally. **IMPORTANT:** the existing line
    `"is_bot": ctx.is_bot,` in the metadata dict must be updated to
    `"is_bot": is_bot,` since `ctx` will no longer exist.
  - `_make_on_raw_reaction_add_handler` (NEW, replaces
    `_make_on_reaction_add_handler`). The swap from cached
    `on_reaction_add` to raw `on_raw_reaction_add` folds in issue #171's
    intent (DM-reaction coverage). Gate call uses
    `channel_id=raw.channel_id`, `author_id=raw.user_id`,
    `is_dm=raw.guild_id is None`.
  - `_make_on_raw_message_lifecycle_handler` (wired for both
    `on_raw_message_edit` and `on_raw_message_delete`). Gate call uses
    `channel_id=raw.channel_id`, `author_id=None` (raw lifecycle events
    do not carry an author), `is_dm=raw.guild_id is None`. The existing
    `self._channel_allowed(str(raw.channel_id))` call is replaced.
  - `_make_on_raw_poll_vote_handler` (wired for both
    `on_raw_poll_vote_add` and `on_raw_poll_vote_remove`). Gate call uses
    `channel_id=raw.channel_id`, `author_id=raw.user_id`,
    `is_dm=raw.guild_id is None`. Poll-vote gating was not in the
    original ticket text but falls naturally out of the "every on_*"
    rule and closes the same class of leak. The existing
    `self._channel_allowed(str(raw.channel_id))` call is replaced.
- The `_channel_allowed` helper method (currently at endpoint.py around
  line 1253) becomes dead code after the above changes and is removed from
  `DiscordEndpoint`. All callers switch to `_gate_inbound`.
- Listener wiring in `endpoint.py` is updated to swap
  `on_reaction_add` for `on_raw_reaction_add`. No other listener names
  change (`on_message`, `on_raw_message_edit`, `on_raw_message_delete`,
  `on_raw_poll_vote_add`, `on_raw_poll_vote_remove`, and `on_ready`
  stay).
- `tests/test_endpoint_hardening.py::test_handlers_registered_via_add_listener`
  (currently asserts `"on_reaction_add"`) is updated to assert
  `"on_raw_reaction_add" in fake._handlers` instead and to drop the
  assertion for `"on_reaction_add"`. The accompanying "Engagement
  listeners" comment is extended to cover the reaction swap rationale.
- A new fake `FakeRawReaction` is added to
  `tests/test_endpoint_inbound.py` (next to the existing `FakeReaction`
  and the existing `FakeRawPollVote` / `FakeRawMessageDelete` fakes) that
  mirrors discord.py's `RawReactionActionEvent` shape: attributes
  `message_id`, `channel_id`, `user_id`, `guild_id` (Optional, `None`
  for DMs), `emoji` (a `FakePartialEmoji`-shaped object whose `__str__`
  returns the unicode char or `<:name:id>`). One companion
  `FakePartialEmoji` class with `__str__` is added in the same file.
- Per-handler regression tests are added or updated in
  `packages/agent-core-discord/tests/test_endpoint_inbound.py`:
  1. `test_on_message_dropped_when_channel_not_in_allowlist` — the
     existing `test_on_message_respects_channel_allowlist` already covers
     the negative case; no new test needed. Rename is NOT required.
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
  `test_on_reaction_add_drops_self_reactions`). It asserts that even with
  a permissive access config, a reaction from the bot's own user is
  dropped by the author-side filter (i.e. the gate is not the only line
  of defence). This is the "asymmetry-coverage" criterion the issue
  called out.
- Existing tests in `test_endpoint_inbound.py` that were author-shaped
  for the cached `on_reaction_add` listener
  (`test_on_reaction_add_publishes_event_envelope`,
  `test_on_reaction_add_drops_self_reactions`,
  `test_on_reaction_add_drops_other_bots`,
  `test_on_reaction_add_drops_ack_emoji`,
  `test_on_reaction_add_dm_context`) are rewritten against the raw fake.
  Names change from `on_reaction_add` to `on_raw_reaction_add`; assertion
  bodies stay the same shape (envelope kind = `Event`, payload type =
  `discord.reaction_add`, payload data fields unchanged). The
  handler-internal logic that ack-emoji and bot-self drops happen BEFORE
  the gate is preserved, so these tests continue to pass with the default
  access config.
  - **Seed the test user via `fake.add_user(...)` for publish-shape
    tests.** The cached `on_reaction_add` listener received the `user`
    object directly and could read `user.display_name` synchronously. The
    raw handler resolves display name via
    `self._resolve_user_display_name(int(raw.user_id))`, which calls
    `client.get_user(raw.user_id)` first and falls through to async
    `client.fetch_user(...)` on a miss. Add
    `fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))`
    AFTER the existing `fake.add_channel(...)` line and BEFORE the
    `fake.fire(...)` call in publish-shape tests to ensure a synchronous
    cache hit.
- `_gate_inbound` is a private method (leading underscore). No `__all__`
  change. No external callers expected.
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
- The diff includes deletion of `_make_on_reaction_add_handler`; deletion
  of `_channel_allowed`; the rename in the listener-wiring block; the new
  `_gate_inbound` method; and edits to each of the four other handler
  factories that thread `_gate_inbound` into their bodies. Reviewer can
  eyeball the parity by reading one method (`_gate_inbound`) and
  confirming each handler calls it.

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
`start()` time. Promoting to a free function would mean threading `access`
through every call site, defeating the centralisation goal. Promoting to
a `staticmethod` on the class doesn't help either — same threading
problem. An instance method is the right shape.

**Why the gate signature is `(channel_id, author_id, is_dm)` and not
`InboundContext`.** Different event types build the context from different
sources (`message.channel.id` vs `raw.channel_id`, `message.guild is
None` vs `raw.guild_id is None`, etc.) and some events have no author at
all (raw deletes). Passing an already-constructed `InboundContext` would
either force every caller to know about it (leaking the access-module type
into endpoint code that otherwise doesn't import it) or force the gate to
accept a heterogeneous union of shapes. Keyword-only primitives are simpler
and let `_gate_inbound` own the `InboundContext` construction internally.

**Why `is_bot` IS a parameter of `_gate_inbound` (with a `False`
default).** Bot-self filtering is an author-shaped concern that needs
access to the discord.py object (`message.author.bot`, `user.bot`, the
raw event's local-cache lookup pattern in `_make_on_raw_poll_vote_handler`).
Per-handler bot-self drops happen BEFORE the gate runs (matching today's
`on_message` ordering), so by the time `_gate_inbound` is called,
non-message handlers have already discarded bots upstream and pass the
default `is_bot=False`. The `on_message` handler is the exception: its
existing `gate_message` call threads the real `is_bot` flag through to the
`allowed_bot_ids` branch in `access.py:116`, which is the regression guard
PR #158 / 2026-06-07 added (and which the
`test_on_message_allows_bots_in_allowed_bot_ids` test pins). To preserve
that semantics WITHOUT making the on_message call site reach around the
gate, `_gate_inbound` accepts `is_bot` as an optional keyword-only
parameter with a `False` default. The on_message factory passes its
locally-computed flag through; reaction / lifecycle / poll-vote factories
accept the default because they've already filtered bots author-side.

**Why `_channel_allowed` is deleted.** After the changes, every inbound
handler calls `_gate_inbound` instead of `_channel_allowed`. The helper
becomes dead code and its presence would invite future callers to bypass
the full gate. Deleting it makes the "use `_gate_inbound`" pattern the
only available path. The gate semantics `_channel_allowed` provided
(guild-channel allowlist check only) are a proper subset of what
`_gate_inbound` provides (full `gate_message` semantics: bot-block,
dm_policy, channel allowlist). The full gate is strictly stronger.

**Why swap `on_reaction_add` → `on_raw_reaction_add` here (folding in
#171).** Issue #171's spec already justifies the swap independently (DM
reactions, cache-evicted messages, parity with poll-vote and lifecycle
listeners). Doing both edits in one PR is cheaper than sequencing them:
the test rewrite from cached fakes to raw fakes happens once, the handler
factory is renamed once, the listener registration is touched once. The
issue text explicitly asks for the fold-in.

**Why NOT extend the swap to `on_message_edit` and `on_message_delete`.**
The codebase already wires the raw variants (`on_raw_message_edit`,
`on_raw_message_delete` via `_make_on_raw_message_lifecycle_handler`). The
issue body was authored against a stale mental model of the file. No swap
is needed for those two — only the gate insertion replacing `_channel_allowed`.

**Why poll-vote gating is included.** The issue text says "every `on_*`
event handler" but only enumerates message / reaction / edit / delete in
the test matrix. Poll-vote handlers are structurally identical
(`on_raw_poll_vote_add`, `on_raw_poll_vote_remove`) and have the same
leak: a vote on a poll in a non-allowlisted channel routes to the bus
today. Gating them here follows the "no path to forget" goal — leaving
them out would re-create the asymmetry the spec exists to eliminate. The
cost is one extra line in each handler and three extra tests.

**Conventions.** Inline gate call sites follow the
`_make_on_message_handler` ordering: author-side guards first, gate
second, side effects third. Tests use the `_start_endpoint(monkeypatch,
access_path=...)` pattern from test_endpoint_inbound.py and the
access.json shape from the existing channel-allowlist tests. Test naming
uses the existing `test_on_<event>_<scenario>` convention. The towncrier
fragment goes under
`packages/agent-core-discord/changelog.d/<issue>.<type>.md` per the
package's `towncrier.toml`.

## Sub-requests (topologically sorted)

1. In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`,
   add the new `_gate_inbound` method on `DiscordEndpoint`. Place it
   immediately above `_make_on_message_handler` (search for
   `def _make_on_message_handler`) so it lives next to the handlers it
   serves:

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

2. Replace the inline gate call in `_make_on_message_handler`. Find the
   block starting with `ctx = InboundContext(` and ending at the
   `if not gate_message(self._access, ctx): return` guard (approximately
   4-6 lines). Replace the entire `ctx = InboundContext(...)` and
   `if not gate_message(...)` block with:

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

   **REQUIRED follow-up edit.** The metadata dict later in the same
   handler has `"is_bot": ctx.is_bot,` — `ctx` no longer exists after
   this change. Update that line to `"is_bot": is_bot,`. Grep for
   `ctx.is_bot` in endpoint.py to find it; the surrounding context is:

   ```python
   metadata: dict[str, Any] = {
       "discord": {
           "channel_id": str(message.channel.id),
           "message_id": str(message.id),
           "guild_id": str(message.guild.id) if message.guild else "",
           "author_id": str(message.author.id),
           "author_display_name": getattr(message.author, "display_name", "") or "",
           "is_dm": is_dm,
           "is_bot": is_bot,  # was ctx.is_bot — ctx no longer exists.
       },
   }
   ```

   **`allowed_bot_ids` regression note.** Passing `is_bot=is_bot` keeps
   the `test_on_message_allows_bots_in_allowed_bot_ids` test green.
   Passing `is_bot=False` (or omitting it) would silently re-introduce
   the 2026-06-07 bug from PR #158. Do not omit this kwarg on the
   on_message call site.

3. Rename `_make_on_reaction_add_handler` →
   `_make_on_raw_reaction_add_handler`. Rewrite the inner coroutine to
   accept a single `raw: Any` parameter (per discord.py
   `RawReactionActionEvent` shape). Mirror the bot-self filter from
   `_make_on_raw_poll_vote_handler` (lookup by user id against
   `self._client.user.id`); preserve the ack-emoji drop with
   `str(raw.emoji)`; preserve the local-cache lookup pattern for
   opportunistic other-bot filtering (use `self._client.get_user(...)`
   with `getattr(user, "bot", False)`). After the author-side drops, call:

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
   helper — same pattern poll-vote uses to maintain parity with cached
   events.

   Full new handler shape:

   ```python
   def _make_on_raw_reaction_add_handler(self):
       async def on_raw_reaction_add(raw: Any) -> None:
           # 1. Drop the bot's own reactions.
           self_user = self._client.user if self._client else None
           self_id = getattr(self_user, "id", None) if self_user is not None else None
           if self_id is not None and str(raw.user_id) == str(self_id):
               return

           # 2. Drop the ack emoji.
           ack_emoji = self._access.ack_reaction
           if ack_emoji and str(raw.emoji) == ack_emoji:
               return

           # 3. Opportunistic other-bot filter (synchronous cache lookup only).
           user = self._client.get_user(int(raw.user_id)) if self._client else None
           if user is not None and getattr(user, "bot", False):
               return

           # 4. Channel allowlist + DM policy gate.
           if not self._gate_inbound(
               event_kind="reaction_add",
               channel_id=raw.channel_id,
               author_id=raw.user_id,
               is_dm=raw.guild_id is None,
           ):
               return

           # 5. Build the Event envelope.
           user_display_name = await self._resolve_user_display_name(int(raw.user_id))
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

4. Update listener wiring. Find the line:

   ```python
   self._add_listener(self._make_on_reaction_add_handler(), "on_reaction_add")
   ```

   and replace with:

   ```python
   self._add_listener(
       self._make_on_raw_reaction_add_handler(), "on_raw_reaction_add"
   )
   ```

5. In `_make_on_raw_message_lifecycle_handler`, replace the existing
   `self._channel_allowed(str(raw.channel_id))` guard at the top of the
   inner coroutine with `_gate_inbound`:

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
           "channel_id": str(raw.channel_id),
           "guild_id": str(raw.guild_id) if raw.guild_id else "",
       }
       ...
   ```

6. In `_make_on_raw_poll_vote_handler`, replace the existing
   `self._channel_allowed(str(raw.channel_id))` guard with `_gate_inbound`,
   placed AFTER the existing bot-self drop and BEFORE the
   `_resolve_user_display_name` call (so the gate short-circuits the
   HTTP fetch for a denied vote):

   ```python
   if self_id is not None and str(getattr(raw, "user_id", "")) == str(self_id):
       return

   if not self._gate_inbound(
       event_kind=event_type,  # "discord.poll_vote_add" or "discord.poll_vote_remove"
       channel_id=raw.channel_id,
       author_id=raw.user_id,
       is_dm=raw.guild_id is None,
   ):
       return

   user_display_name = await self._resolve_user_display_name(int(raw.user_id))
   ```

7. Delete the `_channel_allowed` method from `DiscordEndpoint`. It is now
   dead code — all callers have been updated to use `_gate_inbound`. Grep
   for `_channel_allowed` in `endpoint.py` to confirm no references
   remain before deleting. The method signature to delete:

   ```python
   def _channel_allowed(self, channel_id: str) -> bool:
       ...
   ```

8. In `packages/agent-core-discord/tests/test_endpoint_inbound.py`,
   add `FakePartialEmoji` and `FakeRawReaction` classes near the existing
   fake event classes (after `FakeReaction` or near `FakeRawMessageDelete`):

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

9. Rewrite the five existing reaction tests. For each:
   - Rename `on_reaction_add` to `on_raw_reaction_add` in the
     `fake.fire(...)` call.
   - Replace `FakeReaction(emoji="👍", message=msg)` and the trailing
     `user` argument with a single `FakeRawReaction(...)` carrying the
     same emoji/channel/user/guild ids.
   - For `_drops_other_bots`, register the bot user via
     `fake.add_user(FakeUser(id="999", name="other-bot", bot=True))`.
   - For `_publishes_event_envelope` and `_dm_context`, add
     `fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))`
     before the `fake.fire(...)` call so the synchronous cache hit works.
   - The envelope-shape assertions stay the same (kind=Event, type
     `discord.reaction_add`, six-field payload data).

10. Add the new regression tests in `test_endpoint_inbound.py` after the
    rewritten reaction tests. Follow the `_start_endpoint(monkeypatch,
    access_path=...)` pattern. Each test writes a temp `access.json` via
    `tmp_path`, starts the endpoint, fires the handler, and asserts on
    `handle.published`.

    Required new tests:
    - `test_on_raw_reaction_add_dropped_when_channel_not_in_allowlist`
    - `test_on_raw_reaction_add_publishes_when_channel_in_allowlist`
      (include `fake.add_user(...)` for display name)
    - `test_on_raw_reaction_add_dm_follows_dm_policy_deny`
    - `test_on_raw_message_edit_dropped_when_channel_not_in_allowlist`
    - `test_on_raw_message_delete_dropped_when_channel_not_in_allowlist`
    - `test_on_raw_message_edit_dm_follows_dm_policy_deny`
    - `test_on_raw_poll_vote_add_dropped_when_channel_not_in_allowlist`
    - `test_on_raw_reaction_add_drops_bot_self_reactions_before_gate`
      (asymmetry-coverage: permissive access config, bot-self raw
      reaction, assert no publish)

    Access config shape to use (mirrors existing tests):
    ```json
    {"dmPolicy": "open", "channels": {"200": {}}}
    ```

11. Update `tests/test_endpoint_hardening.py::test_handlers_registered_via_add_listener`.
    Change:
    ```python
    assert "on_reaction_add" in fake._handlers
    ```
    to:
    ```python
    assert "on_raw_reaction_add" in fake._handlers
    ```
    Extend the inline comment block to mention the reaction-swap reason
    ("on_raw_reaction_add so DM reactions and cache-miss reactions reach
    the bus — discord.py omits message.guild on DM reactions in the
    cached dispatcher").

12. Create `packages/agent-core-discord/changelog.d/188.changed.md`
    with content:
    ```
    All Discord inbound event handlers now route through a single
    channel-allowlist + dm_policy gate (``DiscordEndpoint._gate_inbound``).
    Reactions, message edits, message deletes, and poll votes in
    non-allowlisted channels are now silently dropped — matching
    ``on_message`` semantics. Closes the leaks tracked in #170, #171, #180.
    Operators with strict channel allowlists may see fewer Event
    envelopes; this is the intended behaviour. (#188)
    ```

13. Run from the repo root:
    ```bash
    uv run pytest packages/agent-core-discord/tests -v
    just check
    ```
    Confirm zero failures and zero lint/typecheck errors. Check explicitly
    that `_channel_allowed` and `on_reaction_add` no longer appear in
    `endpoint.py`:
    ```bash
    grep -n "_channel_allowed\|on_reaction_add" \
      packages/agent-core-discord/src/agent_core_discord/endpoint.py
    ```
    Expected: no matches.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | (1) Add `_gate_inbound(*, event_kind, channel_id, author_id=None, is_dm=False, is_bot=False) -> bool` method on `DiscordEndpoint`. (2) Replace inline `gate_message`+`InboundContext` block in `_make_on_message_handler` with `_gate_inbound`; update `"is_bot": ctx.is_bot` → `"is_bot": is_bot` in the metadata dict. (3) Rename `_make_on_reaction_add_handler` → `_make_on_raw_reaction_add_handler`; rewrite inner coroutine to accept `raw: Any`; mirror raw-event bot-filter pattern from `_make_on_raw_poll_vote_handler`; insert `_gate_inbound` call between author-side filters and envelope build. (4) Replace `_channel_allowed` call in `_make_on_raw_message_lifecycle_handler` with `_gate_inbound`. (5) Replace `_channel_allowed` call in `_make_on_raw_poll_vote_handler` with `_gate_inbound`, placed after bot-self drop, before `_resolve_user_display_name`. (6) Listener wiring renames `"on_reaction_add"` → `"on_raw_reaction_add"`. (7) **Delete** `_channel_allowed` method (dead code after all callers switch to `_gate_inbound`). No new imports — `gate_message` and `InboundContext` are already imported. |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | (1) Add `FakePartialEmoji` and `FakeRawReaction` classes. (2) Rewrite five existing `test_on_reaction_add_*` tests to fire `on_raw_reaction_add` with the new raw fake shape. (3) Add eight new regression tests (channel-deny, channel-allow, dm-policy-deny, and bot-self-drop coverage for reaction, edit, delete, poll_vote). (4) The existing on_message gate tests are unchanged. |
| `packages/agent-core-discord/tests/test_endpoint_hardening.py` | Update `test_handlers_registered_via_add_listener` to assert `"on_raw_reaction_add"` instead of `"on_reaction_add"`; extend the inline comment. |
| `packages/agent-core-discord/changelog.d/188.changed.md` | New file. One-paragraph towncrier `changed` entry. |

## Alternatives considered

- **Land #170, #171, #180 separately as originally specced.** Would
  produce three impl PRs over a week, each touching the same handler
  factories and test file. The fold-in proposed here ships one PR with
  one rewrite of the reaction tests (cached → raw) and one new
  gate-call pattern reused four times. The fold-in also closes the
  structural gap (no path to forget the gate on the NEXT handler) that
  the three sub-specs leave open. Rejected: the cumulative diff is
  smaller and the structural fix matters.
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
- **Keep `_channel_allowed` and delegate `_gate_inbound` to it.** Retains
  the existing partial-gate helper as an implementation detail of
  `_gate_inbound`. Rejected — this would leave two gate abstractions in
  the class and would NOT fix the DM-policy gap in the non-message
  handlers (since `_channel_allowed` only checks the channel dict, not
  `dm_policy`). The full `gate_message` semantics are required.
- **Replace the existing per-handler bot drops with a centralized
  bot-filter helper too.** Tempting (same DRY argument), but bot
  detection differs per event source: `message.author.bot` for cached
  messages, `user.bot` for cached reactions, opportunistic local-cache
  lookup for raw events that only carry IDs. The asymmetry is real
  (edits/deletes don't have authors at all). Out of scope here; file a
  follow-up if the asymmetry causes pain.
- **Do nothing — close #188 and let #170/#171/#180 land separately.**
  Rejected: the issue explicitly cites three patches across three PRs
  as evidence that the per-handler approach is structurally wrong. The
  centralized gate is the fix the codebase has been asking for.

## Open questions

- **Coordination with the in-flight specs #170, #171, #180.** Their
  spec PRs are merged on this branch but no impl PRs have landed. After
  #188's impl PR merges, the daemon will close #170, #171, #180 as
  superseded. The Reviewer on the impl PR should confirm the impl PR's
  commit message references all three issue numbers so GitHub's
  cross-reference graph stays clean. (Foreman's `merge_impl_pr` action
  handles the issue-close routing — the `pr_body` does NOT use closing
  keywords. The closes happen automatically when the impl PR's lifecycle
  advances past the reviewer gate.)
- **Should `is_bot` also gain a default in `gate_message` itself?**
  Currently `InboundContext.is_bot` is non-optional (access.py:45),
  forcing every caller to think about it. The spec's
  `_gate_inbound(is_bot=False)` default papers over this. Tightening
  `gate_message` is out of scope — flagged for a follow-up if the
  pattern repeats elsewhere.

## Out of scope

- Configurable per-event-type gates (e.g., "allow edits but deny
  reactions on this channel"). The channel allowlist applies uniformly
  across event types. If finer-grained control becomes needed, file a
  follow-up.
- Bot-self filtering for edits/deletes (issue Out of scope: explicit).
- Allowlist editing UI/API (issue Out of scope: explicit).
- Refactoring `access.py` to expose a thinner `gate_channel(...)`
  function. `gate_message` already only consumes `InboundContext` fields
  and is the right seam.
- Touching outbound tool dispatch or any `_deliver_*` paths.
- Closing #170/#171/#180 manually — Foreman's lifecycle handles it via
  `merge_impl_pr` after the impl PR lands.
- Adding tests for `on_ready`, `on_disconnect`, or other non-inbound-event
  lifecycle hooks. They don't carry channel-scoped data and the gate
  concept doesn't apply.
- A migration note about the `on_reaction_add` → `on_raw_reaction_add`
  swap in any operator-facing docs. The package's CHANGELOG entry (via
  towncrier) is the source of truth; no additional doc needed.
