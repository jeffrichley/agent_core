# Claude Code MCP auto-ack — operational trust model

The `builtin.claude_code_mcp` endpoint can **auto-ack** routine green `Acknowledgment` envelopes that reference a **recent outbound** published via this endpoint’s `send` tool, without waking the channel or requiring an HTTP MCP session.

## Assumptions

1. **Bus integrity** — Any party that can publish envelopes to an agent’s mailbox can also send ack-shaped traffic. Auto-ack only fires when `in_reply_to` / `payload.of` match a **recent outbound id** (UUID hex from `send`). That is a high bar for blind guessing but **not** a cryptographic guarantee; compromised or buggy publishers on the same bus remain in scope.

2. **Structural “routine green”** — Classification is by envelope shape (kind, urgency, `error:` note prefix, `of` vs `in_reply_to`, registry membership). It does **not** interpret JSON inside `note`. Treat `urgency` and `note` as authoritative only if your bridges set them honestly.

3. **`metadata.agent_core.ack_timeout_seconds`** — Interpreted only for missing-ack timers; values are **clamped** to a bounded range at runtime (see `claude_code_mcp.py` module constants).

## Observability

- **Debug**: one line per auto-acked inbound id and referenced outbound id.
- **Info**: one line when a missing-ack timer fires (wake path).

Enable `DEBUG` for `agent_core.endpoints.claude_code_mcp` when diagnosing “agent never saw the ack” reports.
