# Envelopes & Kinds

Every message that crosses the bus is an `Envelope`. There is no other wire format. Whether you are an AI agent sending a text message to a Discord adapter, a scheduler signaling a job event, or an endpoint acknowledging receipt, the shape is always the same: an `Envelope` with a typed payload inside.

## The field set

```
class Envelope(BaseModel):
    id: str                              # unique envelope id (hex uuid)
    correlation_id: str                  # groups related envelopes into a thread
    in_reply_to: str | None             # id of the envelope this is a reply to
    from_: str                           # stamped by the bus at publish time
    to: str                              # registered endpoint name
    kind: str                            # discriminates the payload type
    payload: EnvelopePayload | dict      # typed for built-ins, dict for plugin kinds
    metadata: dict[str, Any]            # extension data (adapter-specific, not bus-controlled)
    urgency: Literal["green", "yellow", "red"]  # default: "green"
    expires_at: datetime | None         # TTL; bus expires undelivered envelopes past this
    created_at: datetime
```

### `from_` is stamped by the bus

You do not set `from_` yourself. When you call `BusHandle.publish()`, the bus overwrites `from_` with the publishing endpoint's registered name. This is an identity guarantee: an endpoint cannot spoof another endpoint's name, regardless of what it constructs locally.

### `correlation_id` for threading

Use `correlation_id` to group a chain of related envelopes — the originating request and all replies, progress updates, and cancellations that belong to it. The bus does not interpret or enforce correlation semantics; it is a field you set and read.

`in_reply_to` narrows further: it names the specific envelope that triggered this one, useful when a thread branches (e.g., two parallel ToolInvocation replies to one request).

## Built-in payload kinds

The `kind` field is a free string, but the seven built-in kinds get full Pydantic validation. The bus rejects a built-in kind whose payload fails type validation — you get the error synchronously at publish time, not as a later dead-letter.

| Kind             | Purpose                                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `TextMessage`    | Human-readable text; carries `text: str` and optional `attachments`                                                            |
| `Event`          | Domain events; carries `type: str`, `schema_version`, and an open `data: dict`                                                 |
| `ToolInvocation` | Request to call a named tool; carries `tool: str` and `args: dict`                                                             |
| `Cancellation`   | Cancel a prior request; optionally carries `reason: str`                                                                       |
| `Progress`       | Mid-flight status update; carries `status` (working/blocked/complete) and optional `note`, `percent`                           |
| `Acknowledgment` | Explicit acknowledgment of a prior envelope; carries `of: str` (the envelope id) and optional `note`                           |
| `Notification`   | Inbound notification from an external source (GitHub, Gmail, etc.); carries `source`, `reason`, `landed_at`, and a `body` dict |

Acknowledgment vs ack()

`Acknowledgment` is a *message kind* you publish when you want the sender to know you received and processed their envelope. It is different from the bus-level `ack()` call that clears the in-flight state in the mailbox. Most endpoints do both: call `bus.ack(envelope.id)` to satisfy the bus, and optionally publish an `Acknowledgment` envelope back to the sender to close the application-level loop.

## Urgency

`urgency` is a three-level signal: `green` (default, background), `yellow` (elevated), `red` (needs immediate attention). Higher-urgency envelopes are presented first in inline-wake notifications. The bus delivers in the order they are dispatched; urgency controls presentation in the notification layer, not delivery order on the bus itself.

## Plugin kinds

The built-in set is not closed. Plugins can register additional first-class kinds via the `register_envelope_renderers` hookspec. A plugin kind:

- Uses a dict payload (not a typed Pydantic model) whose `"kind"` key matches `Envelope.kind`.
- Is validated by the plugin's own code, not the bus.
- Has a renderer that formats it inside `<inbox>` notifications for inline-wake delivery.

Duplicate kind names across plugins raise `PluginRegistryError` at startup. See [Extensions](https://jeffrichley.github.io/agent_core/concepts/extensions/index.md) for the registration pattern.

## Envelope as audit trail

Because every envelope is persisted to the SQLite mailbox before delivery, you always have a record of what was sent, when, from whom, to whom, and what happened to it (pending, in-flight, acked, dead-letter, expired). The `bus_tail` surface exposes this for read-only queries.

For exact field types and payload model signatures, see the [API Reference](https://jeffrichley.github.io/agent_core/reference/index.md).
