# agent_core Backlog

Items deferred from approved designs. Each entry lists the originating spec
and the trigger condition for picking it up. The list is intentionally
narrow — items only earn a slot here if a design has already considered and
deferred them.

---

## Bus security — Phase 2 (hook-shaped)

These are opt-in hooks that layer on top of the v1 bus via the existing
`pre_publish` / `pre_deliver` pipeline stages. None of them require changes
to the bus core, the `Endpoint` Protocol, or the persistence layer.

### ACL hook — `agent_core.bus_hooks.acl.AccessControlList`

Per-`from` / `to` allow / deny rules read from `agent_core.yaml`.
Default-deny vs default-allow configurable. Rejected envelopes either
drop silently or raise (caller's choice via param).

- **Source:** Channel bus design § 7
- **Trigger:** First time we register an endpoint that should not be
  reachable from every other endpoint (e.g., a financial-ops agent, an
  admin endpoint, a sensitive vault writer).

### Redaction hook — `agent_core.bus_hooks.redact.PatternRedactor`

Regex-based redaction of envelope payloads before persistence. Keeps
`bus.sqlite` and the audit trail clean of API keys, tokens, and similar
high-blast-radius strings.

- **Source:** Channel bus design § 7
- **Trigger:** First envelope flow that could plausibly carry a secret —
  likely webhook payloads, Discord paste-ins, or any inbound channel that
  can carry user-supplied free text.

### Rate-limit hook

Per-endpoint or per-(`from`, `to`) throttling at the `pre_deliver` stage.
Prevents one endpoint from saturating another (whether through bug, abuse,
or feedback loop).

- **Source:** Channel bus design § 7
- **Trigger:** First observed runaway producer or amplification loop in
  production traffic.

---

## Bus security — Future (revisit when threat emerges)

These are deliberately deferred indefinitely. Each lists the specific
condition that should cause us to revisit. Implementing without that
condition is over-engineering.

### Encryption at rest for `bus.sqlite`

OS-level full-disk encryption is the layer that handles this today.

- **Trigger:** Deploying agent_core somewhere OS-level full-disk
  encryption is unavailable, or storing payloads regulated under a regime
  that mandates application-layer encryption.

### Auth (tokens or mTLS) for the MCP HTTP host

Not needed while `bus.http.bind_host` is loopback. The runner refuses
to start with a non-loopback bind unless an auth scheme is configured.

- **Trigger (token auth):** First time `bind_host` needs to be
  non-loopback — for example, exposing the MCP host on a LAN IP so a
  Claude Code instance on a different machine can connect, or running
  agent_core in a container with a forwarded port.
- **Trigger (mTLS):** When the bus federates across hosts, or when an
  organization-level policy requires mutual auth on internal services.

### Signed envelopes

Cryptographic provenance beyond bus-stamped `from:`. Useful only when the
process boundary is no longer the trust boundary.

- **Trigger:** Cross-trust-domain agent comms — e.g., bus-to-bus across
  different organizations, or accepting envelopes from processes outside
  the local trust domain.

### Sandboxing of endpoint adapters

Endpoints are trusted Python imports. Sandboxing would isolate them from
the host process.

- **Trigger:** Running third-party or untrusted endpoint code, or wanting
  to constrain blast radius of a compromised adapter.

### Per-endpoint end-to-end encryption keys

End-to-end secrecy between specific endpoints, opaque to the bus and to
hooks.

- **Trigger:** Agents running in genuinely separate trust domains that
  need private channels the bus operator cannot read.

### Anomaly detection / behavioral monitoring

Pattern detection on the audit trail — unusual publish rates, off-hours
activity, sudden topology changes, etc. The audit trail enables this; the
detector is a separate project.

- **Trigger:** Separate observability project, not a bus feature.

---

## Bus Phase 1 — surfaced by whole-phase review

These items were observed during the final whole-phase code review of the
v1 bus and judged non-blocking for merge. Each lists the affected file
and the specific condition or change that should trigger the fix.

### Mailbox cap TOCTOU under concurrent publishers

`src/agent_core/bus/core.py` `_enqueue` — pre-validate-then-insert is
not atomic across awaits. Two coroutines both observing `count_pending`
below the cap can both pass the check and both insert, exceeding the
cap. Soft cap, not hard.

- **Source:** Phase 1 whole-phase review
- **Trigger:** First production observation of mailbox-cap overshoot,
  or any consumer that absolutely cannot tolerate a slightly-over-cap
  mailbox. Mitigation is a per-endpoint `asyncio.Lock` around the
  check-then-insert critical section.

### Hook visibility asymmetry across publish lifecycle

`src/agent_core/bus/core.py` `_enqueue` — `pre_publish` hooks see the
original `envelope.to` once before fan-out; per-recipient envelopes get
their own `pre_deliver` pass with the rewritten `to`. An audit hook at
pre_publish cannot record `alice → {a, b}` directly.

- **Source:** Phase 1 whole-phase review
- **Trigger:** First hook (audit log, ACL, redaction) that needs to
  observe or transform the full fan-out recipient list at the
  pre_publish stage. Cheapest fix is a docstring note on
  `BusHandle.publish` and `BusHook.execute`; deeper fix is to thread
  the recipient list into the pre_publish payload.

### Duplicate envelope id raises raw aiosqlite IntegrityError

`src/agent_core/bus/persistence.py` `insert` — republishing the same
id surfaces `aiosqlite.IntegrityError: UNIQUE constraint failed:
envelopes.id` to the caller. No domain wrapper.

- **Source:** Phase 1 whole-phase review
- **Trigger:** First channel adapter that relays events with caller-
  supplied ids (webhook event ids, Discord message ids, etc.) where
  duplicates are expected. Wrap in a `DuplicateEnvelopeId` exception
  or make `insert` idempotent on conflict.

### CLI read-only subcommands instantiate every endpoint

`src/agent_core/bus/cli.py` `_status`, `_mailbox`, `_trace`, `_dlq_list`,
`_replay`, `_dlq_purge` — all call `build_bus_from_config`, which
constructs every endpoint and validates Protocol conformance, then
discards the bus without starting it.

- **Source:** Phase 1 whole-phase review
- **Trigger:** First Phase 2 endpoint whose constructor opens files,
  reads tokens, or otherwise has non-trivial cost — at which point
  `bus status` becomes slow or fails when an unrelated endpoint can't
  construct. Fix is to split YAML loading from endpoint instantiation
  for read-only CLI paths.

### Sweep loops silent on idle ticks

`src/agent_core/bus/cli.py` `_ttl_loop` / `_redelivery_loop` — sweeps
tick on schedule but log nothing on idle (no rows to process). An
operator running `bus run` cannot easily confirm the sweeps are alive
on a quiet bus.

- **Source:** Phase 1 whole-phase review
- **Trigger:** First operational confusion about whether sweeps are
  running. Fix is a debug-level log on each tick.

---

## FastMCP 3.x adapter gaps (`ClaudeCodeMCPEndpoint`)

The original v1 adapter gaps around `_session_active` disconnect cleanup and
polling-only inbound mail were resolved by Sub-project I (PR #9 draft):

- `SessionRegistry` now captures the active FastMCP session via the
  session task-group lifecycle and releases it on disconnect.
- `queue_for_pickup()` is idempotent by envelope id, avoiding duplicate
  in-memory entries during bus retry paths.
- `_notify_mail_arrived()` now pushes `notifications/claude/channel`
  summaries with urgency-aware debounce.
- `agent-core-channel` relays `/notify/<agent>` SSE events into Claude
  Code's supported stdio channel mechanism, including initial wake-on-connect
  snapshots.

Remaining follow-up: decide whether multi-session-per-agent should be
strictly refused or remain most-recent-wins. The current implementation
keeps one active HTTP MCP session slot with most-recent-wins replacement,
while the broker can fan out to multiple relay subscribers. That is useful
for local recovery but has not been designed as a multi-agent ownership model.

---

## Heartbeat-checker endpoint (no-op heartbeat suppression)

Pepper's inbox-architecture spec (`C:\Users\jeffr\.pepper\Memory\projects\pepper\inbox-architecture.md`,
2026-04-29) calls for producer-side suppression of heartbeats whose
checks find nothing actionable. The "all clear, nothing to surface"
case should never enter the agent's mailbox at all — Pepper measured
~38–43/day eliminated and a corresponding cognitive-load drop.

In Pepper's current monolith the check logic (calendar / email /
tasks / GitHub PRs / Discord-mentions) lives inside her prompt-and-tools
so suppression is a 2-hour edit. In agent-core none of those check
capabilities exist outside an agent context, so producer-side
suppression needs its own infrastructure.

The intended shape: a `HeartbeatCheckerEndpoint` that registers on
the bus and is the scheduler's heartbeat target instead of Pepper.
The checker:
- Wakes every 30 minutes via SchedulerEndpoint (existing bus surface).
- Uses `agent-core-credentials` (already shipped) for API keys.
- Runs the check rules from Pepper's spec § 3.5:
  - 🟢 Calendar event in next 2h
  - 🟢 Unread urgent email or new in last 1h from priority sender
  - 🟢 Task overdue today
  - 🟢 Project STATUS.md changed in last hour
  - 🟢 GitHub PR awaiting review or CI failure on active repo
  - 🟢 Unread @mention in any channel
- If any signal fires, publishes a heartbeat envelope to Pepper with
  the signal payload as metadata.
- If all clean, logs "all clear" to a debug file and drops.

Each signal source is its own integration. v1 minimum is probably
calendar + GitHub + filesystem (no Gmail/Discord-mention scan).
Later versions add the rest as their respective MCP/API clients
land in agent-core.

The "always surface" jobs from Pepper's § 3.5 (`pepper_time`,
`nightly_reflection`, `morning_briefing`, `evening_routine`,
`daily_sync`, `weekly_digest`) are NOT heartbeats and don't need
this endpoint — they keep going through the normal scheduler path.
`github_backup` is the one outlier: surfaces only on failure
(formalize the existing convention).

- **Source:** Pepper inbox-architecture spec § 3.5 + § 4.1, 2026-04-29.
- **Trigger:** After the responsive-inbox work (sub-project F or
  similar) ships push notifications + same-sender batching + urgency.
  Heartbeat suppression is a Pepper-readiness item; it doesn't make
  sense to build it before the consumer side is in shape to use it.
  Estimated 3–5 days for the v1 with calendar + GitHub + filesystem
  checks; the long tail is per-source integrations.
