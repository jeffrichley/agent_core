# agent-core-inbound

Deny-by-default inbound notifications router for agent-core beings.
External signals (GitHub webhooks, Gmail messages, calendar events)
flow through per-source **connectors** that classify each event as
`Allow{tier, reason}` or `Deny`. The router de-dupes, rate-limits,
delivers via the agent-core bus, and writes an audit log.

See `docs/superpowers/specs/2026-06-20-inbound-notifications-design.md`
in the agent_core repo for the full design.
