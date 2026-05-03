# Notification surfaces — what the agent sees, when

Every event the bus delivers to an agent must land on a surface the running agent can perceive in-session. "It writes to a log file" is not a surface — the agent cannot tail logs from inside its own conversation. This doc is the contract: for each event type the bus emits, the surface it appears on, and what the agent is expected to do with it.

This is the artifact Cutover **#08** produces.

---

## The shared perception path

All four bullet-points below land via the same pipeline:

```
publisher (daemon / endpoint / hook)
    -> Bus.publish()
    -> ClaudeCodeMCPEndpoint.deliver()
    -> _pending queue (in-process inbox)
    -> _notify_mail_arrived(urgency)
    -> JSON-RPC notification: notifications/claude/channel
       (debounced by urgency: red 0.05s, yellow 0.5s, green 1.0s)
    -> Claude Code receives via:
         - HTTP/streamable MCP: directly, or
         - stdio relay (agent-core-channel → notifications/claude/channel)
```

`ClaudeCodeMCPEndpoint.deliver()` is **envelope-kind-agnostic** — it queues `TextMessage`, `Event`, `ToolInvocation`, `Cancellation`, `Progress`, and (non-routine) `Acknowledgment` envelopes the same way. The agent then calls `list_pending` (an MCP tool) to see specifics, including `payload.type` for `Event` envelopes.

> **Carve-out — routine green Acknowledgments are auto-handled and do NOT wake the agent.** When an inbound envelope matches the routine-green-ack profile (`kind="Acknowledgment"`, `urgency="green"`, references a recent outbound from this agent, no `error:` note), `deliver()` acks it in persistence and returns without queueing or pushing — see `_is_routine_green_ack`. This lets bridges confirm delivery without burning a wake. The carve-out is intentional and tested.

**Tests covering the path:** `packages/core/tests/test_notify_mail_arrived.py`, `test_notify_broker.py`, `test_claude_code_mcp.py`, `test_handoff_enqueue_integration.py`, `test_handoff_jobs_endpoint.py`.

---

## Surface mapping

| Event | Publisher | Envelope shape | Surface | Agent's expected behavior |
|---|---|---|---|---|
| **Inbound Discord message** | `DiscordEndpoint.on_message` | `kind="TextMessage"`, `to=<agent>`, `urgency` per traffic | `notifications/claude/channel` summary → `list_pending` shows the message | Read it; reply or acknowledge as appropriate |
| **Discord reaction (`discord.reaction_add`)** | `DiscordEndpoint.on_reaction_add` | `kind="Event"`, `payload.type="discord.reaction_add"`, `data` carries `emoji`, `channel_id`, `message_id`, `guild_id`, `user_id`, `user_display_name` (bot's own reactions and the configured ack emoji are filtered out) | Same channel summary → `list_pending` shows `Event/discord.reaction_add` | Treat as user feedback on a prior reply; act or acknowledge per agent policy |
| **Continuity ready (`HandoffReady`)** | `HandoffJobsEndpoint._publish_result` | `kind="Event"`, `payload.type="HandoffReady"`, `data` carries `job_id`, `session_id`, `handoff_path`, `handoff_status_path`, `content_sha256`, `to=<agent>` (urgency: green default — `_publish_result` does not override) | Same channel summary → `list_pending` shows `Event/HandoffReady` with `data.handoff_path` | Read the new `handoff.md` (path is in `data.handoff_path`); merge continuity into working context; `ack` the envelope |
| **Continuity failed (`HandoffFailed`)** | `HandoffJobsEndpoint._publish_result` | `kind="Event"`, `payload.type="HandoffFailed"`, `data` carries `job_id`, `session_id`, `handoff_path`, `error`, `to=<agent>` | Same channel summary → `list_pending` shows `Event/HandoffFailed` with `data.error` | Note the failure; if a prior `handoff.md` exists, it is the last-known-good (per Cutover #02 placeholder); fall back to MEMORY.md / dailies; `ack` |
| **Scheduler trigger** | `SchedulerEndpoint._fire` | `kind="TextMessage"`, `payload.text=<configured prompt>`, `metadata.scheduler_job=<job_name>` | Same channel summary → `list_pending` shows the prompt | Treat as a wake — execute the scheduled action |
| **Notify-broker fan-out** | Any publisher targeting an agent with multiple subscribers (e.g., one HTTP MCP session and one stdio relay) | Original envelope; broker fans out the *summary* event, not the envelope | Each subscriber receives the same `notifications/claude/channel` push; `list_pending` is authoritative for what specifically arrived | Same as the underlying envelope's behavior — fan-out is transport-level, not semantic |

---

## What "the agent perceives" requires

Three invariants. If any of these stops holding, the surface is broken.

1. **`deliver()` is kind-agnostic.** Every envelope queues into `_pending` and triggers `_notify_mail_arrived` (modulo the routine-green-ack auto-handle path). Tested in `test_deliver_kind_agnostic_pushes_for_handoff_ready_event_envelope` — which exercises the real `deliver()` entry point with a `kind="Event"` envelope.
2. **`_envelope_to_dict` round-trips `EventPayload`.** Pydantic `model_dump()` includes `kind`, `type`, `schema_version`, and `data` — so `list_pending` shows the agent what specifically arrived. Tested in `test_list_pending_surfaces_event_payload_type_and_data`.
3. **Mixed traffic surfaces uniformly.** Discord and Event envelopes coexist in `_pending` and produce one consolidated channel notification. Tested in `test_mixed_event_and_text_envelopes_surface_together`.

---

## Out of scope (deferred to follow-on tickets)

- **Custom JSON-RPC notification methods per event type** (e.g., `notifications/claude/handoff_ready`). Considered and rejected for #08 on the grounds that the inbox-shaped surface already works and adding type-specific methods grows the channel-relay surface area. Revisit only if real use shows the agent reliably failing to act on inbox events.
- **Push retries when the MCP session is offline.** Currently a slow consumer drops events with a WARN log (see `NotificationBroker.publish`). The channel-relay design treats `list_pending` as authoritative — missing one push is recoverable on the next poll. If a stronger guarantee is needed (e.g., critical alerts), it is its own ticket.
- **Agent-side autonomous action on event arrival** (e.g., automatically reading `handoff.md` when `HandoffReady` lands). That is the agent's policy decision, not a framework concern. The surface delivers the event; what the agent does with it lives in agent prompts / CLAUDE.md / skills.
