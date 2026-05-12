# Issue #83 — Inline wake preview exposes `channel_id` + auto-echo on outbound (Design)

> **Status:** Drafted 2026-05-12. Pending spec-review approval.
>
> **Issue:** [#83](https://github.com/jeffrichley/agent_core/issues/83) — `discord-pepper: inline wake preview should expose inbound channel_id`.
>
> **Scope:** Combined D = A + C from the issue body. A surfaces `channel_id` and `channel_name` on the `<inbox>` preview tag; C adds `in_reply_to` → recent-inbounds cache auto-echo on outbound, with `metadata.discord.channel_id` always winning when explicitly set and a hard error when neither resolution path succeeds.

## Problem

Post-2026-05-06 channel cutover, channel routing on Discord outbounds became explicit — the agent must set `metadata.discord.channel_id` itself. But the inline `<inbox>` preview format in `notifications/claude/channel` wakes does not include `metadata.discord.channel_id`. Receiving agents (Pepper, Wren) cannot determine the inbound channel without:

1. Calling `mcp__agent-core__peek(envelope_id)` to hydrate full metadata, OR
2. Calling `mcp__agent-core__list_pending()` and reading the item's metadata, OR
3. Using `mcp__agent-core__reply(in_reply_to=...)` (works for short replies; fails for long composes via the bus-level "reply_to message not found" path).

**Concrete failure mode**, 2026-05-12 morning: Jeff typed in `#pepper-upgrade` (channel_id `1491445346570866812`). Pepper kept replying in `#pepper-chat` (channel_id `1488680018077945978`) because she had hardcoded the latter and the wake gave her no signal of the actual inbound channel. Three corrections from Jeff before the diagnosis landed. The "manual channel_id maintenance" pattern is the failure mode itself.

This design surfaces the channel context in the preview (A) AND adds an explicit-or-auto resolution chain on outbound that eliminates the maintenance burden (C). The two changes are orthogonal at the code layer but compose at the agent's behavior layer.

## Out of scope

- Surfacing other Discord metadata fields (`author_display_name`, `guild_id`, `is_dm`) on the preview. Each requires a named symptom in current agent code; none exists today. Add later per ticket with named demand.
- Endpoint-registered preview-attr registry abstraction. Today there is exactly one endpoint (Discord) needing preview-attr surfacing. Rule-of-three says abstract on N=3. Followup: extract a shared preview-attr mechanism when a non-Discord adapter earns it.
- Shared `RecentInboundsCache` utility extraction. `claude_code_mcp.py` already has a per-endpoint cache pattern; discord-pepper adds a second instance with parallel-but-distinct data. N=2 of the pattern, defer to N=3. Followup tracked.
- Looser matching rules for auto-echo (`correlation_id` fallback, "last inbound" heuristic). Both have false-echo / cross-thread risk; `in_reply_to` exact match is the only safe resolution path. Looser rules await named symptoms.
- Bus-level envelope schema change. The change is to the `<inbox>` *string representation* in the wake notification and to discord-pepper's outbound routing logic. Bus envelope shape and persistence are unchanged. No schema migration.
- Retrofitting XML escape to existing framework attrs (`kind`, `from`, `urgency`, `envelope_id`). These are bounded enums and hex IDs today; structurally safe. Defensive retrofit is a separate followup.
- `send()` API ergonomics. Making `in_reply_to` more prominent on `mcp__agent-core__send`'s tool description (to discourage the ambiguous-middle case) is a separate followup ticket post-#83.

## Design

### Architecture

Two surfaces, both already in the workspace, both independent at the code level:

| Concern | File | Change shape |
|---|---|---|
| **A** — preview surfaces `channel_id` + `channel_name` | `packages/agent-core-channel/src/agent_core_channel/rendering.py` | Extract attribute construction from `render_envelope` into a `_inbox_attrs(env)` helper. Helper adds a per-namespace if-block for the `discord` metadata namespace. Called from the three `<inbox>` construction sites: `render_envelope`, `_render_with_truncation`, `_render_preview`. |
| **C** — outbound channel resolution chain | `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | New `_resolve_channel_id(outbound) -> str` method on `DiscordEndpoint`. Precedence: explicit `metadata.discord.channel_id` → `in_reply_to` → recent-inbounds cache lookup → `_ToolError`. New `_recent_inbounds: OrderedDict[str, Envelope]` bounded LRU+TTL cache populated when discord-pepper publishes inbound envelopes from `on_message` / `on_reaction_add`. |

A and C are independent. A ships even if C fails (preview surfaces channel info; agent passes it through manually). C ships even if A fails (`reply()` and explicit `in_reply_to`+`send()` continue to work). Together they eliminate the manual-channel_id-maintenance failure mode.

The rendering layer remains otherwise endpoint-agnostic. The per-namespace block is one localized addition keyed off `env.metadata.discord` presence — not a registry, not an architectural seam. Future namespaces (gmail, calendar) add another if-block when they earn it per rule-of-three.

### Components

#### A — `rendering.py`: attrs helper extracted, discord-namespace block added

```python
from xml.sax.saxutils import quoteattr


def _inbox_attrs(env: dict) -> list[str]:
    """Build the `<inbox>` framework attribute list for an envelope.

    Framework attrs: kind, from, urgency, envelope_id, optional in_reply_to.
    Per-namespace preview surfacing: discord namespace contributes
    channel_id (and channel_name when channel_id is also present) so
    receiving agents can route replies without a peek round-trip.

    Mode flags (preview, render='fallback', batch) are appended by callers
    after this helper returns; they are not envelope-content.
    """
    kind = env.get("kind", "Unknown")
    env_id = env.get("id", "")
    from_ = env.get("from", "")
    urgency = env.get("urgency", "green")
    in_reply_to = env.get("in_reply_to")

    attrs = [
        f"kind='{kind}'",
        f"from='{from_}'",
        f"urgency='{urgency}'",
        f"envelope_id='{env_id}'",
    ]
    if in_reply_to:
        attrs.append(f"in_reply_to='{in_reply_to}'")

    # Per-namespace preview surfacing. Add cases as namespaces earn them
    # via documented agent symptoms (rule-of-three before any registry).
    discord = (env.get("metadata") or {}).get("discord") or {}
    if discord:
        cid = discord.get("channel_id")
        if cid:
            attrs.append(f"channel_id={quoteattr(str(cid))}")
            cname = discord.get("channel_name")
            if cname:
                attrs.append(f"channel_name={quoteattr(str(cname))}")

    return attrs
```

**Attribute naming:** `channel_id`, `channel_name` (snake_case, matching existing `envelope_id`, `in_reply_to`).

**XML escape via `quoteattr`:** Channel/thread names from Discord can contain apostrophes (DM names derived from display names; thread names; legacy server-renamed channels). `quoteattr` returns properly-quoted attribute values handling `'`, `"`, `<`, `>`, `&`. `channel_id` is escaped too for defensiveness, even though Discord snowflakes are numeric.

**Stricter rule:** `channel_name` is emitted *only when `channel_id` is also present*. Routing-actionability gates surface-presence; a channel_name without a channel_id is noise that pretends to be signal (an agent might think they have routing info, then hit the hard-error).

**Callers:**
- `render_envelope` — calls `_inbox_attrs(env)`, appends `render='fallback'` if body fell back.
- `_render_with_truncation` — calls `_inbox_attrs(env)`, appends `render='fallback'` per its `fallback` parameter.
- `_render_preview` — calls `_inbox_attrs(env)`, appends `preview='true'`.

The fourth `<inbox>` emission inside `render_item` (defensive fallback for unrecognized item shapes) constructs an inert block with no env metadata — does not use the helper.

**Batch attribute** (`batch='N/M'`) is applied by `render_item` via post-processing string replacement and coexists with the new attrs.

#### C — `discord-pepper/endpoint.py`: channel_id resolution chain

This is **preservation + additive**. Today's `_send` already reads `metadata.discord.channel_id` and errors when missing. The change adds a fallback resolution layer between explicit-set and hard-error.

| Layer | Today | After |
|---|---|---|
| Explicit `metadata.discord.channel_id` | Used | **Unchanged** — explicit always wins. |
| Fallback when explicit missing | Hard error | **Try `in_reply_to` cache lookup**, then hard error. |
| Hard error shape | Yellow Ack with `error:` prefix | **Same shape**, preserved as floor. |

```python
def _resolve_channel_id(self, outbound: Envelope) -> str:
    """Resolve channel_id with precedence:
    1. Explicit `metadata.discord.channel_id` (preserves current behavior).
    2. Fallback: `in_reply_to` → cached inbound's channel_id (auto-echo).
    3. Hard error — refuse to guess (preserves current safety floor).

    Sub-causes for the failure path are logged server-side at WARNING
    level for operator diagnosis; the agent-facing Ack message is
    unified across sub-causes (consistent remediation cue).
    """
    # 1. Explicit always wins (preservation invariant).
    discord_meta = (outbound.metadata or {}).get("discord") or {}
    if explicit := discord_meta.get("channel_id"):
        return explicit

    # 2. Auto-echo via in_reply_to cache lookup.
    if outbound.in_reply_to:
        inbound = self._recent_inbounds.get(outbound.in_reply_to)
        if inbound:
            inbound_discord = (inbound.metadata or {}).get("discord") or {}
            if cid := inbound_discord.get("channel_id"):
                return cid
            logger.warning(
                "channel_id resolution failed: cached_inbound_missing_channel_id, "
                "in_reply_to=%s", outbound.in_reply_to,
            )
        else:
            logger.warning(
                "channel_id resolution failed: cache_miss, "
                "in_reply_to=%s", outbound.in_reply_to,
            )
    else:
        logger.warning(
            "channel_id resolution failed: no_explicit_no_in_reply_to, "
            "outbound_id=%s", outbound.id,
        )

    # 3. Refuse to guess.
    raise _ToolError(
        "cannot determine channel — set metadata.discord.channel_id "
        "explicitly, or set in_reply_to so auto-echo can resolve."
    )
```

**Cache: `_recent_inbounds: OrderedDict[str, Envelope]`**, bounded LRU + TTL eviction. Mirrors the pattern in `claude_code_mcp.py:217` (different scope: discord-pepper tracks envelopes it *published from Discord*; claude_code_mcp tracks envelopes it *delivered to its agent*). Defaults `cap=5000`, `ttl=3600` (1h) — chosen by mirroring `claude_code_mcp`, not derived independently. Any divergence later needs named evidence.

**Cache write:** discord-pepper's existing inbound publish paths (`on_message`, `on_reaction_add`, `on_raw_*`) call `self._record_inbound(envelope)` after publishing. The recorder copies `metadata` to avoid post-cache mutation bleed (same defensive pattern as `claude_code_mcp._record_recent_inbound`).

**Call sites switched to `_resolve_channel_id`:** all paths in discord-pepper that today read `metadata.discord.channel_id` directly — `_send`, `_send_briefing`, `_edit`, `_react`, `_send_typing`, the TextMessage envelope handler. Estimated 5–7 sites; exact count locked in writing-plans pass.

**Error delivery channel:** yellow `Acknowledgment` with `error:` prefix, published from `deliver()` synchronously. Matches the existing `test_textmessage_without_channel_returns_error` shape exactly.

### Data flow

Four scenarios cover the surface. The composition summary at the end is the single-glance artifact.

#### Scenario 1 — Inbound arrives, agent sees channel context (A in action)

```
1. Jeff types in #pepper-upgrade (channel_id 1491445346570866812).
2. discord-pepper.on_message receives the discord.Message.
3. discord-pepper builds Envelope:
     id=abc, from=discord-pepper, to=agent-pepper, kind=TextMessage,
     payload={text: "hi"},
     metadata={discord: {channel_id: "1491445346570866812",
                        channel_name: "#pepper-upgrade",
                        ...}}
4. discord-pepper publishes to bus AND records envelope in
   self._recent_inbounds (key=abc).
5. Bus delivers to Pepper's pickup queue.
6. agent-core-channel renders the wake via render_envelope →
   _inbox_attrs(env) → attrs include channel_id + channel_name
   from the discord namespace:
     <inbox kind='TextMessage' from='discord-pepper' urgency='green'
            envelope_id='abc' channel_id="1491445346570866812"
            channel_name="#pepper-upgrade">hi</inbox>
7. Pepper reads the wake. Channel context is visible at a glance,
   no peek round-trip needed.
```

#### Scenario 2 — Short reply via `reply()` (existing path, unchanged)

```
Pepper calls mcp__agent-core__reply(in_reply_to=abc, payload={text: "hello back"}).
reply() inherits routing from the inbound's recent-inbounds entry
(claude_code_mcp side), including metadata.discord.channel_id. The
outbound envelope carries channel_id explicitly. discord-pepper.deliver
receives, _resolve_channel_id returns the explicit value at step 1
(explicit always wins). Posts to #pepper-upgrade.
```

No code change in this path. Documented to show explicit-wins consistency.

#### Scenario 3 — Long compose via `send()` with `in_reply_to` (C in action)

```
Pepper has a 4KB response. Uses send() instead of reply():

  mcp__agent-core__send(
    to="discord-pepper",
    kind="TextMessage",
    in_reply_to="abc",                  # threading
    payload={text: "long response..."}, # NO explicit channel_id
  )

discord-pepper.deliver receives. _resolve_channel_id:
  1. metadata.discord.channel_id explicit? No → skip.
  2. in_reply_to=abc set? Yes. Lookup self._recent_inbounds["abc"].
     Hit. Read inbound's metadata.discord.channel_id = "1491445...".
     Return it.
  3. Send proceeds with resolved channel_id. Posts to #pepper-upgrade.
```

Pepper did not have to copy `channel_id` from the preview into the outbound — setting `in_reply_to` was enough. The manual-channel_id-maintenance failure mode is eliminated.

#### Scenario 4 — Forgot to thread, no explicit channel (hard error)

```
Pepper calls send() WITHOUT in_reply_to AND WITHOUT explicit channel_id:

  mcp__agent-core__send(
    to="discord-pepper",
    kind="TextMessage",
    payload={text: "announcement"},
  )

discord-pepper.deliver receives. _resolve_channel_id:
  1. metadata.discord.channel_id? No.
  2. in_reply_to set? No → skip cache lookup entirely.
  3. Refuse to guess. logger.warning("...no_explicit_no_in_reply_to..."),
     raise _ToolError.

discord-pepper publishes yellow Acknowledgment:
  kind=Acknowledgment, urgency=yellow,
  payload.note="error: cannot determine channel — set
               metadata.discord.channel_id explicitly, or set
               in_reply_to so auto-echo can resolve."
```

#### Topic-routing variant

Pepper intentionally sends to a different channel than the inbound's. She sets `metadata.discord.channel_id="<other-channel-id>"` explicitly. `_resolve_channel_id` step 1 returns the explicit value immediately — `in_reply_to` is ignored even if set. **Topic-override always works.**

#### Composition summary

| Agent action | A surfaces | C resolves | Result |
|---|---|---|---|
| `reply(in_reply_to=X)` | Preview shown; irrelevant to outbound | Inherits explicit channel from `reply()` | Routes correctly |
| `send(in_reply_to=X)` | Preview shown; agent does not copy channel_id | Cache lookup via `in_reply_to` | Routes correctly |
| `send(channel_id=X explicit)` | Preview shown; agent reads it and passes through | Explicit wins | Routes correctly (topic override works) |
| `send()` with neither | Preview shown; agent forgets to use it | Refuse to guess | Yellow Ack error, agent fixes |

**Verb coverage:** the same resolution chain applies to every Discord verb that needs a channel — `send`, `react`, `edit`, `send_typing`, `send_briefing`, and the TextMessage envelope handler. An agent that wants to react to an inbound sets `in_reply_to` and omits `channel_id`; same flow, same auto-echo, same hard-error floor. No verb-specific routing logic.

### Error handling

Three categories: resolution failures (C side), renderer failures (A side), and expected-degraded states.

#### Resolution failures — unified Ack, distinct logs

All failure modes funnel through one `_ToolError` raised from `_resolve_channel_id`, surfaced as a single yellow Acknowledgment with `error:` prefix. The agent's remediation is the same in every case (set `channel_id` or fix threading); a unified message reduces cognitive load. Sub-cause discrimination lives in server-side logs.

| Sub-cause | Trigger | Log line |
|---|---|---|
| No explicit, no `in_reply_to` | Outbound has neither. | `no_explicit_no_in_reply_to, outbound_id=...` |
| Cache miss (never recorded) | `in_reply_to` set, referenced envelope not in cache. | `cache_miss, in_reply_to=...` |
| Cache miss (TTL evicted) | Old inbound aged out of cache. | `cache_miss, in_reply_to=...` (same line; differentiated by examining cache stats in companion logs if needed) |
| Cache miss (LRU evicted) | Cache reached `cap`. | `cache_miss, in_reply_to=...` |
| Cache miss (daemon restart) | Cold-start; cache empty. | `cache_miss, in_reply_to=...` |
| Cached inbound has no channel_id | Defensive: cache hit, but cached envelope's `metadata.discord.channel_id` missing/empty. Should be impossible (discord-pepper publishes with channel_id set). | `cached_inbound_missing_channel_id, in_reply_to=...` |

Note: TTL-vs-LRU-vs-never-recorded share a single `cache_miss` log line because the agent's remediation does not depend on the distinction. Structured detail (sub-cause codes on the Ack payload) is a deferred enhancement; if the unified message proves insufficient in practice, add then.

#### Renderer failures — existing fallback preserved

`render_envelope` and siblings already have try/except blocks that catch renderer exceptions and emit a fallback `<inbox>` block with `render='fallback'`. The new `_inbox_attrs` helper inherits the same protection: if it raises (e.g., on a malformed envelope dict), the caller's existing `_render_fallback_body` path triggers, the wake notification still ships, and the agent sees a fallback block with the diagnostic body.

Defensive shape inside `_inbox_attrs`:
- `(env.get("metadata") or {}).get("discord") or {}` — idiomatic null-safety.
- Walrus `if cid := discord.get("channel_id")` correctly skips empty strings and `None` (falsy).
- `quoteattr(str(cid))` defensively coerces to string before escape.

No exception path through normal data.

#### Cold-start property — expected, documented

After daemon restart, `_recent_inbounds` is empty. Auto-echo lookup fails for any outbound whose `in_reply_to` references a pre-restart inbound. Resolves to the **same hard-error** as any other cache miss.

**Not a bug.** Mirrors `claude_code_mcp.py`'s identical cold-start property for `reply()`. The cache is a runtime optimization, not a durable contract. Agents that need cross-restart threading must either:
- Use `reply()` while the inbound is still fresh (auto-clears at handle time, but works pre-restart), OR
- Set `metadata.discord.channel_id` explicitly on the outbound (always wins, always works).

#### Graceful degradation on partial metadata

| Metadata shape | Helper behavior |
|---|---|
| `discord = {channel_id: "X", channel_name: "#x"}` | Both attrs emitted. |
| `discord = {channel_id: "X"}` (no name) | Only `channel_id` emitted. |
| `discord = {channel_name: "#x"}` (no id) | **Neither emitted.** channel_name without channel_id is noise; routing-actionability gates surface. |
| `discord = {channel_id: "", channel_name: None}` | Neither emitted (falsy skip). |
| `discord = {}` (empty dict, truthy by existence) | Block entered, no fields emitted. No crash. |
| `metadata = {}` or missing entirely | Block not entered. Helper returns framework attrs only. |
| `metadata = "non-dict"` (defensive) | Idiomatic `.get()` chain returns `{}`. No crash. |

Rule: **emit what is present and truthy; degrade silently on missing/empty; never crash on malformed metadata.**

## Testing

Six groups, ~40 tests total. Each test name communicates behavior; bodies are drafted in the writing-plans pass.

### 5.1 `_inbox_attrs(env)` helper unit tests

**Framework attrs:**
- `test_inbox_attrs_framework_only_no_metadata`
- `test_inbox_attrs_includes_in_reply_to_when_set`
- `test_inbox_attrs_omits_in_reply_to_when_none`

**Discord namespace, happy path:**
- `test_inbox_attrs_emits_channel_id_and_name_when_both_present`
- `test_inbox_attrs_emits_channel_id_alone_when_name_missing`
- `test_inbox_attrs_escapes_special_chars_in_channel_name` — verifies `pepper's chat`, `<weird>`, `a & b` produce parser-safe output via `quoteattr`.

**Discord namespace, defensive degradation:**
- `test_inbox_attrs_omits_both_when_channel_id_missing_even_if_name_present`
- `test_inbox_attrs_omits_both_on_empty_strings`
- `test_inbox_attrs_omits_both_on_none_values`
- `test_inbox_attrs_handles_empty_discord_dict`
- `test_inbox_attrs_handles_missing_metadata`
- `test_inbox_attrs_handles_non_dict_metadata_defensively`

**Caller integration:**
- `test_render_envelope_includes_channel_attrs_for_discord_inbound`
- `test_render_preview_includes_channel_attrs_for_discord_inbound`
- `test_render_with_truncation_includes_channel_attrs_for_discord_inbound`
- `test_render_item_batch_preserves_channel_attrs_alongside_batch_attr`
- `test_render_envelope_fallback_emits_channel_attrs_with_render_fallback`

### 5.2 `_resolve_channel_id(outbound)` resolver unit tests

**Precedence order:**
- `test_resolve_explicit_channel_id_wins_over_cache_hit` — topic-override invariant.
- `test_resolve_returns_cached_channel_id_on_cache_hit_when_explicit_missing`

**Hard-error sub-causes (combined raises + logs):**
Each test asserts both `pytest.raises(_ToolError)` and `caplog.text` (at WARNING level) within one body.
- `test_resolve_raises_and_logs_when_neither_explicit_nor_in_reply_to_set`
- `test_resolve_raises_and_logs_on_cache_miss_never_recorded`
- `test_resolve_raises_and_logs_on_cache_miss_after_ttl_eviction`
- `test_resolve_raises_and_logs_on_cache_miss_after_lru_eviction`
- `test_resolve_raises_and_logs_on_cache_miss_after_daemon_restart` — fresh endpoint instance simulates cold start.
- `test_resolve_raises_and_logs_when_cached_inbound_has_no_channel_id`
- `test_resolve_error_message_is_unified_across_sub_causes`

**Logging-assertion convention:** `caplog` fixture at `logging.WARNING`. Documented here so the writing-plans pass inherits the choice.

### 5.3 Cache lifecycle (`_recent_inbounds`)

Mirrors `claude_code_mcp.py`'s test patterns for `_recent_inbounds`:
- `test_recent_inbounds_records_inbound_on_publish`
- `test_recent_inbounds_lru_eviction_caps_at_max`
- `test_recent_inbounds_ttl_sweep_removes_stale`
- `test_recent_inbounds_lookup_returns_none_after_eviction`
- `test_recent_inbounds_lookup_returns_none_for_unknown_envelope_id`
- `test_recent_inbounds_defaults_match_claude_code_mcp` — locks `cap=5000`, `ttl=3600` by mirroring.

### 5.4 Verb coverage (auto-echo applies uniformly)

Parameterized across every Discord verb that resolves a channel:
- `test_auto_echo_resolves_channel_for_text_message_envelope`
- `test_auto_echo_resolves_channel_for_send_tool`
- `test_auto_echo_resolves_channel_for_edit_tool`
- `test_auto_echo_resolves_channel_for_react_tool`
- `test_auto_echo_resolves_channel_for_send_typing_tool`
- `test_auto_echo_resolves_channel_for_send_briefing_tool`
- `test_auto_echo_hard_errors_uniformly_across_verbs` — same Ack shape from every verb when channel can't resolve.

### 5.5 Integration test — end-to-end happy path

- `test_inbound_to_outbound_routes_correctly_via_in_reply_to_only` — simulate Discord inbound in channel A; verify wake preview surfaces `channel_id` (A); agent calls `send(in_reply_to=<inbound>, text="long")` without explicit channel_id; verify outbound posts to channel A (C).

One E2E lock for the full A+C composition that addresses the 2026-05-12 bug.

### 5.6 Regression locks

- `test_explicit_channel_id_still_routes_correctly_unchanged` — today's explicit-channel_id path preserved.
- `test_existing_textmessage_without_channel_error_message_unchanged` — locks `test_textmessage_without_channel_returns_error` continues to pass.
- `test_reply_tool_inheritance_path_unchanged`.

## Followups (out of scope for #83)

Tracked for separate tickets:

1. **Retrofit XML escape on framework `<inbox>` attrs** (`kind`, `from`, `urgency`, `envelope_id`). Bounded enums and hex IDs today, structurally safe, but defensive is defensive.
2. **Make `in_reply_to` prominent on `mcp__agent-core__send`**. The ambiguous-middle case (forgot both) becomes loud-error in this design; making `in_reply_to` more discoverable on the tool surface reduces the rate of that error class.
3. **Extract `RecentInboundsCache` shared utility** when a third endpoint needs the pattern (currently N=2 after this lands: `claude_code_mcp` + `discord-pepper`).
4. **Structured detail on the unified error Ack** if the single-message-across-sub-causes proves insufficient in practice. Sub-cause codes on `note_detail` are the natural next surface.

## Implementation order

1. A first, in isolation: helper extraction, per-namespace block, callers updated, unit tests. Ships safely on its own; preview includes channel info even before C lands.
2. Cache infrastructure: `_recent_inbounds` + `_record_inbound` + sweep loop, mirroring `claude_code_mcp`. Tests.
3. C: `_resolve_channel_id` helper, call-site migration across the verbs. Tests.
4. Integration test 5.5 verifies the composition.
5. Regression locks 5.6 verify no behavioral drift.

Each step lands its own tests; the implementation plan (writing-plans) will sequence the bite-sized tasks.

## Open questions for spec review

Nothing currently open. All design questions resolved during brainstorming:
- Architectural shape (per-namespace if-block vs registry) → if-block, defer registry to N=3.
- Which Discord attrs to surface → `channel_id` + `channel_name` only, named-symptom rule.
- Auto-echo matching rule → `in_reply_to` exact only; looser rules deferred.
- Hard-error vs silent fallback → hard error, "refuse to guess."
- Cache location (bus vs endpoint) → endpoint, mirroring `claude_code_mcp`, N=2 of pattern.
- Logging UX → unified Ack message, distinct server-side log lines.
- `channel_name` without `channel_id` → strict pairing, both or neither.
- Attribute escape → `quoteattr` on all helper-emitted values.
