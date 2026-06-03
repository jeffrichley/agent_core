# Spec: Discord adapter `allowedBotIds` opt-in allowlist (issue #143)

## Goal

Lift the hardcoded "block every bot author" behaviour in
`agent_core_discord.access.gate_message` and replace it with a default-deny,
opt-in allowlist keyed by Discord bot id. Default behaviour is bit-for-bit
identical to today (no `allowedBotIds` → every bot blocked). Surfaced
2026-06-03 in the multi-being Discord workspace so Pepper and Wren can hear
each other in shared guild channels without the bus-mediated dual-emit
workaround. See issue [#143](https://github.com/jeffrichley/agent_core/issues/143).

## Acceptance criteria

- `AccessConfig` in
  `packages/agent-core-discord/src/agent_core_discord/access.py` gains
  `allowed_bot_ids: list[str] = field(default_factory=list)`.
- `load_access_config()` reads JSON key `allowedBotIds` into that field.
  Missing → empty list. Non-list JSON value (e.g., a string) → empty list +
  one `log.warning` matching the existing fail-closed pattern for unknown
  `dmPolicy`.
- `gate_message()` replaces the `if ctx.is_bot: return False` short-circuit
  with `if ctx.is_bot: return ctx.author_id in cfg.allowed_bot_ids`. No
  fall-through to the DM / channel branches when `ctx.is_bot` is True
  (matches the snippet in the issue body).
- `DiscordEndpoint._make_on_message_handler` in
  `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:
  - Drops the `message.author.bot` half of the line-998 short-circuit so
    bot-authored messages reach the gate. The self-message guard
    (`message.author == self._client.user`) stays — never react to our own
    posts.
  - Sets `is_bot=bool(message.author.bot)` on the `InboundContext` at
    line 1007 instead of the hardcoded `False`, so the gate's new branch
    actually sees the flag.
- `_make_on_reaction_add_handler` keeps its existing other-bot reaction
  filter — `allowedBotIds` is scoped to inbound messages in v1 (reactions
  out of scope, see "Out of scope").
- Existing access.json files without `allowedBotIds` continue to load and
  behave identically to today; no migration script.
- Module docstring at the top of `access.py` mentions `allowedBotIds`
  alongside the existing `dmPolicy / allowFrom / channels / ackReaction`
  list. (There is no package README — verified via
  `find packages/agent-core-discord -name '*.md'` returns only
  CHANGELOG.md. No additional doc files in scope.)
- New `CHANGELOG.md` / towncrier news fragment for `agent-core-discord`
  describing the new field. The package uses towncrier (see
  `packages/agent-core-discord/towncrier.toml`); follow the existing
  fragment convention.
- `just check` (lint + typecheck + tests) exits zero.

## Approach

The change is small and localized. Two files in production code, one
test file, one news fragment.

**1. `access.py` — schema + loader + gate.** The existing
`AccessConfig` dataclass already uses `field(default_factory=list)`
for `allow_from`, so adding `allowed_bot_ids` follows the same shape.
The loader already has a precedent for fail-closed handling of bad
input — `_VALID_DM_POLICIES` falls back to `"deny"` with a
`log.warning` on unknown values (see lines 56-64). Reuse that idiom
for `allowedBotIds`: if `raw["allowedBotIds"]` is present and not a
list, log a warning and substitute an empty list. Silent coercion via
`list(raw.get(...))` is wrong here — `list("123")` returns
`["1","2","3"]`, which would silently mis-load a string-typed bot id
as three single-character allowlist entries.

The gate change matches the issue's example verbatim:

```python
if ctx.is_bot:
    return ctx.author_id in cfg.allowed_bot_ids
```

Allowlisted bots return early from the bot path. They do NOT then pass
through the DM / channel allowlist. This matches the snippet in the
issue body and the design intent ("we trust this specific other-being
to be heard, full stop"). The trade-off is documented under "Open
questions" so the reviewer can confirm intent before the Worker runs.

**2. `endpoint.py` — unblock the path to the gate.** This is the
non-obvious half of the change. Today
`_make_on_message_handler` (line 998) reads:

```python
if message.author == self._client.user or message.author.bot:
    return
```

This short-circuits BEFORE `gate_message` is called, so the access
gate never sees any bot message. The fix:

```python
if message.author == self._client.user:
    return
```

The self-guard stays (it prevents the bot from reacting to its own
posts — separate concern from the access gate, and removing it would
break `test_on_message_drops_messages_from_self`). The
`message.author.bot` half is now the gate's job.

Two lines below, `is_bot=False` (line 1007) must become
`is_bot=bool(message.author.bot)` so the new gate branch actually
receives the flag. Without both edits the schema change is dead code.

The `discord.Member.bot` attribute is a stable property of
`discord.py` and is already read at line 1667 for envelope metadata,
so the integration is bus-clean.

**3. Tests.** `tests/test_access.py` already covers gate semantics
with a `_ctx()` helper at line 62 and an explicit
`test_gate_blocks_bot_authors_unconditionally` at line 66. The
unconditional-block test gets re-cast as a default-empty-allowlist
test. Add the six new tests enumerated under the issue's Tests
section.

`tests/test_endpoint_inbound.py` has
`test_on_message_drops_messages_from_bots` at line 79. That test
currently passes because of the endpoint-level short-circuit; under
the new behaviour, with an empty `allowedBotIds`, the gate also drops
the message — the test should still pass. Add one new
endpoint-integration test that loads an access.json with an
allowlisted bot id and asserts the bot's message is published.

**Why these particular conventions.** camelCase in JSON (`allowedBotIds`)
matches the existing keys (`dmPolicy`, `allowFrom`, `ackReaction`).
snake_case in Python (`allowed_bot_ids`) matches the existing field
names. Empty-list default and fail-closed coercion match the existing
loader's posture (see `_VALID_DM_POLICIES` block). Spelling out
"allowedBotIds" rather than reusing `allowFrom` keeps person and bot
gating cleanly separated (the issue calls this out explicitly).

## Sub-requests (topologically sorted)

1. Update `AccessConfig` in
   `packages/agent-core-discord/src/agent_core_discord/access.py`: add
   `allowed_bot_ids: list[str] = field(default_factory=list)`.
2. In the same file, update `load_access_config()` to read
   `raw.get("allowedBotIds", [])`. If the value is present and not a
   `list`, fall back to `[]` and `log.warning(...)` — mirror the
   `_VALID_DM_POLICIES` fail-closed idiom (lines 56-64).
3. In the same file, update `gate_message()`: replace
   `if ctx.is_bot: return False` (lines 84-85) with
   `if ctx.is_bot: return ctx.author_id in cfg.allowed_bot_ids`.
4. Update the module docstring at the top of `access.py` to list
   `allowedBotIds` alongside the other JSON fields.
5. In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`
   `_make_on_message_handler` (around lines 995-1010):
   - Drop `or message.author.bot` from the line-998 self-and-bot
     short-circuit. The self-guard stays.
   - Change `is_bot=False` (line 1007) to
     `is_bot=bool(message.author.bot)`.
6. Update `packages/agent-core-discord/tests/test_access.py`:
   - Re-cast `test_gate_blocks_bot_authors_unconditionally` (line 66)
     as `test_gate_message_bot_default_denied` with the same assertion
     but new framing ("empty allowedBotIds blocks all bots").
   - Add `test_gate_message_bot_in_allowlist_passes`: `is_bot=True`,
     `author_id="123"`, `AccessConfig(allowed_bot_ids=["123"])` →
     True.
   - Add `test_gate_message_bot_not_in_allowlist_blocked`:
     `is_bot=True`, `author_id="999"`,
     `AccessConfig(allowed_bot_ids=["123"])` → False.
   - Add `test_load_access_config_reads_allowed_bot_ids`: JSON with
     `{"allowedBotIds": ["123", "456"]}` round-trips into
     `cfg.allowed_bot_ids == ["123", "456"]`.
   - Add `test_load_access_config_missing_allowed_bot_ids_defaults_empty`:
     JSON without the field → `cfg.allowed_bot_ids == []` (list, not
     None).
   - Add
     `test_load_access_config_invalid_allowed_bot_ids_falls_back`:
     JSON with `{"allowedBotIds": "not-a-list"}` →
     `cfg.allowed_bot_ids == []` and a `log.warning` is emitted (use
     `caplog`, mirror `test_load_access_config_unknown_dm_policy_falls_back_to_deny`).
7. Add an integration test in
   `packages/agent-core-discord/tests/test_endpoint_inbound.py`
   alongside `test_on_message_respects_access_gate_dm_deny`:
   - `test_on_message_publishes_allowlisted_bot`: write an access.json
     containing `{"allowedBotIds": ["999"]}`, fire `on_message` with
     `_msg(is_bot=True, author_id="999")`, assert one envelope is
     published.
   - Keep `test_on_message_drops_messages_from_bots` (line 79); with
     no `allowedBotIds` configured the message is still dropped — same
     end result, the test continues to assert the safe default.
   - Keep `test_on_message_drops_messages_from_self` (line 92);
     unchanged because the self-guard in step 5 is preserved.
8. Add a towncrier news fragment under
   `packages/agent-core-discord/` following the existing fragment
   convention in `towncrier.toml`. One-line `feat`-style entry naming
   the new field.
9. Run `just check` and confirm it exits zero. Fix anything that
   surfaces.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/access.py` | Add `allowed_bot_ids` field to `AccessConfig`; loader reads `allowedBotIds` with fail-closed list-type guard; `gate_message` replaces unconditional-bot-block with allowlist check; module docstring updated. |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | `_make_on_message_handler`: drop `message.author.bot` from the self-and-bot short-circuit, set `is_bot=bool(message.author.bot)` on the inbound context. |
| `packages/agent-core-discord/tests/test_access.py` | Re-cast one existing test, add five new tests covering the new field and gate semantics. |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | Add one integration test verifying an allowlisted bot's message is published end-to-end. |
| `packages/agent-core-discord/<news fragment>` | Towncrier `feat` entry for the new `allowedBotIds` field. |

## Alternatives considered

- **Treat allowlisted bots like persons (still subject to DM / channel
  allowlist after the `is_bot` check).** Rejected because the issue's
  example code snippet for the gate returns early from the bot path
  (`return ctx.author_id in cfg.allowed_bot_ids`). Surfaced as an
  Open Question in case the reviewer wants to change the design.
- **Overload `allowFrom` to accept both human and bot ids.** Rejected
  because the issue explicitly calls out keeping person and bot
  gating separate, and because operators would have no way to express
  "DM allowlist for humans but block this bot" without two fields.
- **Boolean `allowOtherBots: true` flag (coarse on/off).** Rejected
  because it can't distinguish Pepper from any random bot that joins
  the guild — the issue is explicit about per-id opt-in.
- **Wildcards / glob patterns in `allowedBotIds`** (e.g.,
  `"1480938766246871050*"`). Rejected by the issue's Out-of-scope
  list; trivial to add later without breaking compatibility.
- **Auto-detect sibling agent-core bots in the daemon process.**
  Rejected by the issue's Out-of-scope list; less explicit than a
  config-named opt-in.
- **Do nothing; keep using the bus-mediated dual-emit workaround.**
  Rejected because the workaround is friction (each being must
  remember to dual-emit) and a dedup burden, and the underlying
  hardcode is the actual defect.

## Open questions

- The issue's snippet has the gate return early from the bot path
  (`if ctx.is_bot: return ctx.author_id in cfg.allowed_bot_ids`).
  This means an allowlisted bot bypasses any channel allowlist — e.g.,
  Pepper would pass even if she posted in a guild channel not listed
  in `channels`. The plan follows the issue verbatim, but the
  Reviewer should confirm this is intentional. The alternative is two
  lines instead of one (allowlist check, then fall through to the
  channel block), at the cost of complicating the bot path. Flagging
  here in case the reviewer wants to change the design before the
  Worker runs.

## Out of scope

- A per-person allowlist gating human authors by id (issue says
  separate ticket).
- Wildcards or pattern matching in `allowedBotIds` (issue says
  exact-id-only for v1).
- Auto-detecting sibling agent-core bots in the same daemon process
  (issue says explicit config is clearer).
- Applying `allowedBotIds` to inbound reactions
  (`_make_on_reaction_add_handler`). The current other-bot reaction
  filter stays; v1 is messages-only. Re-evaluate when a named symptom
  appears (e.g., wanting Pepper's 👍 on Wren's post to register as a
  signal).
- Changing or migrating the JSON file location
  (`~/.agent-core/discord-<being>-access.json`); already settled in
  the 2026-06-03 cutover referenced in the issue's Related section.
- Refactoring the loader to use Pydantic / a schema validator. The
  existing dataclass + manual coerce pattern is what the rest of the
  file uses; introducing a validator is a larger and orthogonal
  cleanup.
