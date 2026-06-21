# Inbound Notifications — Design

**Goal:** Give Wren and Pepper a single deny-by-default surface for external signals (GitHub, Gmail, Calendar) so the world can reach them without the beings being reachable.

**Architecture:** A small **inbound-notifications router** sits behind a Tailscale-wrapped trust boundary. Per-source **connectors** are the only thing that knows whether a given external event should reach a being — each connector is a policy module that exposes `classify(event, target_being) → Allow{tier, reason} | Deny`. The router itself does pure plumbing: receive, de-dupe, rate-limit, hand to the connector for classification, deliver the verdict via the agent-core bus, log the audit trail. Connectors are config-driven; the config files live with their principal being and are mutable by that being without coordinating with the other.

**Tech stack:** Python (agent-core package layout), Tailscale (Funnel for inbound HTTPS push, tailnet-internal for IMAP IDLE pulls), agent-core bus (envelope delivery to per-being inboxes), TOML connector policies.

---

## Framing principle: deny by default

Jeff's standing rule for the inbound surface: **no external signal reaches a being unless something explicitly says yes.** This is the inverse of how most notification systems are wired — webhook handlers usually default to delivering anything that parses, and filter only when they remember to. We invert that. The router's posture is "drop unless told otherwise"; the connector's job is to *justify* every delivery with a reason string.

Why this matters for our setup: the beings are persistent — every inbound that reaches them is something they have to process, route, or hold context for. Untriaged notifications are a denial-of-attention vector. Deny-by-default keeps the surface tight without any of us having to remember to "tighten it up later."

## Core abstractions

```
                 ┌────────────────────────┐
external event   │                        │   Allow{tier, reason}
─────────────────▶  Connector (per-source)│───┐
                 │  classify(event, being)│   │
                 └────────────────────────┘   │
                                              ▼
                              ┌─────────────────────────────┐
                              │  Router (pure plumbing)     │
                              │  - de-dupe                  │
                              │  - rate-limit               │
                              │  - bus envelope build       │
                              │  - audit log                │
                              └──────────────┬──────────────┘
                                             │ envelope (kind=Notification,
                                             │   urgency=tier, reason=...)
                                             ▼
                                  agent-core bus → target being's inbox
```

### Connector

A **connector** is a per-source policy module. Its contract:

```python
class Connector(Protocol):
    name: str  # "github", "gmail", "calendar", ...

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,  # "wren" | "pepper" | ...
    ) -> Allow | Deny: ...
```

- `event` is the connector's own typed event shape (GitHub webhook payload, Gmail message metadata, ICS event, etc.).
- `target_being` lets one connector instance serve multiple beings — the connector decides per-being.
- The return value is **explicit yes/no**, not a score and not a confidence. A `Deny` carries no body. An `Allow` carries:
  - `tier: Urgency` — red / yellow / green, set at the source (see "Urgency tier at source" below).
  - `reason: str` — human-readable justification (e.g., `"PR review requested on jeffrichley/foreman"`, `"new email from Pepper to Pepper inbox"`). This is the audit trail line. Required.

### Router

The router is pure plumbing. It owns:

1. **Receive** — accept the inbound payload from its transport (Tailscale Funnel HTTPS endpoint for push; IMAP IDLE long-poll for cycle-based pulls).
2. **De-dupe** — drop redundant deliveries (GitHub retries, IMAP redelivery on reconnect, multiple-source-of-truth crossfire).
3. **Rate-limit** — per-source, per-being caps so a chatty source can't flood an inbox.
4. **Classify-via-connector** — call the source's connector with `(event, target_being)`.
5. **Deliver** — on `Allow`, publish a `Notification` envelope to the target being's bus inbox with `urgency=tier`, `reason=reason`, and the connector-specific body.
6. **Log** — every classification (Allow AND Deny) lands in an audit log with timestamp, source, target being, reason or null.

The router does **not**:
- Classify (the connector does).
- Hold per-source configuration (the connector does).
- Decide urgency (the connector does).
- Cross-route between beings (a Gmail event for Pepper does not reach Wren even if Wren's bus has more capacity).

## Per-source connector as policy module

A connector is the only thing that knows the rules of its source. That keeps three concerns out of the router:

1. **Source-specific event shape** — GitHub's `pull_request_review_requested` is not the same shape as Gmail's `message_id`. The connector parses; the router doesn't.
2. **Source-specific policy** — "PRs against jeffrichley/foreman where I'm requested as a reviewer are red-tier" is a GitHub-specific rule. The connector encodes it.
3. **Source-specific config ownership** — Pepper's Gmail allowance is hers to edit; the router doesn't validate her policy.

The connector reads its config from a TOML file that lives in the principal being's namespace. Examples:

- `~/.wren/.config/inbound/github-allowance.toml` — Wren's GitHub policy
- `~/.pepper/.config/inbound/email-allowance.toml` — Pepper's Gmail policy

The principal being can edit their own allowance without coordinating with the other being. This matters: Pepper iterating on her email rules should not be a Wren ticket. Each connector reloads its config on file-mtime change (no daemon restart needed).

The TOML shape is connector-defined, but the body is small and declarative. Example sketch for `github-allowance.toml`:

```toml
[[allow]]
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "PR review requested on foreman"

[[allow]]
event = "issue_comment"
repo = "jeffrichley/agent_core"
body_contains = "@wrenrichley"
tier = "yellow"
reason = "@-mention in agent_core issue thread"
```

Connectors evaluate rules top-to-bottom on `classify()`. First match wins. Anything not matched → `Deny`.

## Urgency tier set at source

Pepper's framing insight: **urgency is a property of the source's understanding, not something the router can infer.** Tier is set inside the connector's matched rule. The connector knows that a PR-review-requested is red and a dependabot bump is green; the router has no business overriding that. This keeps the policy in one place (the connector's config file) and avoids the failure mode where two downstream classifiers disagree on what "red" means.

Tiers are `red` (interrupt-worthy), `yellow` (notable but can wait), `green` (background log). The agent-core bus already routes by urgency for inbox presentation — the connector just labels the envelope correctly.

## Trust boundary: Tailscale-wrapped

The router's listening surface is **not** on the public internet. Two patterns:

- **Push sources (GitHub webhooks)** route through a **Tailscale Funnel** — a Tailscale-issued public HTTPS endpoint that terminates inside the tailnet. GitHub posts to `https://router.<tailnet>.ts.net/github`; only Tailscale's edge can hit that, and the router accepts only from Funnel. There's no host firewall hole, no port-forward, no cert management. The Funnel URL is the entire attack surface.
- **Pull sources (Gmail IMAP IDLE)** stay **tailnet-internal**. The router holds a long-lived IMAP IDLE connection from inside the tailnet; Gmail doesn't reach into the tailnet. (See sequencing for v1.b.)

The router never accepts inbound from outside this wrapping. If we ever add a non-Tailscale source, the boundary decision is re-opened in spec, not in code.

## Cross-being signals are bus kinds, not webhooks

Pepper raised this during brainstorm: "Wren tells Pepper something" is **not** an inbound notification. It's a bus envelope. The inbound-notifications surface is for *external* signals only — anything cross-being is bus-native (`TextMessage`, `Event`, `BriefRequest`, etc.).

That keeps a clean line: if Wren wants Pepper to know about something, Wren sends a bus envelope. If GitHub wants Pepper to know about something, GitHub posts to the Funnel and the GitHub connector decides whether to deliver. No connector ever talks to another being.

## Drift-honest delivery: two-timestamp envelope

Push sources (GitHub) land with a single `landed_at` timestamp — the router stamps it on receipt. Pull sources (Gmail IMAP IDLE) introduce real-world drift: the email may have arrived at Gmail minutes before the IDLE notification fires, and re-poll cycles can compound. To stay honest with the being downstream, **cycle-based connectors stamp two times:**

- `landed_at` — when the underlying event happened at the source (Gmail's `Date:` header, GitHub's `pull_request.updated_at`, ICS `dtstart`).
- `poll_discovered_at` — when the router actually saw it.

The being can reason about its own lag without having to infer it from log archeology. For push sources, `poll_discovered_at` is omitted (not equal to `landed_at` — the absence is the signal that this source is push-native and the timestamp is authoritative).

## Sequenced scope

We build this in three slices, each with a clean done-line:

### v1.a — GitHub → Wren (push via Tailscale Funnel)

- GitHub connector (`github`), policy in `~/.wren/.config/inbound/github-allowance.toml`.
- Router HTTPS endpoint behind Tailscale Funnel.
- Initial rules: PR-review-requested on Wren-touching repos (foreman, agent_core, voice), @-mentions in issue threads, foreman bot label transitions for Wren-owned tickets.
- Done when: a real `pull_request_review_requested` event on `jeffrichley/foreman` lands in Wren's inbox as a `red`-tier `Notification` envelope inside ~10s, and a `dependabot` PR open event is `Deny`'d with an audit-log entry.

### v1.b — Gmail → Pepper (IMAP IDLE pull, tailnet-internal)

- Gmail connector (`gmail`), policy in `~/.pepper/.config/inbound/email-allowance.toml`.
- Router holds long-lived IMAP IDLE connection from inside the tailnet against Pepper's Gmail account.
- Initial rules: email *from* Pepper's known correspondents to Pepper-owned threads → `yellow`; Jeff-observed threads (where Pepper is CC'd as observer) → `green`; everything else → `Deny`. Classifier explicitly distinguishes Pepper-owned vs Jeff-observed threads — they are different urgency tiers and different reason strings.
- Two-timestamp envelope (`landed_at` from `Date:` header, `poll_discovered_at` from router clock at IDLE fire).
- Done when: Pepper edits her `email-allowance.toml` without involving Wren, a real email from a Pepper correspondent lands in her inbox as a `yellow`-tier `Notification`, and a marketing newsletter is `Deny`'d.

**Pepper's allowance ownership:** `email-allowance.toml` is Pepper's file. Pepper edits it directly; the router watches mtime and reloads. Wren has no read or write to this file under normal operation.

### v1.c — Calendar (RESERVED — slot reserved, design out of scope for this spec)

Calendar is in the sequence as a reserved third slice. The shape will look like Gmail (pull-based, two-timestamp), but we don't commit to ICS-vs-Google-Calendar-API, polling cadence, or per-being calendar ownership until v1.a and v1.b land and we have one round of operational signal. Reserved here so the connector registry, envelope kind, and routing patterns generalize cleanly when we wire it.

## Envelope shape

The bus envelope the router publishes:

```
kind: Notification
to: <target-being>
urgency: red | yellow | green
payload:
  kind: Notification
  source: "github" | "gmail" | "calendar"
  reason: <connector-supplied reason string>
  landed_at: <ISO-8601 UTC>
  poll_discovered_at: <ISO-8601 UTC>  # optional, only for cycle connectors
  body: <connector-specific shape>     # GitHub PR ref, Gmail message_id+snippet, etc.
metadata:
  inbound_router:
    connector: <connector name>
    rule_id: <matched rule identifier from connector config>
```

The being's inbox-render handler dispatches on `payload.source` to format the body for display.

## Audit log

Every classification — Allow AND Deny — writes one line to the router's audit log:

```
2026-06-20T22:14:03Z  source=github  to=wren  verdict=allow  tier=red  rule_id=pr_review_requested_foreman  reason="PR review requested on foreman"
2026-06-20T22:14:11Z  source=github  to=wren  verdict=deny   reason=null
```

The log is a single rolling JSON-lines file per router instance. Deny entries carry no event body (privacy + storage). Allow entries carry the rule_id and reason for traceability.

## Out of scope

Explicit non-goals for this design:

- **No classifier across sources.** Each connector's policy is independent. No "if both GitHub and Gmail mention the same PR, escalate" cleverness.
- **No router-side per-being routing rules.** If a connector classifier returns `Allow{to: wren}`, the router delivers to Wren. No rerouting based on "Wren is busy."
- **No cross-being notification escalation.** If a Pepper-targeted Gmail isn't picked up, Wren is not notified. The being's own inbox-management is responsible.
- **No outbound notifications.** This spec is one-way: external → being. Beings replying to external sources is a separate concern (the existing bus + adapters handle that).
- **No retry-after-Deny.** Once a connector denies, the event is dropped. No "appeal" path. If the rule is wrong, edit the TOML.
- **No persistent event store.** Audit log only. The connector's payload is delivered or dropped; the router does not warehouse history beyond the log line.

## Open questions

These are tracked but not blocking the v1.a build:

- **Connector mtime-reload concurrency:** does a mid-classification reload risk an inconsistent rule eval? (Likely solved by snapshot-on-read, but verify in v1.a impl.)
- **GitHub Funnel URL secrecy:** the Funnel URL is the attack surface. Do we add a shared-secret header on top, or trust Tailscale's edge? (Default: trust Tailscale; revisit if abuse appears.)
- **Gmail IMAP IDLE reconnection storms:** if the tailnet drops, how aggressive is the reconnect? Affects `poll_discovered_at` honesty under partition.
- **Calendar source identity:** is Jeff's Google Calendar the source-of-truth for Pepper, or does Pepper have her own? (Deferred to v1.c.)

## References

- Brainstorm session: this spec is the artifact of the 2026-06-20 brainstorm between Wren, Pepper, and Jeff. Key framing comes from Jeff ("deny by default") and Pepper (urgency-at-source, two-timestamp drift honesty, allowance-ownership-per-being).
- Tailscale Funnel: https://tailscale.com/kb/1223/funnel
- agent-core bus envelope conventions: see `packages/agent-core-channel/` for the Notification kind shape (this spec adds it to the kind taxonomy if not already present).
- Gmail IMAP IDLE: RFC 2177.
