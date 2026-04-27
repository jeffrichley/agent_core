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
