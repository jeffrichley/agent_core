# Cutover #08 — Notification surface (test playbook)

**Spec:** [`docs/requirements/pepper-cutover-08-notification-surface.md`](../../requirements/pepper-cutover-08-notification-surface.md)
**Surface mapping doc:** [`docs/cutover/notification-surfaces.md`](../notification-surfaces.md)
**Closes the perception side of:** [Cutover #02 scenario (b)](../../requirements/pepper-cutover-02-handoff-observability.md)
**Implementation commits:**
- (this ticket — to be filled in when committed)
- Pre-existing infrastructure: PR #9 responsive-inbox + channel-relay; PR #11 type registry + MCP channel fix; commit `028ddcb` (HandoffReady published as `EventPayload`).

## What was implemented

No new framework code. The deliver path was already kind-agnostic; cutover #08 work is **verify + document + lock-in tests**:

1. New tests in `packages/core/tests/test_notify_mail_arrived.py` confirm `Event`-kind envelopes (e.g., `HandoffReady`, `HandoffFailed`) traverse the same `notifications/claude/channel` perception path as `TextMessage` envelopes:
   - `test_deliver_kind_agnostic_pushes_for_handoff_ready_event_envelope` — calls real `deliver()` with `kind="Event"` and verifies the JSON-RPC push fires
   - `test_list_pending_surfaces_event_payload_type_and_data` — `_envelope_to_dict` round-trips full `EventPayload` shape (`kind`, `type`, `schema_version`, `data`)
   - `test_mixed_event_and_text_envelopes_surface_together` — Discord `TextMessage` and bus `Event` envelopes coexist in one consolidated push with grouped sender counts
2. New doc [`docs/cutover/notification-surfaces.md`](../notification-surfaces.md) lists every event type, its publisher, envelope shape, surface, and the agent's expected behavior. Acts as the source of truth for "if X happens, the agent sees it via Y."

## Acceptance criteria (from spec §"Done looks like")

1. Each bus event type has a documented surface where it appears for the running agent — the surface-mapping doc is that source of truth.
2. Smoke tests fire one of each event type and the agent visibly reacts on the next turn:
   - Continuity-ready (Cutover #02 case b)
   - Channel-relay incoming Discord message
   - Scheduler trigger (e.g., heartbeat)
   - Notify-broker fan-out (multi-subscriber)
3. Surfaces are consistent across event types — Pepper does not need a different perception model for each kind.

## Verification steps (end-of-cutover)

### Step 1 — Automated checks for kind-agnostic perception

```powershell
cd E:\workspaces\ai\agents\agent_core\packages\core
uv run pytest tests/test_notify_mail_arrived.py tests/test_notify_broker.py `
              tests/test_notify_route.py tests/test_notify_broker_publish_hook.py `
              tests/test_claude_code_mcp.py -v
```

**Expected:** all green. Confirms `deliver()` queues every envelope kind, `_envelope_to_dict` serializes `EventPayload` via `model_dump()`, mixed traffic groups under one notification.

### Step 2 — Bus-tail observation of all four event types

With the bus daemon running and Pepper connected via Claude Code:

```powershell
# In a separate terminal, observe drops (issue #16 will add a richer bus tail):
uv run agent-core bus dlq list --config <cfg>     # for any drops
# Or inspect _pending directly via the list_pending MCP tool from Pepper.
```

#### Step 2a — Continuity-ready (HandoffReady)

1. End a Pepper session so SessionEnd hook fires + daemon completes.
2. Watch for `Event/HandoffReady` to appear in Pepper's next `list_pending` call.
3. **Test:** Pepper sees the event in her inbox; reads the newly-written `handoff.md` (path comes from `data.handoff_path`); acks the envelope.

#### Step 2b — Channel-relay incoming Discord message

1. Send a message to Pepper's Discord guild on a relayed channel.
2. **Test:** Pepper receives a `notifications/claude/channel` push within ~1 second (debounce); `list_pending` shows the `TextMessage` envelope; she replies.

#### Step 2c — Scheduler trigger

1. Have Pepper (or any tool client) call the scheduler endpoint's `create` ToolInvocation with a near-future trigger — e.g., a one-shot at `now + ~30s`, or an interval of `seconds=10`. (`SchedulerEndpoint` has no force-fire CLI today; the supported entry point is the `create`/`update` tool invocations defined in `packages/core/src/agent_core/endpoints/scheduler.py`.)
2. Wait for `_fire` to publish the envelope (it builds `kind="TextMessage"`, `payload.text=<configured prompt>`, `metadata.scheduler_job=<name>` — see `scheduler.py:_fire`).
3. **Test:** Pepper receives the wake; `list_pending` shows the scheduled `TextMessage` prompt; she acts on it.

#### Step 2d — Notify-broker fan-out

1. Connect two subscribers to the same agent:
   - **Subscriber A:** Pepper's normal Claude Code session (HTTP MCP — registers via `_register_session` when she connects).
   - **Subscriber B:** start `uv run agent-core-channel --agent <name> --daemon-url http://127.0.0.1:8789` in a separate terminal — this opens a stdio MCP session that subscribes to the broker. (Port 8789 matches the daemon's operational config; the channel-relay CLI's own default is 8788, so the explicit `--daemon-url` is required.)
2. Publish any envelope to that agent (e.g., reuse Step 2a or 2b).
3. **Test:** both subscribers receive the `notifications/claude/channel` push within the same debounce window. Confirms `NotificationBroker.publish` fans out correctly to all subscribed queues.

### Step 3 — Failure modes

#### Step 3a — Slow consumer

1. Configure a subscriber whose MCP session is slow / blocked.
2. Publish more than `_DEFAULT_QUEUE_MAX` events rapidly.
3. **Test:** `NotificationBroker` logs a `dropped event for <agent> (slow consumer)` warning. The fast consumer still receives all events. `list_pending` is authoritative for what specifically arrived — recovery is "agent calls `list_pending` after the next push it does receive."

#### Step 3b — No session connected

1. Disconnect Pepper's Claude Code session.
2. Publish an envelope.
3. **Test:** `deliver()` raises `EndpointUnavailable`; bus queues for redelivery; on session reconnect, the envelope is delivered + perceived. (Routine green acks bypass this and are auto-handled in persistence.)

## Pass/fail summary

| Check | Pass when |
|---|---|
| Step 1 | All notify + mcp tests green. |
| Step 2a | HandoffReady visible in `list_pending`; agent reads `handoff.md` autonomously. |
| Step 2b | Discord message arrives at agent within ~1 second. |
| Step 2c | Scheduler-fired wake reaches the agent on schedule. |
| Step 2d | Both subscribers receive the same notification push. |
| Step 3a | Slow consumer drops events with a WARN; `list_pending` recovers. |
| Step 3b | Disconnected session → bus retries; reconnect surfaces queued envelopes. |

## Known limitations (recorded; not blocking #08 done)

- **No type-specific JSON-RPC methods.** The framework deliberately surfaces all events via the generic `notifications/claude/channel` push; the agent uses `list_pending` to learn the specifics. Revisit only if real use shows reliable agent failure to act on inbox events.
- **Slow-consumer behavior is drop-with-WARN, not retry.** Acceptable because `list_pending` is authoritative — missing one push is recoverable on the next poll. If a stronger guarantee is needed for critical events, that is its own ticket.
- **Issue #16 (read-only bus tail / audit feed) is open.** Not blocking #08 — would make Step 2 verification easier when running on a live machine.
