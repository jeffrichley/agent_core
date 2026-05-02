# Issue 12 — Auto-handle routine Acknowledgments; wake only on failures or timeouts

**Date:** 2026-05-02  
**Status:** Draft (spec-first; implementation follows approval)  
**GitHub:** [jeffrichley/agent_core#12](https://github.com/jeffrichley/agent_core/issues/12)  
**Related:** [issue #14](https://github.com/jeffrichley/agent_core/issues/14) (burst / coalesce wakes — orthogonal; may stack later)  
**Depends on:** [`2026-04-29-responsive-inbox-design.md`](2026-04-29-responsive-inbox-design.md) (push path, urgency, `ClaudeCodeMCPEndpoint`)

---

## TL;DR

Keep every `Acknowledgment` envelope on the bus (delivery receipts stay). Change **`ClaudeCodeMCPEndpoint`** so routine **green** acks for the agent’s **own recent outbounds** are **auto-handled** (same persistence outcome as `handle()`, no channel wake). Still **wake** the agent for yellow/red acks, failure semantics, non-ack kinds, and when an outbound waits **too long** for any ack (**missing-ack alarm**). Add an **opt-in** to wake on every green ack for debugging.

Design phrase: *keep the receipt, drop the doorbell.*

---

## Problem

Each successful outbound to a bridge (e.g. Discord) yields a green `Acknowledgment` back to the agent. Today `deliver()` always queues the envelope and calls `_notify_mail_arrived()`, so the agent gets a **`notifications/claude/channel`** wake, reads the mailbox, and `handle()`s an envelope that adds no new decision — doubling wakes per conversational turn and spending context on noise.

---

## Goals

1. **Preserve** the ack envelope on the wire and in persistence (no silent drops).
2. **Auto-handle** eligible green acks on the agent’s behalf: remove from in-memory pickup queue and call `BusHandle.ack()` for the **ack envelope’s id**, without scheduling `_notify_mail_arrived()`.
3. **Wake** when something is actionable or risky: non-green urgency, non-`Acknowledgment` kind, acks that are not “routine green delivery receipts,” or **missing-ack** timeout.
4. **Inspectability:** auto-handled acks remain **queryable** the same way any acked envelope is (SQLite `state='acked'` rows; no separate shadow store required for v1).
5. **Operator / developer escape hatch:** per-endpoint (or env) flag to **force wakes** on all acks for debugging.

---

## Non-goals

- Removing or batching `Acknowledgment` envelopes on the bus.
- Changing Discord’s `_reply()` ack shape for v1 (today `in_reply_to` and `AcknowledgmentPayload.of` both reference the **delivered** envelope id — the agent’s outbound `TextMessage` id for chat replies).
- Implementing issue #14 in this spec (debounce across mixed envelope types may interact; implementation order should run tests together if both land close together).

---

## Definitions

| Term | Meaning |
|------|--------|
| **Outbound id** | `Envelope.id` of a message **published** by this agent endpoint via the MCP `send` tool (bus-stamped `from_` = agent name). |
| **Routine green ack** | `kind == "Acknowledgment"`, `urgency == "green"`, and `in_reply_to` equals an **outbound id** still in the endpoint’s **recent-outbound registry** (see below). |
| **Failure ack** | Not routine: `urgency` in `yellow` / `red`, or `AcknowledgmentPayload.note` / metadata matches an implementation-defined “error path” pattern (e.g. Discord `_reply` uses `error:` prefix in JSON string — treat as wake-worthy even if urgency stayed green unless spec tightened). **Spec decision:** any ack whose `note` starts with `error:` (case-sensitive as today) is **never** auto-handled; always wake. |

---

## Behavior

### 1. Recent-outbound registry

- On successful **`send`** tool completion (after `publish` returns), record `outbound_id` + `created_at` (monotonic clock) in an in-memory structure scoped to this `ClaudeCodeMCPEndpoint` instance.
- **Eviction:** remove ids when (a) a matching ack is auto-handled or manually handled, or (b) **max age** exceeded. **Default max age:** 15 minutes (covers slow bridges; tunable via daemon config key, e.g. `agent_core.auto_ack_outbound_ttl_seconds`).
- **Capacity:** bounded LRU or max count (e.g. 10k ids) with oldest evicted first to avoid memory leaks on long-lived agents.

### 2. Auto-handle path (`deliver`)

When an envelope arrives for the agent:

1. If **debug: wake all acks** flag is set → existing behavior (queue + notify).
2. Else if envelope is a **routine green ack** (definitions above) **and** `AcknowledgmentPayload.of == envelope.in_reply_to` (sanity check against malformed acks) **and** `in_reply_to` is in the recent-outbound registry:
   - `queue_for_pickup` **must not** retain this envelope (either skip queue entirely, or enqueue then immediately process — **spec prefers:** never append to `_pending` for auto-handled acks to avoid races with `list_pending`).
   - Call `await self._handle.ack(envelope.id)` so persistence matches today’s manual `handle()`.
   - Remove `in_reply_to` from recent-outbound registry and cancel any **missing-ack timer** for that id.
   - **Do not** call `_notify_mail_arrived()`.
3. Else → today’s path: `queue_for_pickup` + `_notify_mail_arrived(urgency)` + `EndpointUnavailable` if no session (unchanged).

### 3. Wake path (explicit)

Always use the existing debounced notify path when **any** of:

- Envelope `kind != "Acknowledgment"`.
- `Acknowledgment` with `urgency` in `yellow` or `red`.
- `Acknowledgment` classified as **failure ack** (see Definitions).
- Green ack but `in_reply_to` **not** in registry (another endpoint’s ack pattern, or evicted outbound).
- **Debug** flag enabled.

### 4. Missing-ack alarm

- When recording an outbound id, schedule a **timer** (default **30s**, tunable globally `agent_core.missing_ack_seconds` and optionally per-`send` via envelope metadata, e.g. `metadata.agent_core.ack_timeout_seconds` as integer override).
- If the timer fires **before** any ack (auto or manual) clears that outbound id:
  - Remove outbound id from “pending ack” set (or mark as `alarmed` to avoid duplicate fires).
  - Call `_notify_mail_arrived()` with urgency **yellow** (or **red** if outbound was originally red — typically green for chat); summary should still come from `_build_summary()` but the daemon **may** append a meta hint (string-safe) such as `meta.missing_ack_outbound_id` listing one id (channel meta must stay `Record<string, string>` per `_coerce_channel_meta` — use JSON string or a dedicated key agreed in implementation).
- If ack arrives before fire: cancel timer.

### 5. Opt-in debugging

- **Config surface:** constructor argument on `ClaudeCodeMCPEndpoint`, e.g. `wake_on_all_acknowledgments: bool = False`, wired from daemon YAML / env (`AGENT_CORE_WAKE_ON_ALL_ACKS=1`) in the runner that constructs endpoints.
- When `True`, disable auto-handle and missing-ack suppression for acks (restore current behavior).

### 6. MCP instructions

Update FastMCP `instructions` string so agents know:

- Routine delivery acks may disappear from pickup without a wake; use operational access to persistence or future tooling if they need historical `message_id` from notes.
- They will still be woken on failures, urgent acks, other kinds, and missing-ack.

---

## Persistence and TTL (inspectability)

- Auto-handle uses the same **`ack()`** path as manual `handle()`: row moves to `acked` in SQLite. **No TTL deletion** in v1 beyond whatever existing retention does (none in core today — ops handle DB size).
- **Open question from issue (resolved for v1):** retention for inspection equals **acked row lifetime** in the store; document for operators. Optional later: read-only `list_acked` MCP tool — **out of scope** unless needed for Pepper.

---

## Testing

Add / extend tests under `packages/core/tests/`:

1. **Auto-handle:** publish outbound via agent `send` (or inject registry + deliver ack) → assert no `notifications/claude/channel` / broker publish for that ack path (spy existing patterns in `test_notify_mail_arrived.py`).
2. **Wake on yellow/red** ack: unchanged wake count.
3. **Wake on error note:** ack with `note` starting `error:` → wake.
4. **Missing-ack:** outbound recorded, no ack, advance clock → one wake with meta hint (async test with small timeout).
5. **Debug flag:** forces wake on green routine ack.
6. **Malformed ack:** `of != in_reply_to` → wake (do not auto-handle).

Integration with Discord package tests optional in v1 if core unit tests cover endpoint logic with stub bus.

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Race: ack arrives before `send` returns to client | Registry write **before** `publish` awaits dispatch completion, or register synchronously before returning `send` result — implementation must ensure id is registered before any ack can be delivered. |
| Duplicate wakes with #14 | Coordinate in implementation PRs; debounce already merges summaries — missing-ack should not double-fire with normal arrivals if timer cancelled correctly. |
| Agent confusion | Instructions + CHANGELOG entry. |

---

## Rollout

1. Land this spec (committed on feature branch).
2. Implement on `fix/issue-12-auto-ack-wakeup` with tests; feature-flag config default **on** (new behavior) after beta validation, or default **off** if safer — **recommend default on** for chat agents once tests pass, matching issue intent.

---

## Spec self-review (2026-05-02)

- No `TBD` left; open items are explicitly “optional later.”
- `in_reply_to` vs Discord `_reply(incoming)` aligned: `incoming` is the outbound job envelope, so `in_reply_to` equals agent outbound id for Discord-delivered TextMessages.
- Scope is one implementation plan: `ClaudeCodeMCPEndpoint` + tests + runner wiring + docs/instructions.
