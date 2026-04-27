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
