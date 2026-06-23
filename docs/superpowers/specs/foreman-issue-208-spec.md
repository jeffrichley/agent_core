# Spec: channel-allowlist gate for meta-event handlers (issue #208)

## Goal

Apply the existing channel-allowlist gate — already enforced on `on_message` — to the three other inbound handler factories in `DiscordEndpoint`: `_make_on_reaction_add_handler`, `_make_on_raw_message_lifecycle_handler`, and `_make_on_raw_poll_vote_handler`. After this change, `discord.reaction_add`, `discord.message_edit`, `discord.message_delete`, and `discord.poll_vote_*` events from non-allowlisted channels are silently dropped before publishing to the bus. See [issue #208](https://github.com/jeffrichley/agent_core/issues/208).

## Acceptance criteria

- `discord.reaction_add` events from a channel NOT in `cfg.channels` are dropped (not published to the bus).
- `discord.message_edit` events from a non-allowlisted channel are dropped.
- `discord.message_delete` events from a non-allowlisted channel are dropped.
- `discord.poll_vote_add` (and `discord.poll_vote_remove`) events from a non-allowlisted channel are dropped.
- The same four event types from an allowlisted channel still flow through unchanged.
- An empty `channels` dict continues to allow all channels (unchanged allow-all semantics, matching `gate_message()` behavior).
- A `_channel_allowed(channel_id: str) -> bool` method on `DiscordEndpoint` encapsulates the one shared rule so the four handlers don't each repeat it inline.
- Tests exist per handler (reaction, edit, delete, poll-vote) asserting non-allowlisted-channel events are dropped.
- Tests exist confirming allowlisted-channel events still publish.
- `just check` exits zero (ruff + mypy + pytest with 85% coverage gate).

## Approach

**Pattern naming.** No GoF pattern applies here — this is a straightforward bug fix: apply an existing guard to three call sites that were accidentally missed. The right design idiom is **DRY via extraction**: the one channel-gate rule lives in `_channel_allowed()`, and every inbound handler calls it rather than duplicating the two-line conditional.

**Where the rule lives.** `access.py:gate_message()` already encodes the channel-gate logic for full messages:

```python
if not cfg.channels:
    return True
return ctx.channel_id in cfg.channels
```

`gate_message()` is not reused here because it also evaluates DM policy, bot-block, and author identity — none of which are available or meaningful for the meta-event handlers. A dedicated `_channel_allowed(channel_id: str) -> bool` method on `DiscordEndpoint` is the right extraction: it reads `self._access.channels` and encodes exactly the guild-channel branch rule, nothing else.

**Where to add the guard in each handler.** Each handler already computes `channel_id` as part of building the `data` dict:

- `on_reaction_add` (line 1138): `channel_id = str(message.channel.id)` is built into `data` at line 1153. The guard goes after the ack-emoji drop (step 2), before the `data` dict is constructed (step 3).
- `on_raw_message_lifecycle` (line 1271): `channel_id = str(raw.channel_id)` is built into `data` immediately. The guard goes at the top of the function, before any dict construction.
- `on_raw_poll_vote` (line 1220): `channel_id = str(raw.channel_id)` is built into `data` at line 1240. The guard goes after the self-vote drop, before the user-display-name resolution (which does an async HTTP fetch on cache miss — skipping that fetch for blocked events is an extra correctness win).

**No changes to `access.py`.** The `gate_message()` function and `AccessConfig` are untouched; the new helper is purely on `DiscordEndpoint` where `self._access` lives.

**File layout.** All changes are in two files: the production handler (`endpoint.py`) and the inbound test file (`test_endpoint_inbound.py`). No new files.

## Sub-requests (topologically sorted)

1. **Add `_channel_allowed()` helper to `DiscordEndpoint` in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`.**

   Insert as a new method on `DiscordEndpoint` immediately before `_make_on_reaction_add_handler` (currently at line 1138). Place it at the same indentation level as the other `def _make_*` methods.

   ```python
   def _channel_allowed(self, channel_id: str) -> bool:
       """Return True if channel_id passes the configured channel allowlist.

       Mirrors the guild-channel branch of ``gate_message()`` in access.py:
         - Empty ``channels`` dict → allow all (unchanged allow-all default).
         - Non-empty → allow only if ``channel_id`` is an explicit key.

       Used by meta-event handlers (reaction, message lifecycle, poll vote)
       that share the same channel gate but do not go through the full
       ``gate_message()`` path (which also evaluates DM policy, bot-block,
       and author identity — not applicable to these events).
       """
       if not self._access.channels:
           return True
       return channel_id in self._access.channels
   ```

2. **Gate `_make_on_reaction_add_handler` in `endpoint.py`.**

   In `on_reaction_add`, after step 2 (ack-emoji drop, currently ending with `return`) and before the `data` dict is constructed, insert the new step 3 block below. **Important:** `message = reaction.message` is currently the first line of the existing step 3 (line 1150). Extract it out of that step and place it as the first line of the new guard block so that `message` is in scope when `message.channel.id` is read:

   ```python
   # 3. Channel allowlist gate — same rule as on_message.
   message = reaction.message          # extracted from former step 3
   channel_id_str = str(message.channel.id)
   if not self._channel_allowed(channel_id_str):
       return
   ```

   Update the subsequent `data` dict (renumbered to step 4) to reuse `channel_id_str` instead of re-computing `str(message.channel.id)`, and to use the already-assigned `message` variable:

   ```python
   # 4. Build the Event envelope.
   data: dict[str, Any] = {
       "emoji": str(reaction.emoji),
       "channel_id": channel_id_str,
       "message_id": str(message.id),
       "guild_id": str(message.guild.id) if message.guild else "",
       "user_id": str(user.id),
       "user_display_name": getattr(user, "display_name", "") or "",
   }
   ```

   Renumber all remaining comment blocks that were "3. Build…" / "3. …" to "4. …" (single comment update, cosmetic). Remove the now-redundant `message = reaction.message` line that was previously the first line of what was step 3, since it is now captured in the guard block above.

3. **Gate `_make_on_raw_poll_vote_handler` in `endpoint.py`.**

   In `on_raw_poll_vote`, after the self-vote drop (currently ending with `return`) and before the `_resolve_user_display_name` call, add:

   ```python
   # Channel allowlist gate — same rule as on_message.
   if not self._channel_allowed(str(raw.channel_id)):
       return
   ```

   This positions the gate before the async `fetch_user` HTTP call, so non-allowlisted votes don't incur an unnecessary network round-trip.

4. **Gate `_make_on_raw_message_lifecycle_handler` in `endpoint.py`.**

   In `on_raw_message_lifecycle`, add at the very top of the function body (before the `data` dict is constructed):

   ```python
   # Channel allowlist gate — same rule as on_message.
   if not self._channel_allowed(str(raw.channel_id)):
       return
   ```

5. **Add channel-allowlist tests for meta-event handlers in `packages/agent-core-discord/tests/test_endpoint_inbound.py`.**

   Append a new test section after the existing engagement-event tests. The `_start_endpoint`, `FakeReaction`, `FakeRawPollVote`, `FakeRawMessageDelete`, and `FakeRawMessageUpdate` helpers are all already defined in this file — reuse them exactly.

   ```python
   # --- Channel-allowlist gate for meta-event handlers (issue #208) ---
   # Regression guard: reaction/edit/delete/poll-vote events from non-allowlisted
   # channels must be dropped (not published). Observed leaking from #pepper-chat
   # on 2026-06-22/23 despite message content being correctly blocked.

   @pytest.mark.asyncio
   async def test_on_reaction_add_drops_non_allowlisted_channel(monkeypatch, tmp_path):
       """Reaction from a guild channel NOT in the channels allowlist is dropped."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       fake.add_channel(FakeChannel(id="999"))  # non-allowlisted
       msg = FakeMessage(id="m", channel_id="999", content="")
       msg.author = FakeUser(id="100")
       msg.guild = type("G", (), {"id": "g"})()
       msg.channel = fake.get_channel("999")
       user = FakeUser(id="100")
       reaction = FakeReaction(emoji="👍", message=msg)
       try:
           await fake.fire("on_reaction_add", reaction, user)
           assert handle.published == []
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_reaction_add_passes_allowlisted_channel(monkeypatch, tmp_path):
       """Reaction from an allowlisted channel still publishes."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       fake.add_channel(FakeChannel(id="200"))
       msg = FakeMessage(id="m", channel_id="200", content="")
       msg.author = FakeUser(id="100")
       msg.guild = type("G", (), {"id": "g"})()
       msg.channel = fake.get_channel("200")
       user = FakeUser(id="100")
       reaction = FakeReaction(emoji="👍", message=msg)
       try:
           await fake.fire("on_reaction_add", reaction, user)
           assert len(handle.published) == 1
           assert handle.published[0].payload.type == "discord.reaction_add"
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_raw_message_edit_drops_non_allowlisted_channel(monkeypatch, tmp_path):
       """message_edit from a non-allowlisted channel is dropped."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       raw = FakeRawMessageUpdate(message_id=1, channel_id=999, guild_id=1000)
       try:
           await fake.fire("on_raw_message_edit", raw)
           assert handle.published == []
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_raw_message_edit_passes_allowlisted_channel(monkeypatch, tmp_path):
       """message_edit from an allowlisted channel still publishes."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       raw = FakeRawMessageUpdate(message_id=1, channel_id=200, guild_id=1000)
       try:
           await fake.fire("on_raw_message_edit", raw)
           assert len(handle.published) == 1
           assert handle.published[0].payload.type == "discord.message_edit"
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_raw_message_delete_drops_non_allowlisted_channel(monkeypatch, tmp_path):
       """message_delete from a non-allowlisted channel is dropped."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       raw = FakeRawMessageDelete(message_id=1, channel_id=999, guild_id=1000)
       try:
           await fake.fire("on_raw_message_delete", raw)
           assert handle.published == []
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_raw_message_delete_passes_allowlisted_channel(monkeypatch, tmp_path):
       """message_delete from an allowlisted channel still publishes."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       raw = FakeRawMessageDelete(message_id=1, channel_id=200, guild_id=1000)
       try:
           await fake.fire("on_raw_message_delete", raw)
           assert len(handle.published) == 1
           assert handle.published[0].payload.type == "discord.message_delete"
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_raw_poll_vote_add_drops_non_allowlisted_channel(monkeypatch, tmp_path):
       """poll_vote_add from a non-allowlisted channel is dropped."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       raw = FakeRawPollVote(
           message_id=1, channel_id=999, user_id=100, guild_id=1000, answer_id=1
       )
       try:
           await fake.fire("on_raw_poll_vote_add", raw)
           assert handle.published == []
       finally:
           await ep.stop()


   @pytest.mark.asyncio
   async def test_on_raw_poll_vote_add_passes_allowlisted_channel(monkeypatch, tmp_path):
       """poll_vote_add from an allowlisted channel still publishes."""
       import json
       access = tmp_path / "access.json"
       access.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
       ep, handle, fake = await _start_endpoint(monkeypatch, access_path=str(access))
       fake.add_user(FakeUser(id="100", name="alice", display_name="Alice"))
       raw = FakeRawPollVote(
           message_id=1, channel_id=200, user_id=100, guild_id=1000, answer_id=1
       )
       try:
           await fake.fire("on_raw_poll_vote_add", raw)
           assert len(handle.published) == 1
           assert handle.published[0].payload.type == "discord.poll_vote_add"
       finally:
           await ep.stop()
   ```

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | Add `DiscordEndpoint._channel_allowed(channel_id: str) -> bool` method. Add early-return guard to `on_reaction_add` (step 3), `on_raw_poll_vote` (after self-vote drop), and `on_raw_message_lifecycle` (top of function). |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | Append 8 new tests covering non-allowlisted-drop and allowlisted-pass for each handler type (reaction, message_edit, message_delete, poll_vote_add). |

## Alternatives considered

- **Reuse `gate_message()` directly in the meta-event handlers**: Rejected. `gate_message()` requires an `InboundContext` with `is_dm`, `author_id`, and `is_bot` — all unavailable for raw lifecycle and poll-vote events. Constructing a synthetic context with dummy values risks silently wrong behavior (e.g., if the bot-block branch fires unexpectedly). A dedicated helper encoding only the channel-gate rule is clearer and safer.
- **Add a dedicated `gate_meta_event()` function to `access.py`**: Considered. The channel check is two lines (`if not cfg.channels: return True; return channel_id in cfg.channels`). A separate public function in `access.py` adds a new API surface for a minimal extraction. Keeping it as a private method on `DiscordEndpoint` (where `self._access` lives) avoids leaking a half-formed abstraction into the public access module. The issue author's suggestion of a small `_channel_allowed` helper aligns with this choice.
- **Fix only the `on_reaction_add` handler** (the explicitly observed leak): Rejected. The issue identifies all three handler factories as having the same flaw, and the meta-event test coverage for all three is equally absent. A partial fix would leave documented known bugs.

## Open questions

None. The root cause, affected call sites, and desired fix are all unambiguous from the issue and the code.

## Out of scope

- Outbound direction (wren's own reactions routing to the bus) — explicitly deferred by the issue.
- DM-policy enforcement for meta-events (reactions in DMs, etc.) — explicitly deferred by the issue as "DM-policy handling (unchanged)."
- `on_raw_poll_vote_remove`: the same factory as `on_raw_poll_vote_add` is modified; sub-request 3 fixes both with one edit. A separate drop-test for `poll_vote_remove` is not added since the factory is identical code.
- Changes to `access.py`, `AccessConfig`, or `gate_message()`.
- Any change to how the `on_message` path handles the channel gate — it already works correctly.
