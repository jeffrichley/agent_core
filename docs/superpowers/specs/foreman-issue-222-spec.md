# Spec: fix reply() silent Discord drop by stripping inbound-only metadata keys (issue #222)

## Goal

`reply()` in `ClaudeCodeMCPEndpoint` shallow-inherits the full inbound `metadata` dict, which for Discord inbounds includes receive-side-only fields (`guild_id`, `author_id`, `author_display_name`, `is_bot`, `is_dm`). The Discord adapter's shape validator rejects these fields on outbound TextMessages and responds with a yellow `Acknowledgment` instead of delivering the message, producing silent message loss. This spec fixes `reply()` to strip the inbound-only Discord fields before building the outbound envelope, and upgrades the adapter's Unrecognized-shape response from yellow to red urgency as a belt-and-suspenders safety net. See [issue #222](https://github.com/jeffrichley/agent_core/issues/222).

## Acceptance criteria

- `reply()` called with a Discord inbound that carries `guild_id`, `author_id`, `author_display_name`, `is_bot`, or `is_dm` in `metadata.discord` produces an outbound envelope whose `metadata.discord` contains none of those keys.
- `reply()` preserves `metadata.discord.channel_id` (and other outbound-valid keys like `embeds`, `message_id`, `reply_to`) unchanged.
- `reply()` preserves non-`discord` metadata keys (e.g. `trace_id`) unchanged — the fix must not broaden beyond the `discord` sub-dict.
- `reply()` with an explicit `metadata` override (the caller-supplied `metadata` arg) is unaffected: the override is applied after stripping, so a caller who passes `{"discord": {"channel_id": "X"}}` still wins.
- The Unrecognized-shape Acknowledgment published by the Discord adapter changes from `urgency="yellow"` to `urgency="red"` (the validator-exception branch at the top of `deliver()` stays yellow — different failure mode).
- `new_failures_count == 0` on `just check` after the change (all updated tests pass).

## Approach

**Pattern naming.** No GoF pattern fits. This is the **Sanitize-at-Boundary** idiom: strip transport-layer noise from the routing context at the earliest safe point (inside `reply()`, before the envelope is published), rather than requiring every downstream consumer to tolerate inbound-only metadata. The engineering principle is SRP: `reply()` owns building a well-formed outbound envelope; "well-formed" includes "free of inbound-only transport metadata that the outbound adapter will reject."

**Primary fix — `reply()` in `claude_code_mcp.py`.**  
After the shallow merge `out_metadata = {**inbound_metadata, **(metadata or {})}` (line 1060), check whether `out_metadata` contains a `"discord"` sub-dict. If it does, remove the five known inbound-only keys. The set is defined as a module-level constant `_DISCORD_INBOUND_ONLY_KEYS` to keep the stripping logic readable and to serve as a single update point if the inbound schema grows. This approach (remove-known-bad rather than allow-known-good) is preferred because it preserves future outbound keys that the adapter might add — the adapter's own `_KNOWN_DISCORD_META_OUTBOUND_KEYS` in `shape_validator.py` is the authoritative allow-list for the adapter's validation; `reply()` just needs to remove the specific fields it knows it received on the inbound side. There is no import dependency between `packages/core` and `packages/agent-core-discord` — the constant lives entirely in core.

The stripping is applied to the merged `out_metadata`, not to the raw `inbound_metadata`. This ordering means:
- A caller who passes `metadata={"discord": {"channel_id": "X"}}` (replacing the whole `discord` sub-dict) gets the override unadulterated — the override dict has no inbound-only keys, so stripping is a no-op.
- A caller who passes no `metadata` override gets the inherited `channel_id` (from inbound) with the inbound-only keys stripped.
- A caller who passes `metadata={"discord": {"channel_id": "Y", "guild_id": "Z"}}` (intentionally setting `guild_id` outbound) will have `guild_id` stripped. This is correct: the adapter has no routing for `guild_id` on outbound, so no meaningful override exists.

**Safety net — red urgency for Unrecognized shapes in `endpoint.py`.**  
The Discord adapter's `deliver()` (line 743 in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`) currently calls `self._reply(envelope, note, urgency="yellow")` for Unrecognized shapes. Yellow looks like a partial-success warning, which is what masked the bug. Changing it to `urgency="red"` makes these shape failures immediately visible in the agent's session (red envelopes wake the agent and land in the inbox with high urgency). The validator-exception branch at lines 710–716 (`f"validator failed: {exc!r}"`) stays yellow — that's a catalog bug in our own code, not a sender error, and doesn't warrant waking the agent.

**Existing test that encodes the broken behavior.**  
`test_reply_publishes_and_acks_atomically` in `packages/core/tests/test_claude_code_mcp_consume_reply.py` (line 222) currently asserts `out.metadata == {"discord": {"channel_id": "C1", "guild_id": "G1"}}`. With the fix, `guild_id` is stripped, so this assertion must be updated to `{"discord": {"channel_id": "C1"}}`. The test's inbound metadata setup (line 200) that includes `guild_id` in the inbound can stay — it's the correct scenario to test.

## Sub-requests (topologically sorted)

1. **Add `_DISCORD_INBOUND_ONLY_KEYS` constant and stripping logic to `reply()` in `packages/core/src/agent_core/endpoints/claude_code_mcp.py`.**  
   Insert after line 1060 (`out_metadata = {**inbound_metadata, **(metadata or {})}`):
   - Define module-level constant (near other module-level constants): `_DISCORD_INBOUND_ONLY_KEYS: frozenset[str] = frozenset({"author_display_name", "author_id", "guild_id", "is_bot", "is_dm"})`
   - After the merge: `discord_meta = out_metadata.get("discord")` / `if isinstance(discord_meta, dict) and (discord_meta.keys() & _DISCORD_INBOUND_ONLY_KEYS):` / `out_metadata = {**out_metadata, "discord": {k: v for k, v in discord_meta.items() if k not in _DISCORD_INBOUND_ONLY_KEYS}}`

2. **Update `test_reply_publishes_and_acks_atomically` in `packages/core/tests/test_claude_code_mcp_consume_reply.py` and add a new regression test.**  
   - Line 222: change `{"discord": {"channel_id": "C1", "guild_id": "G1"}}` → `{"discord": {"channel_id": "C1"}}`.
   - Add `test_reply_strips_discord_inbound_only_keys` that builds an inbound with all five inbound-only keys plus `channel_id` in `metadata.discord`, calls `reply()`, and asserts the outbound `metadata.discord` contains only `channel_id`.

3. **Change Unrecognized-shape ack urgency from `"yellow"` to `"red"` in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`.**  
   Line 743: `await self._reply(envelope, note, urgency="yellow")` → `urgency="red"`. No other changes to `deliver()`.

4. **Update two existing tests in `packages/agent-core-discord/tests/test_endpoint_outbound.py`.**  
   - `test_unrecognized_field_produces_failed_delivery_ack` (line 2561): `assert ack.urgency == "yellow"` → `"red"`. Also update the comment on line 2556 from "Yellow Ack" to "Red Ack".
   - `test_multi_field_unrecognized_produces_one_ack_listing_all_fields` (line 2774): `assert ack.urgency == "yellow"` → `"red"`.
   - Note: `test_validator_internal_exception_produces_yellow_ack` (line 2850) is unchanged — that branch stays yellow.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/endpoints/claude_code_mcp.py` | **Modify.** Add `_DISCORD_INBOUND_ONLY_KEYS` module-level constant. In `reply()`, strip inbound-only keys from `out_metadata["discord"]` after the shallow merge. |
| `packages/core/tests/test_claude_code_mcp_consume_reply.py` | **Modify.** Update line 222 assertion in `test_reply_publishes_and_acks_atomically`. Add `test_reply_strips_discord_inbound_only_keys`. |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify.** Line 743 only: `urgency="yellow"` → `urgency="red"` in the Unrecognized-shape branch of `deliver()`. |
| `packages/agent-core-discord/tests/test_endpoint_outbound.py` | **Modify.** Update `urgency` assertions in `test_unrecognized_field_produces_failed_delivery_ack` (line 2561) and `test_multi_field_unrecognized_produces_one_ack_listing_all_fields` (line 2774) from `"yellow"` to `"red"`. |

## Alternatives considered

- **Option 2 — Discord adapter tolerates inbound-only fields on outbound:** Would close the bug for `reply()` but is semantically wrong: the adapter has no routing for these fields, so tolerating them silently is a different kind of lie. It also wouldn't help if a bug elsewhere puts inbound-only metadata on an outbound. Rejected in favour of fixing the source.
- **Option 1b — filter by keeping only the known-good set (allow-list approach):** Instead of `_DISCORD_INBOUND_ONLY_KEYS` (remove-bad), keep only `{"channel_id", "embeds", "message_id", "reply_to"}` from `metadata.discord`. Rejected: this would silently drop any future outbound Discord keys that an agent legitimately passes (e.g. `components`, `allowed_mentions` — both in `_KNOWN_CANONICAL_SEND_ARGS` but not on the TextMessage path). The remove-bad approach is more conservative about what it strips.
- **Option 3 only (red urgency, no `reply()` fix):** The minimum-viable change from the issue. Rejected as the primary fix: it surfaces the failure loudly but doesn't prevent it. The ergonomic path (`reply()`) still breaks; callers must still hand-craft metadata. Both sub-fixes are included.
- **Do nothing, document the workaround:** Rejected. Two beings (Wren and Pepper) have already hit this; the issue explicitly flags it as a silent-loss path. The workaround (`send()` with hand-built metadata) defeats the purpose of `reply()`.

## Open questions

None. The fix location (`reply()` in `claude_code_mcp.py`), the set of fields to strip (the five named in the issue body plus confirmed in `_validate_text_message`'s comment in `shape_validator.py`), the urgency change (`yellow` → `red` for Unrecognized only), and the test updates are all unambiguous from the issue and the code.

## Out of scope

- Fixing `reply()` for non-Discord transport metadata (e.g. an `email` sub-dict with inbound-only fields) — no other transport has this problem today; YAGNI.
- Changing `_KNOWN_DISCORD_META_OUTBOUND_KEYS` in `shape_validator.py` — the validator's allow-list is correct; this PR does not alter the adapter's validation logic.
- Adding `is_dm`, `guild_id`, etc. as valid outbound routing fields — the Discord API has no send-time equivalent; these are purely receive-side.
- Auditing `send()` or other MCP tools — only `reply()` inherits inbound metadata wholesale.
- Changing what inbound-Discord envelopes carry — the inbound metadata schema is correct and intentional.
