# Issue #84 v2 — discord-pepper typing-cleanup linkage (Design)

> **Status:** Drafted 2026-05-12. Approved through Section 4 by Pepper (principal-on-this-stream).
>
> **Issue:** [#84](https://github.com/jeffrichley/agent_core/issues/84) — `discord-pepper: typing indicator persists after outbound publishes when in_reply_to is unset (v2 — corrected framing)`.
>
> **Scope:** Extend `_send`'s post-send cleanup to clear `_awaiting_reply_ids` by the inbound's Discord message_id when the outbound carries bus-level `envelope.in_reply_to`. Add a 90s per-task lazy TTL safety net on `_awaiting_reply_ids` for cases where explicit cleanup doesn't fire. Two translation sites: `_deliver_text_message` (TextMessage envelope path) and `_inject_channel_id` (ToolInvocation dispatch path).

## Problem

`_typing_while_pending` (`endpoint.py:381–400`, commit `81162fe` from 2026-05-02) holds Discord's `channel.typing()` context manager open while the inbound's Discord message_id is in `_awaiting_reply_ids`. discord.py auto-refreshes the typing indicator every ~5s while the context is held. **This part works.**

The bug is the **cleanup path**. The polling loop exits only when the Discord message_id is discarded from `_awaiting_reply_ids`. Two existing cleanup mechanisms fire:

1. **`_clear_pending_ack(ch, args.reply_to)` at `endpoint.py:1283–1284`** — fires after a successful `_send` IF `args.reply_to` is set. `args.reply_to` comes from `metadata.discord.reply_to`, which is **Discord's UI threaded-reply feature** (the visual *"replying to ..."* card above an outbound). It's an optional Discord-UI affordance, not a bus contract.
2. **Buffer-overflow and TTL-sweep eviction paths** (`endpoint.py:1095, 1137`) — fire on `_pending_acks` pressure, unrelated to the agent's reply intent. Tracked separately in issue #87 as a watch-only edge case.

**Lived instance, 2026-05-12** (Jeff observed; Pepper repro'd): Pepper sent a reply via `mcp__agent-core__send(kind="TextMessage", in_reply_to=<env_id>)` without setting `metadata.discord.reply_to`. Jeff watched the typing indicator persist on Discord after Pepper's message landed. The typing-while-pending task kept running because `_clear_pending_ack` never fired — `args.reply_to` was unset.

**The root cause** is a seeding-key vs cleanup-key mismatch in the agent's contract: the bus-level linkage signal (`envelope.in_reply_to`, the universally-required field on a reply) is *not consulted* by the existing cleanup path. The Discord-UI `reply_to` flag is, but it's optional and orthogonal — many agent outbound shapes (proactive briefings, fresh-thread sends, replies-without-threaded-UI) intentionally omit it.

**The same bug exists on the ToolInvocation `send` verb path**: an agent calling `mcp__agent-core-discord__send(channel_id=..., text=..., in_reply_to=<env_id>)` with bus-level `in_reply_to` and no Discord-UI `reply_to` produces the same persistent-typing failure.

## Out of scope

- **Background sweep task for `_awaiting_reply_ids`.** Per-task lazy sweep inside `_typing_while_pending` covers all cases where the task continues running. The narrow uncovered window — `CancelledError`/daemon-shutdown — leaves a stale entry until next process restart. No observed memory-leak instance. Followup pending a named leak symptom.
- **WARNING-log on TTL eviction.** Diagnostic instrumentation pending a named diagnostic incident. TTL eviction is the *expected* behavior on cache miss / cold-start / dismissed inbounds — logging at WARNING would be noisy. The pre-implementation criterion-watch caught this (don't pre-instrument for unobserved incidents).
- **TextMessage handler unification via `_resolve_channel_id`.** Still deferred from #83's Followups #5. The Q1 design choice was deliberately (B)-at-both-sites to avoid pulling this in (the `outbound_channel_id` static-fallback risk note from #83 stands).
- **Buffer-pressure investigation** (#87, watch-only `priority:low`). `_awaiting_reply_ids` is discarded from buffer-overflow and pending-ack TTL-sweep paths, which can clear typing prematurely under load. Same trigger gate.
- **Per-verb cleanup divergence tests.** Group 2 test 9 parameterizes `edit`, `react`, `send_briefing`. Per-verb deep-coverage pending a verb-specific cleanup symptom.
- **Cache extraction to shared utility.** `_recent_inbounds` now has two consumers within `discord-pepper` (channel resolution from #83 + typing cleanup from #84). This is N=2 of *consumers-within-one-endpoint*, NOT N=3 of *endpoint-pattern repetition*. The rule-of-three extraction trigger (per #83's Followup #3) remains gated on a non-`discord-pepper` adapter needing the same shape.

## Design

### Architecture

Translate bus-level `envelope.in_reply_to` → inbound Envelope (via `_recent_inbounds` from #83) → inbound's Discord `message_id` → new `_SendArgs.cleanup_inbound_message_id` field, set at outbound construction time. `_send`'s existing post-send cleanup block extends with one parallel `if` to clear `_awaiting_reply_ids` (and its new sibling timestamps dict) by this new field. A 90s per-task lazy TTL safety net inside `_typing_while_pending` evicts orphan entries when explicit cleanup never fires.

Two translation sites:

1. **`_deliver_text_message`** — TextMessage envelope path. The site of Jeff's observed bug.
2. **`_inject_channel_id`** — ToolInvocation dispatch path (the existing #83 closure that already injects `channel_id` from the same `env` variable). Same envelope context, same cache lookup, sibling field on the args dict.

Both sites are surgical: ~3 lines each. No new modules, no new verbs, no resolver extension. The cache (`_recent_inbounds`) stays endpoint-local. `_send`'s contract grows by exactly one optional field.

### Components

**1.** `packages/agent-core-discord/src/agent_core_discord/args.py` — add field on `_SendArgs`:

```python
class _SendArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    reply_to: str | None = None
    files: list[str] | None = None
    cleanup_inbound_message_id: str | None = None  # NEW — bus-linkage cleanup
```

The field is optional with default `None`. Callers that don't set it (existing `send` verb tool args, existing TextMessage envelope path without `in_reply_to`) get the pre-existing behavior unchanged.

**2.** `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:

- **Class attribute** (near top of `DiscordEndpoint`): `_TYPING_TTL_SECONDS: float = 90.0`. Class-level (not module-level) for test injection: tests can construct an endpoint with a shorter TTL without monkeypatching the module.
- **Initial state** (around line 282 where `_awaiting_reply_ids` is declared): add `self._awaiting_reply_ids_timestamps: dict[str, float] = {}`. Sibling pair to `_awaiting_reply_ids`, same shape as `_recent_inbounds + _recent_inbounds_timestamps` from #83.
- **Six pair-management sites** (lines 782, 904, 908, 1095, 1137, 1159): every `_awaiting_reply_ids.add(mid)` pairs with `self._awaiting_reply_ids_timestamps[mid] = time.monotonic()`. Every `_awaiting_reply_ids.discard(mid)` pairs with `self._awaiting_reply_ids_timestamps.pop(mid, None)`. The `.pop(..., None)` is defensive: if pair-management ever slipped at insertion, discard doesn't crash.
- **`_typing_while_pending`** (lines 381–400): inside the polling loop, before `asyncio.sleep(0.2)`, check `time.monotonic() - self._awaiting_reply_ids_timestamps.get(message_id, 0) > self._TYPING_TTL_SECONDS`. If true: discard the id from both sets, break out of the loop. The `get(..., 0)` returns 0 for missing keys, producing a huge delta and triggering immediate eviction — **self-healing** in the unlikely case a pair-management site slipped.
- **`_deliver_text_message`** (lines 638–697): after the existing `text` / `channel_id` / `reply_to` / `embeds` extraction, add the lookup:
  ```python
  cleanup_inbound_message_id: str | None = None
  if envelope.in_reply_to:
      inbound = self._recent_inbounds.get(envelope.in_reply_to)
      if inbound:
          discord_meta = (inbound.metadata or {}).get("discord") or {}
          cleanup_inbound_message_id = discord_meta.get("message_id")
  ```
  Pass `cleanup_inbound_message_id=cleanup_inbound_message_id` into the `_SendArgs(...)` construction.
- **`_inject_channel_id`** (inside `_dispatch`): mirror the same walk against the existing `env` closure variable. After the channel-id injection, before the `return raw`:
  ```python
  if env.in_reply_to and "cleanup_inbound_message_id" not in raw:
      inbound = self._recent_inbounds.get(env.in_reply_to)
      if inbound:
          discord_meta = (inbound.metadata or {}).get("discord") or {}
          cid = discord_meta.get("message_id")
          if cid:
              raw["cleanup_inbound_message_id"] = cid
  ```
  Pydantic validates `_SendArgs` with the new field populated. No plumbing change to `_dispatch` itself — `env` is already in scope.
- **`_send`** (around line 1284, after the existing `if args.reply_to: clear_pending_ack(args.reply_to)`): add the parallel cleanup:
  ```python
  if args.cleanup_inbound_message_id:
      await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)
  ```
  `_clear_pending_ack` already does the actual discard work; it just needs to be called with the right id.

**3.** `packages/agent-core-discord/tests/` — additions in Testing section.

Three files touched in production code; ~3 lines added at each translation site; no new files in production. Test additions in existing or sibling test files.

### Data flow

**Happy-path scenario (TextMessage envelope, `in_reply_to` set, cache hit):**

```
1. Discord user sends a message in #pepper-chat.
   discord-pepper.on_message:
     mid = str(message.id)
     self._awaiting_reply_ids.add(mid)
     self._awaiting_reply_ids_timestamps[mid] = time.monotonic()   # NEW
     await self._handle.publish(envelope)
     self._record_inbound(envelope)                                 # #83 cache write
     asyncio.create_task(self._typing_while_pending(message.channel, mid))

2. Agent: mcp__agent-core__send(
     to="discord-pepper", kind="TextMessage",
     in_reply_to=<inbound_envelope_id>,                            # bus linkage
     payload={"kind": "TextMessage", "text": "..."},
     metadata={"discord": {"channel_id": "..."}})

3. Bus validates + publishes. discord-pepper.deliver() → _deliver_text_message:
     inbound = self._recent_inbounds.get(envelope.in_reply_to)
     cleanup_id = None
     if inbound:
         cleanup_id = (inbound.metadata or {}).get("discord", {}).get("message_id")
     args = _SendArgs(channel_id=..., text=..., ..., cleanup_inbound_message_id=cleanup_id)
     await self._send(args)

4. _send executes channel.send(...). After successful send:
     if args.reply_to: await self._clear_pending_ack(ch, args.reply_to)              # existing
     if args.cleanup_inbound_message_id:                                              # NEW
         await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)

5. _clear_pending_ack discards from both sets:
     self._awaiting_reply_ids.discard(mid)
     self._awaiting_reply_ids_timestamps.pop(mid, None)                              # NEW

6. _typing_while_pending polling loop: mid not in _awaiting_reply_ids → exits.
   discord.py context manager closes. Typing indicator stops on Discord.
```

**ToolInvocation `send` parallel path** (`mcp__agent-core-discord__send(channel_id=..., text=..., in_reply_to=<env_id>)`):

```
3'. _dispatch → _inject_channel_id(raw, env):
      # Existing #83 channel_id injection logic.
      if env.in_reply_to and "channel_id" not in raw:
          raw["channel_id"] = self._resolve_channel_id(env)
      # NEW typing-cleanup translation.
      if env.in_reply_to and "cleanup_inbound_message_id" not in raw:
          inbound = self._recent_inbounds.get(env.in_reply_to)
          if inbound:
              cid = (inbound.metadata or {}).get("discord", {}).get("message_id")
              if cid:
                  raw["cleanup_inbound_message_id"] = cid
      return raw
    → pydantic validates _SendArgs with new field populated.
    → _send (step 4 above) handles cleanup uniformly.
```

**Cache-miss / no-linkage scenarios** (all converge to TTL safety net):

| Scenario | `cleanup_inbound_message_id` | Cleanup mechanism |
|---|---|---|
| Cache hit (happy path) | Set to inbound's Discord message_id | Step 4–5 explicit clear |
| Cache miss (TTL eviction, cold-start, never-recorded) | `None` | 90s lazy TTL in `_typing_while_pending` |
| Outbound has no `in_reply_to` set | `None` (translation skipped) | 90s lazy TTL |
| Inbound `metadata.discord.message_id` missing (defensive) | `None` | 90s lazy TTL |
| Agent dismisses inbound via `handle()`, no outbound at all | (no outbound → no clear) | 90s lazy TTL |

The TTL is the **complete** safety net — every path where explicit cleanup doesn't fire ends in lazy TTL eviction within 90 seconds. Each scenario's behavior is locked in Section 3's Testing.

### Error handling

| Failure | Where it surfaces | Severity |
|---|---|---|
| `_recent_inbounds.get()` returns `None` (cache miss) | `cleanup_id` stays `None`; explicit clear no-ops; TTL safety net handles | No-op; degrades to TTL |
| `inbound.metadata.discord.message_id` missing | Same path; `cleanup_id` stays `None`; TTL safety net | No-op; degrades to TTL |
| `_clear_pending_ack` raises mid-cleanup | Existing exception path (`_send`'s try/except) catches; logs; continues. Pre-existing behavior, unchanged | Logged; no Ack impact |
| TTL eviction fires while reply is mid-flight (>90s composes) | `_typing_while_pending` exits early; typing stops slightly before reply lands. **Strictly better than the bug** (typing stops a few seconds early vs. typing persists indefinitely after the reply) | Cosmetic |
| Pair-management slips (add without timestamp set) | `get(mid, 0)` returns 0; `current_time - 0` is a huge delta; immediate eviction on next poll. **Self-heals** | Self-correcting |

**No new urgency tier, no new ack shape, no metric/counter, no config knob.** The failure-mode space converges either on the explicit-cleanup path (happy path) or the TTL safety net (degraded paths). The TTL safety net's worst case is a 90s window of stale tracking — bounded, observable in tests, and strictly better than the persisting-stale bug being fixed.

#### Pre-existing semantics inherited

The wire-up inherits these properties from `_typing_while_pending`'s pre-existing behavior:

- **Polling interval:** 200ms (`asyncio.sleep(0.2)` between checks). Fine-grained enough that cleanup latency is sub-second.
- **Typing-context lifecycle:** discord.py's `channel.typing()` context manager auto-refreshes every ~5s. The polling loop holds it open; closing the context cancels discord.py's refresh task.
- **`_typing_while_pending` exception handling:** existing `try/except` catches `CancelledError` (re-raised), other exceptions (logged at DEBUG). Pre-existing; unchanged.
- **Multiple inbounds in the same channel:** each gets its own `_awaiting_reply_ids` entry + its own polling task. Cleanup is per-message_id, so unrelated inbounds aren't affected.

### Security considerations

No new surface. The cleanup linkage operates entirely on internal state (`_awaiting_reply_ids`, `_awaiting_reply_ids_timestamps`, `_recent_inbounds`). No new agent-controllable knob, no path-resolution surface, no escalation path.

## Testing

Thirteen tests across four groups. Time-mocking pattern lifted from #83's `_recent_inbounds` TTL tests: manually set timestamps to `time.monotonic() - <delta>` rather than `asyncio.sleep`-ing, so the suite stays fast and deterministic.

### Group 1: Pair-management discipline (4 tests)

Locks the *every-add-pairs-every-discard* contract at each of the six call sites. Helper-converges-three pattern: lines 1095, 1137, 1159 all go through `_clear_pending_ack`, so one test on the helper covers all three.

- `test_awaiting_reply_id_and_timestamp_seeded_together_on_message` — line 904 (main seeding site). After `on_message` fires, both `mid in _awaiting_reply_ids` AND `mid in _awaiting_reply_ids_timestamps`.
- `test_clear_pending_ack_clears_both_sets` — covers lines 1095, 1137, 1159. Verifies the helper discards from both structures.
- `test_awaiting_reply_id_cleared_on_publish_rollback` — line 908. Force `_handle.publish` to raise; verify both sets clear in the `except` block.
- `test_awaiting_reply_id_cleared_on_endpoint_stop` — line 782. Call `stop()` with an entry pending; verify both sets clear.

### Group 2: Cleanup wiring — the main fix (5 tests)

- `test_text_message_envelope_with_in_reply_to_clears_typing` — **the #84 named-symptom regression lock.** Seed `_awaiting_reply_ids` (simulate prior inbound); send `mcp__agent-core__send(kind="TextMessage", in_reply_to=<env_id>)`; assert `_awaiting_reply_ids` and `_awaiting_reply_ids_timestamps` both cleared after the send completes. **If this test ever flakes or fails, the bug has returned.**
- `test_text_message_envelope_without_in_reply_to_does_not_clear_typing` — no linkage; cleanup no-ops; `_awaiting_reply_ids` retains the inbound's mid (TTL safety net is what eventually clears it; tested in Group 3).
- `test_text_message_envelope_with_cache_miss_does_not_clear_typing` — evict the inbound from `_recent_inbounds` before sending; verify cleanup no-ops cleanly (no crash on missing-cache).
- `test_tool_invocation_send_with_in_reply_to_clears_typing` — parallel path; `mcp__agent-core-discord__send(channel_id=..., text=..., in_reply_to=<env_id>)` clears typing via `_inject_channel_id`'s translation.
- `test_tool_invocation_verbs_clear_typing_via_in_reply_to` — parameterized across `edit`, `react`, `send_briefing`. Same cache-walk via `_inject_channel_id`, same `_send` cleanup block.

### Group 3: TTL safety net (3 tests)

- `test_typing_evicts_after_ttl_when_no_cleanup_fires` — set timestamp to `time.monotonic() - 100`; run one poll tick; verify `_awaiting_reply_ids.discard(mid)` AND `_awaiting_reply_ids_timestamps.pop(mid)` both fire; `_typing_while_pending` exits cleanly.
- `test_typing_does_not_evict_within_ttl_window` — set timestamp to `time.monotonic() - 10`; verify polling loop keeps running; `mid` still in both sets.
- `test_typing_evicts_immediately_on_missing_timestamp_self_heal` — the self-healing property: add `mid` to `_awaiting_reply_ids` WITHOUT setting timestamp (simulate pair-management slip); first poll tick uses `get(mid, 0)`; `current_time - 0` = huge delta > 90; immediate eviction. **Locks the architectural property that pair-management slips are recoverable, not load-bearing.**

### Group 4: Regression lock (1 test)

- `test_existing_clear_pending_ack_via_args_reply_to_path_unchanged` — the pre-existing `if args.reply_to: _clear_pending_ack(args.reply_to)` path still works when `cleanup_inbound_message_id` is `None`. Locks backward-compat for Discord-UI threaded-reply outbounds that don't carry bus-level `in_reply_to`. **Canary: should pass green-first if step 6 is implemented correctly; if it fails, the `args.reply_to` path has drifted (diagnostic information, not a #84 bug).**

### Out-of-scope test classes (explicitly considered, decided against)

- **Missing `inbound.metadata.discord.message_id`** — defensive shape, no named instance. The `(inbound.metadata or {}).get("discord", {}).get("message_id")` chain handles it cleanly via `None` propagation. Same restraint as #64's no-test-for-pre-existing-semantics-inherited.
- **Proactive outbound (no `in_reply_to`, fresh-thread send)** — no `_awaiting_reply_ids` entry was ever created (no inbound triggered it). Test for "nothing happens when nothing should happen" is YAGNI.
- **Per-verb deep coverage beyond parameterize-three.** Group 2 test 9's parameterization across `edit`, `react`, `send_briefing` covers the verb surface. Per-verb deep tests pending verb-specific symptoms.
- **Both `args.reply_to` AND `cleanup_inbound_message_id` set, same id.** `discard` is idempotent — calling it twice with the same id is a no-op. Test for "no harm done in the dual-clear case" is the same shape as defensive-no-test discipline.
- **WARNING-log assertion on TTL eviction.** Skipped per Section 2's decision (no diagnostic instrumentation pending a named incident).

The restraint here is the criterion-watch firing bilaterally: speculative coverage on either side of the contract is the same anti-pattern. Thirteen tests tied to either a named symptom (Group 2 test 5), a named contract (Group 1 pair-management, Group 3 TTL), or a documented mode (Group 4 backward-compat) is the right scope.

## Followups (out of scope for #84 v2)

Tracked for separate tickets, each named-trigger-bound:

1. **WARNING-log on TTL eviction.** **Trigger:** an observed typing-related diagnostic incident where the polling-loop eviction's invisibility costs time-to-diagnose.
2. **Background sweep task for `_awaiting_reply_ids`.** Currently per-task lazy sweep. The `CancelledError`/daemon-shutdown window leaves a stale entry until next process restart. **Trigger:** an observed `_awaiting_reply_ids` memory leak instance under load.
3. **TextMessage handler unification via `_resolve_channel_id`.** Still deferred from #83's Followups #5. #84 explicitly avoided pulling this in (Q1 push-back on (C) placement). **Trigger:** unchanged from #83 — a named symptom on the TextMessage handler that the unification would resolve, with the `outbound_channel_id` static-fallback risk note acknowledged.
4. **Buffer-pressure investigation.** Issue #87, watch-only `priority:low`. **Trigger:** observation of typing dropping mid-compose with buffer-pressure context.
5. **Per-verb cleanup divergence tests.** **Trigger:** verb-specific cleanup divergence symptom.

## Implementation order

TDD, bite-sized per `writing-plans`:

1. **Schema first** — add `cleanup_inbound_message_id: str | None = None` to `_SendArgs` in `args.py`. No callers yet.
2. **Pair-management infrastructure** — add `_awaiting_reply_ids_timestamps` attribute; update the six call sites (lines 782, 904, 908, 1095, 1137, 1159) to pair `add`/`discard` with timestamp `set`/`pop`. Write Group 1 tests (4 tests). Red → green.
3. **TTL polling-loop check + self-heal** — add `_TYPING_TTL_SECONDS = 90.0` class attribute. Extend `_typing_while_pending`'s loop with the TTL check + self-healing `get(mid, 0)` pattern. Write Group 3 tests (3 tests). Red → green.
4. **Translation at `_deliver_text_message`** — add the `_recent_inbounds.get(envelope.in_reply_to)` walk; pass `cleanup_inbound_message_id` to `_SendArgs`. Write Group 2 tests 5–7 (TextMessage envelope paths). Red → green.
5. **Translation at `_inject_channel_id`** — add the same walk against the existing `env` closure. Write Group 2 tests 8–9 (ToolInvocation paths). Red → green.
6. **`_send` cleanup block extension** — add `if args.cleanup_inbound_message_id: await self._clear_pending_ack(...)`. Group 2 tests now pass green.
7. **Regression lock** — Group 4 test (1 test). **Should pass green-first** if step 6 is implemented correctly. If it fails: diagnostic information that the `args.reply_to` path has drifted, not a #84 bug to fix.
8. **Full gate** — `just check` green: lint, mypy, contracts, full test suite. **Run `pytest packages -q` (full repo, not just `agent-core-discord/tests`)** per the broader-suite-check lesson from #64. `_SendArgs` is shared; blast radius isn't confined to one package.
9. **End-of-ticket Pepper ping** before push, per the working norm.
10. **PR + merge** per Jeff's standing authorization for high-priority work this cycle.

## Open questions for spec review

None. Every design decision resolved through Q1 (placement: (B) at both sites), Q2 (TTL implementation: (α) parallel timestamps dict + per-task lazy sweep), and Sections 1–4 refinements.
