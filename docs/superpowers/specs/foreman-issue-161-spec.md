# Spec: Strip Discord inbound-only enrichment from `reply()` outbound metadata and reinforce NACK coverage (issue #161)

## Goal

Close the silent-drop class on `reply()` against Discord-routed inbounds.
`mcp__agent-core__reply()` currently shallow-merges the inbound envelope's
`metadata.discord` block into the outbound, which carries inbound-only
adapter-enrichment fields (`guild_id`, `author_id`, `author_display_name`,
`is_dm`, `is_bot`) that the Discord adapter's strict-mode shape validator
(#114) rejects as `Unrecognized`. The result is the outbound never reaches
Discord and the caller sees `status: published` with no further signal.
This spec applies the issue author's preferred **A + C** combo: (A) filter
inbound `metadata.discord.*` to an outbound-safe allowlist inside `reply()`
so the common path stops mis-firing, and (C) lock in regression coverage
that the Discord adapter still publishes a yellow NACK Acknowledgment if
any other unrecognized `metadata.discord.*` field surfaces, so future
foot-shapes are visible in seconds instead of requiring a raw-log grep.
See issue [#161](https://github.com/jeffrichley/agent_core/issues/161).

## Acceptance criteria

- A new module-level constant
  `_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS: frozenset[str] = frozenset({"channel_id", "message_id"})`
  in `packages/core/src/agent_core/endpoints/claude_code_mcp.py`,
  with a docstring naming the inbound enrichment site
  (`agent_core_discord.endpoint.DiscordEndpoint._make_on_message_handler`,
  the `metadata = {"discord": {...}}` block around lines 1090-1104) so
  future inbound-enrichment additions have a single grep target.
- A new private helper
  `def _filter_inbound_discord_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]`
  in the same file. It returns a shallow copy of `metadata` with one
  change: if `metadata["discord"]` is a `dict`, replace it with the
  subset whose keys are in `_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS`. All
  non-`"discord"` top-level keys (e.g., `"trace_id"`, `"attachments"`)
  pass through untouched. If `metadata["discord"]` is missing,
  not-a-dict (string, list, None), or already empty, the value passes
  through unchanged. Pure function, no I/O, no logging.
- In the `reply()` MCP tool body
  (around `claude_code_mcp.py:1060`), the line
  `out_metadata = {**inbound_metadata, **(metadata or {})}` is replaced
  with the filter applied to the inbound side:
  `out_metadata = {**_filter_inbound_discord_metadata(inbound_metadata), **(metadata or {})}`.
  The caller-supplied `metadata` override still wins per shallow-merge
  semantics — its `"discord"` block (if any) replaces the filtered
  inbound block entirely, exactly as today.
- The same filter is applied identically whether the inbound was
  resolved from `self._pending` or from `self._recent_inbounds`. Both
  branches feed `inbound_metadata` into the same downstream merge, so
  the single edit at the merge site covers both.
- `reply()`'s docstring is updated: the existing "metadata merge is
  **shallow**" paragraph (lines 1015-1022) gains a one-sentence note
  that inbound `metadata.discord.*` keys outside
  `{channel_id, message_id}` are dropped before merge to keep the
  outbound Discord-adapter-valid, naming
  `_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS` as the source of truth.
- New tests in
  `packages/core/tests/test_claude_code_mcp_consume_reply.py`:
  - `test_reply_strips_inbound_only_discord_keys_from_outbound_metadata`:
    inbound `metadata.discord` carries the full enrichment set
    (`channel_id`, `message_id`, `guild_id`, `author_id`,
    `author_display_name`, `is_dm`, `is_bot`); after `reply()`, the
    published envelope's `metadata.discord` contains only
    `{"channel_id", "message_id"}` with the original values
    preserved, and no `guild_id`/`author_id`/`author_display_name`/
    `is_dm`/`is_bot` key.
  - `test_reply_preserves_non_discord_top_level_metadata`: inbound
    metadata has both a `discord` block and a sibling `trace_id`
    key; after `reply()`, `trace_id` is preserved as-is on the
    outbound and `discord` is filtered. Defends against the filter
    accidentally widening to non-discord siblings.
  - `test_reply_caller_metadata_override_still_replaces_discord_block`:
    inbound has full enrichment; caller passes
    `metadata={"discord": {"channel_id": "Z"}}`; the outbound's
    `metadata.discord` is exactly `{"channel_id": "Z"}` — proves the
    existing shallow-merge override semantics survive the filter
    (the filter runs on the inbound, not on the caller's override).
  - `test_reply_handles_missing_discord_block_in_inbound_metadata`:
    inbound metadata is `{}` (no `discord` key at all); `reply()`
    publishes successfully and the outbound's `metadata` is `{}`.
    Defends against a `KeyError` regression.
  - `test_reply_handles_non_dict_discord_block_in_inbound_metadata`:
    inbound `metadata = {"discord": "not-a-dict"}` (a malformed
    inbound — represented as a string); `reply()` does not raise, the
    filter leaves the value untouched, and the published outbound
    carries `metadata.discord == "not-a-dict"`. Adapter-level
    validation handles that case downstream (validator returns
    `Unrecognized` and produces a NACK).
  - `test_reply_after_consume_uses_recent_inbounds_cache_and_strips`:
    parallels the existing
    `test_reply_after_consume_uses_recent_inbounds_cache` (line 327)
    but with full enrichment on the inbound — proves the filter
    applies on the cache code path as well as the `_pending` path.
- A new test in
  `packages/agent-core-discord/tests/test_endpoint_outbound.py`:
  - `test_inbound_enrichment_fields_on_outbound_produce_failed_delivery_ack`:
    construct a `TextMessage` envelope whose `metadata.discord`
    contains the full inbound-enrichment set (the bug's exact
    fingerprint — `channel_id`, `message_id`, `guild_id`, `author_id`,
    `author_display_name`, `is_dm`, `is_bot`). Run it through
    `DiscordEndpoint.deliver()`. Assert (a) no Discord API call
    landed; (b) a `kind="Acknowledgment"` envelope with
    `urgency="yellow"` was published to the sender, with
    `in_reply_to` matching the original envelope id and the unrecognized
    field names enumerated in `payload.note`; (c) the original envelope
    was acked. Parallels the existing
    `test_unrecognized_field_produces_failed_delivery_ack`
    (line 2521) which uses a synthetic `mystery_field` — the new test
    pins the exact real-world shape from the bug repro so a future
    refactor that drops one of these fields from the validator's
    rejection set fails loudly.
- Two towncrier news fragments, one per package:
  - `packages/core/changelog.d/161.fixed.md`: one-line entry naming
    that `reply()` now filters inbound `metadata.discord.*` to the
    outbound-safe allowlist (`channel_id`, `message_id`) before merge,
    fixing silent drops at the Discord adapter.
  - `packages/agent-core-discord/changelog.d/161.fixed.md`: one-line
    entry confirming the adapter's NACK coverage for inbound-enrichment
    fields surfacing on outbound envelopes is now pinned by regression
    test.
  Both `changelog.d/` directories are new; create them. Format follows
  the towncrier conventions declared in each package's `towncrier.toml`
  (the `[[tool.towncrier.type]] directory = "fixed"` block).
- `just check` (lint + typecheck + tests) exits zero.

## Approach

The bug is one merge line in `reply()`. The fix is one filter at that
merge line plus regression tests at both layers (sender-side filter
and adapter-side NACK).

**1. Why allowlist, not denylist.** The shape validator's
`_KNOWN_DISCORD_META_OUTBOUND_KEYS` (in
`packages/agent-core-discord/src/agent_core_discord/shape_validator.py:139-144`)
is `{channel_id, embeds, message_id, reply_to}`. Of those, only
`channel_id` and `message_id` come from inbound enrichment (see
`agent_core_discord.endpoint.DiscordEndpoint._make_on_message_handler`,
lines 1090-1104 — `embeds` and `reply_to` are caller-supplied on
outbound, never written by `on_message`). A denylist of
`{guild_id, author_id, author_display_name, is_dm, is_bot}` matches
today's enrichment exactly, but the next inbound enrichment that lands
on `on_message` (`thread_id`, `webhook_id`, anything) silently leaks
through the merge and back into the same silent-drop pit. An allowlist
of `{channel_id, message_id}` flips the failure mode: new inbound
enrichments are dropped from `reply()` by default, and the engineer who
wants them on the outbound must consciously add the key to
`_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS` (and presumably to
`_KNOWN_DISCORD_META_OUTBOUND_KEYS` on the adapter side too).

**2. Why filter at the core MCP endpoint, not at the Discord
adapter.** Two reasons. (a) The Discord adapter already does the
right thing — when an unrecognized `metadata.discord.*` field reaches
it, `deliver()` (`endpoint.py:684-712`) routes the envelope to
`_reply()` which publishes a yellow `Acknowledgment` and acks the
inbound. The bug is upstream: the merge in `reply()` (which lives in
core, not in the Discord adapter) is the surface that pumps
inbound-only fields into outbounds in the first place. Fixing it at
the source means callers stop hitting the validator's reject path
through no fault of their own. (b) The core MCP endpoint must not
depend on `agent_core_discord` — that's a downstream-only import
direction. So the filter lives in core, with the allowlist defined
locally and a comment pointing back to the Discord enrichment site
as the lockstep target. If the day comes that a second adapter
enriches `metadata.<adapter>.*` on inbound, this same shape can be
lifted to a generic per-adapter filter; YAGNI for now (only Discord
enriches today).

**3. Why retain caller-override semantics unchanged.** The existing
shallow-merge contract is `inbound | caller_override`. Callers who
already pass `metadata={"discord": {...}}` are deliberately replacing
the entire inbound `discord` block; filtering their override would be
a behaviour change orthogonal to the bug. The filter runs on the
*inbound side* of the merge only, leaving the caller-supplied
`metadata` argument untouched. The existing test
`test_reply_metadata_override_wins_per_key` (line 299) still passes
because that test's inbound has only `channel_id` (already in the
allowlist) plus a top-level `trace_id` (untouched).

**4. The adapter-side regression test (Fix C).** The Discord adapter's
NACK path was added in #114 and is covered today by
`test_unrecognized_field_produces_failed_delivery_ack` (`test_endpoint_outbound.py:2521`),
which uses a synthetic `mystery_field`. The issue's third acceptance
criterion calls for end-to-end coverage that an unrecognized field
produces a NACK — the existing test demonstrates the path exists, but
no test pins the *exact* enrichment fields from the bug's repro. Add
one regression test seeded with `{channel_id, message_id, guild_id,
author_id, author_display_name, is_dm, is_bot}` so any future change
that loosens the validator's known-outbound-keys set (e.g., adds
`is_bot` to it) flips a red CI light. This is a small targeted test,
not a new infrastructure layer.

**5. Why not Fix B (permissive outbound validation).** The issue lists
B as an option but the issue author's lean is A + C. B trades silent
drops for silent acceptance of typos — `metadata.discord.cahnnel_id`
would land at the adapter, get ignored, and the message would fall to
the `outbound_channel_id` fallback or hit
`_ToolError("TextMessage requires metadata.discord.channel_id...")`.
That's a different silent-failure class, not a smaller one. Skipping B
keeps the strict-mode posture from #114 intact (which is what makes A
detectable in the first place via the validator's `Unrecognized`
branch).

**6. Conventions.** Module-level constants use SCREAMING_SNAKE prefixed
with underscore (matches `_KNOWN_DISCORD_META_OUTBOUND_KEYS` in
shape_validator). Helpers used inside the endpoint live as
module-level private functions, not methods on the endpoint class
(matches `_canonical_tool`, `_envelope_to_dict` style elsewhere in
`claude_code_mcp.py`). News fragments under `<package>/changelog.d/`
named `<issue>.<type>.md` follow the towncrier convention declared in
each `towncrier.toml`; both packages' towncriers register a `fixed`
type. Both `changelog.d/` directories are new — neither package has
filed a news fragment yet — so the Worker creates them and adds a
sentinel only if towncrier demands it (it doesn't; absent
`changelog.d/` is fine until release time).

## Sub-requests (topologically sorted)

1. In `packages/core/src/agent_core/endpoints/claude_code_mcp.py`,
   add the module-level constant
   `_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS: frozenset[str] = frozenset({"channel_id", "message_id"})`
   above the `ClaudeCodeMCPEndpoint` class definition (near the other
   module-level constants — co-locate with whatever
   `_AUTO_ACK_NOTE` / `_KNOWN_*` constants already live there; if
   none, place above the class with a short module-level comment).
   Include a docstring naming the inbound enrichment site
   (`agent_core_discord.endpoint.DiscordEndpoint._make_on_message_handler`)
   so a grep for the constant lands on the lockstep target.
2. In the same file, add the helper:

   ```python
   def _filter_inbound_discord_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
       """Return a shallow copy of ``metadata`` with ``metadata['discord']``
       filtered to the outbound-safe key set.

       The Discord adapter populates ``metadata.discord`` on inbound with
       enrichment fields (``guild_id``, ``author_id``, ``author_display_name``,
       ``is_dm``, ``is_bot``) that the strict-mode shape validator (#114)
       rejects on outbound. ``reply()`` shallow-merges inbound metadata into
       its outbound envelope; without this filter, those fields leak into
       the outbound and trigger an ``Unrecognized`` NACK at the adapter,
       silently dropping the user-visible reply (issue #161).

       Lockstep with the inbound enrichment site:
       ``agent_core_discord.endpoint.DiscordEndpoint._make_on_message_handler``,
       the ``metadata = {"discord": {...}}`` block. If a new outbound-safe
       field is added on the enrichment side, add it to
       ``_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS`` AND to
       ``shape_validator._KNOWN_DISCORD_META_OUTBOUND_KEYS``.
       """
       result = dict(metadata)
       discord = result.get("discord")
       if isinstance(discord, dict):
           result["discord"] = {
               k: v
               for k, v in discord.items()
               if k in _OUTBOUND_SAFE_INBOUND_DISCORD_KEYS
           }
       return result
   ```

   Ensure `from collections.abc import Mapping` is imported (or
   `typing.Mapping` if that matches the file's existing convention —
   verify which is already imported and use it).
3. In the `reply()` MCP tool body in the same file, replace the
   single line
   `out_metadata = {**inbound_metadata, **(metadata or {})}` (around
   line 1060) with
   `out_metadata = {**_filter_inbound_discord_metadata(inbound_metadata), **(metadata or {})}`.
4. Update the `reply()` docstring (lines 1015-1022) to append one
   sentence to the existing shallow-merge paragraph:
   *"Inbound `metadata.discord.*` keys outside
   `_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS` (`channel_id`, `message_id`)
   are stripped before the merge so the outbound passes the Discord
   adapter's strict-mode shape validator (#114); without this, replies
   to Discord-routed inbounds silently drop at the adapter (#161)."*
   No other paragraphs change.
5. In `packages/core/tests/test_claude_code_mcp_consume_reply.py`,
   add the five new tests enumerated under Acceptance criteria. Reuse
   the existing `_inbound_text` helper (verify its signature accepts
   a `metadata=` kwarg — it does, see line 200's usage) and the
   `_RecordingHandle` fixture pattern used by surrounding tests.
   Each test must assert on the *exact* outbound `metadata.discord`
   dict (`assert out.metadata == {...}`) so a regression on the
   allowlist contents shows up as a precise diff, not a wildcard
   "didn't crash" pass.
6. In `packages/agent-core-discord/tests/test_endpoint_outbound.py`,
   add `test_inbound_enrichment_fields_on_outbound_produce_failed_delivery_ack`
   directly below the existing
   `test_unrecognized_field_produces_failed_delivery_ack` (line 2521),
   following the same `_started(monkeypatch)` / `FakeChannel` /
   `await ep.deliver(env)` setup. The metadata payload is exactly:

   ```python
   metadata={"discord": {
       "channel_id": "123",
       "message_id": "456",
       "guild_id": "789",
       "author_id": "1011",
       "author_display_name": "test-user",
       "is_dm": False,
       "is_bot": True,
   }}
   ```

   Assertions: (a) `ch.sent == []`; (b) a `kind="Acknowledgment"`,
   `urgency="yellow"` envelope was published with `in_reply_to ==
   env.id` and `payload.note` containing each of the five
   inbound-only field names (`guild_id`, `author_id`,
   `author_display_name`, `is_dm`, `is_bot`); (c) `env.id` in
   `handle.acked`. The test serves as the bug's named regression and
   is independent of the `reply()` fix — the adapter must NACK even
   if a future caller (or a future inbound-enrichment-only bus
   producer) ever surfaces this shape.
7. Create `packages/core/changelog.d/161.fixed.md` with a one-line
   `fixed` entry: *"`reply()` strips Discord inbound-only metadata
   (`guild_id`, `author_id`, `author_display_name`, `is_dm`, `is_bot`)
   before merging into the outbound; replies to Discord-routed
   inbounds now reach the adapter instead of silently dropping at the
   strict-mode shape validator (#161)."*
8. Create `packages/agent-core-discord/changelog.d/161.fixed.md`
   with a one-line `fixed` entry: *"Regression test pins the NACK
   path for `TextMessage` outbounds carrying Discord inbound-only
   enrichment fields (`guild_id`, `author_id`, `author_display_name`,
   `is_dm`, `is_bot`); the adapter publishes a yellow
   `Acknowledgment` instead of silently dropping (#161)."*
9. Run `just check` from the repo root and confirm zero exit. Fix
   anything that surfaces. The full test matrix matters because the
   merge-line edit could conceivably interact with the
   `_recent_inbounds` cache copy semantics — the new
   `test_reply_after_consume_uses_recent_inbounds_cache_and_strips`
   is the canary for that.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/endpoints/claude_code_mcp.py` | Add `_OUTBOUND_SAFE_INBOUND_DISCORD_KEYS` constant; add `_filter_inbound_discord_metadata` helper; thread the helper through `reply()`'s metadata merge; extend `reply()` docstring with the strip semantics. |
| `packages/core/tests/test_claude_code_mcp_consume_reply.py` | Add five regression tests: full-enrichment strip, non-discord-top-level preservation, caller-override semantics, missing-discord-block safety, non-dict-discord-block safety, and the cache-path variant. |
| `packages/agent-core-discord/tests/test_endpoint_outbound.py` | Add `test_inbound_enrichment_fields_on_outbound_produce_failed_delivery_ack` — pins adapter NACK for the exact bug-repro field set. |
| `packages/core/changelog.d/161.fixed.md` | New file. One-line towncrier `fixed` entry for the core change. Creates the `changelog.d/` directory. |
| `packages/agent-core-discord/changelog.d/161.fixed.md` | New file. One-line towncrier `fixed` entry for the adapter regression coverage. Creates the `changelog.d/` directory. |

## Alternatives considered

- **Fix B from the issue: make the Discord adapter's outbound
  validator permissive (ignore unrecognized `metadata.discord.*`
  fields).** Rejected — issue author's lean is A + C, and B trades
  one silent-failure class (rejection-without-NACK) for another
  (typos-silently-accepted). Keeping the strict-mode posture from
  #114 is what makes inbound-only field leaks detectable at all.
- **Denylist `{guild_id, author_id, author_display_name, is_dm,
  is_bot}` instead of allowlist `{channel_id, message_id}`.**
  Rejected because the next inbound enrichment landed on
  `_make_on_message_handler` re-opens the silent-drop pit without
  anyone touching this file. Allowlist forces the conscious add.
- **Move the filter into the Discord adapter (strip on outbound
  delivery).** Rejected because the adapter already handles the case
  correctly via NACK; the bug is the caller sending an
  Unrecognized-shaped envelope through `reply()`, which is a core MCP
  endpoint concern. Filtering at the adapter would silently fix the
  bug but lose the strict-mode signal, equivalent to choosing Fix B.
- **Generalize to a per-adapter inbound-only filter
  (`metadata.<adapter>.*` matrix) in `Envelope` itself.** Rejected as
  premature — only Discord enriches `metadata.<x>` today. YAGNI;
  introduce when a second adapter needs it.
- **Add a Pydantic model for `metadata.discord` and let validation
  reject inbound-only fields at envelope construction time.**
  Rejected because `Envelope.metadata` is intentionally schema-free
  (`dict[str, Any]`) — see `bus/envelope.py`. Schemaifying it is a
  far larger and orthogonal refactor that no other adapter has asked
  for.
- **Do nothing; document the workaround memory
  (`feedback_discord_reply_metadata_asymmetry`) more loudly.**
  Rejected by the issue (*"That memory is the workaround. This ticket
  is the structural fix."*). Workaround memories accumulate friction
  and don't survive new bus callers who haven't read them.
- **Implement the peek-tool / publish-vs-delivery observability
  surface (Pepper's pending ticket) instead.** Rejected as orthogonal —
  the issue Related section explicitly scopes that to a separate
  ticket. Observability surfaces a category of bug; this ticket fixes
  one specific bug in that category.

## Open questions

None blocking. The Reviewer may wish to confirm:

- That the allowlist `{channel_id, message_id}` is the full
  outbound-safe subset of today's inbound enrichment. The reasoning is
  spelled out in Approach §1 (validator's `_KNOWN_DISCORD_META_OUTBOUND_KEYS`
  is `{channel_id, embeds, message_id, reply_to}`, and only
  `channel_id`/`message_id` come from `on_message`; `embeds` and
  `reply_to` are caller-supplied). If a reviewer disagrees and would
  add e.g. `embeds` to the allowlist, the change is a one-line edit to
  the constant.

## Out of scope

- Implementing Pepper's peek-tool / publish-vs-delivery observability
  surface. The issue Related section calls this out as a separate
  pending ticket.
- Adding Fix B (permissive outbound validation). Issue author's lean
  is A + C, not A + B + C.
- Refactoring `metadata.discord` into a Pydantic / dataclass schema.
  Out of scope per Alternatives.
- Generalizing the inbound-only filter to a generic per-adapter
  matrix on `Envelope`. YAGNI until a second adapter enriches.
- Changing the shape validator's
  `_KNOWN_DISCORD_META_OUTBOUND_KEYS` set. The bug is upstream of
  validation; this spec does not modify the validator.
- Modifying the `_record_recent_inbound` cache shape or its
  metadata-copy semantics (`claude_code_mcp.py:344-356`). The filter
  runs at the merge site in `reply()`, which catches both the
  `_pending` and cache code paths without any cache change.
- Adding NACK envelopes for non-validation drops elsewhere in the
  Discord adapter (e.g., `discord.errors.HTTPException` paths in
  `_send`). The adapter already covers those via the existing
  `_reply()` / `_ToolError` branches in `deliver()` (lines 735-739);
  the issue's Fix C scope is the validator path specifically.
- Auto-stripping or transforming `metadata.attachments` (set by
  `_make_on_message_handler` when the inbound has attachments). The
  attachments block is at top-level metadata, not inside
  `metadata.discord`, and is irrelevant to the bug's validator
  rejection. Future work if needed.
