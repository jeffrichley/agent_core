# Issue #114 — Unified `discord_send` envelope shape (Design)

> **Status:** Drafted 2026-05-23. Pending spec-review approval.
>
> **Issue:** [#114](https://github.com/jeffrichley/agent_core/issues/114) — `discord: unified discord_send envelope shape`.
>
> **Scope:** Add a canonical `tool=discord_send` to the Discord adapter. Close the silent-drop class on unrecognized fields via an adapter-side strict-mode validator that publishes a failed-delivery `Acknowledgment` to the sender with the canonical equivalent named. No bus envelope schema change; existing shapes continue to deliver via back-compat with a deprecation-warning log. Deprecation removal is a follow-up ticket gated on telemetry.

## Problem

Discord-bound envelopes can take two structurally different shapes depending on payload:

- **Plain text:** `TextMessage` + `metadata.discord.channel_id`.
- **Embeds / files:** `ToolInvocation` + `tool=send_discord_message` + `args.{channel_id, text, embeds | files}`.

Senders have to remember which shape to use for which payload. The mistake mode is silent: an embed payload sent via `TextMessage` + `metadata.discord.embeds` was historically accepted (the bus returned `status: published`), never delivered to Discord, never produced a failed-delivery `Acknowledgment` to the sender. The publish-side success signal was honest; the routing-side drop was silent.

The specific poster-child case (`TextMessage` + `metadata.discord.embeds`) is now routed by `_deliver_text_message` per commit [`a278c68`](https://github.com/jeffrichley/agent_core/commit/a278c68) ("feat(briefs): DiscordEmbedDestination + bus-mediated embed delivery (T11)"). That commit added the routing as a side-effect of an unrelated briefs feature, not as a deliberate fix to the silent-drop class. The pattern that produced the original bug — the adapter using `.get()` on metadata namespaces without enforcing a recognized-shape contract — still lives everywhere else in `endpoint.py`. New fields added in future tickets (e.g., `components`, `modals`, the typed-Task work in #117) will produce the same class of silent-drop unless we converge on one canonical shape and tighten the adapter's strict-mode posture.

### Concrete failure mode (2026-05-23, Pepper)

Pepper sent an embed payload via `TextMessage` + `metadata.discord.embeds`. The bus returned `status: published`; no Discord message was created; no failed-delivery `Acknowledgment` came back. Pepper diagnosed the wrong-shape choice only because she'd made the same mistake before. The fact that her current code path now happens to deliver (per `a278c68`) does not eliminate the class — it just shifted the next victim to whatever field the adapter next reads via `.get()` without recognition.

### Goal of this ticket

Close the silent-drop *class* for the discord-send surface. Two-part fix:

1. **One canonical shape that always works** — `tool=discord_send` with unified args. Senders no longer choose between two shapes.
2. **Adapter-side strict-mode** — any envelope carrying a field the adapter does not have routing for produces a failed-delivery `Acknowledgment` to the sender (yellow urgency) with the canonical equivalent named in the note. No more silent drops.

## Out of scope

- **Adding a new envelope kind to the closed union.** Considered (Option A in brainstorming); rejected in favor of a new `ToolInvocation` tool name (Option B). Reasoning: A required a real schema migration (every `kind`-switching consumer in the codebase + tests + rendering layer), and A's only earned benefit — publish-time rejection of ambiguous old shapes — is achievable at the adapter layer via the strict-mode validator at zero schema cost.
- **Removal of the deprecated shapes.** Old shapes continue to deliver after this ticket lands. Removal is a follow-up ticket gated on deprecation-readiness telemetry (see "Next-ticket triggers" below).
- **Closing `.get()` silent-drop patterns elsewhere in this adapter or in other adapters.** Class-shape applies; per-instance fixes await their own named symptoms (rule-of-three for any cross-cutting extraction).
- **Generalizing to other surfaces** (`slack_send`, `email_send`, `notion_send`). No current symptom, no current adapter. When the second similar adapter ships, extract a shared shape-validator utility per rule-of-three.
- **Args expansion beyond Discord-supported send fields.** Interaction handlers, modal responses, voice-channel commands, etc. are separate capabilities and earn their own tools / arg models.
- **Bus-side schema validation.** The bus continues to validate only the envelope structure (closed `kind` union, payload-discriminator match). Per-adapter shape contracts live in the adapter, not in the bus.

## Design

### Architecture

Three moves, all inside the existing schema (no new envelope kind):

**1. New canonical tool.** Add `discord_send` to the Discord adapter's tool table. Args are one unified shape: `{channel_id (required), text?, embeds?, files?, reply_to?, allowed_mentions?, components?, cleanup_inbound_message_id?}` (the same eight fields enumerated in Components §2). The adapter routes by what is present — text-only is one path, `+embeds` another, `+files` another, `+reply_to` wraps as a reply. One sender shape that always works.

**2. Strict-mode validator at adapter entry.** Every envelope handed to `DiscordEndpoint.deliver()` is matched against a routing-exists check before dispatch. The check answers one question: *does the adapter have routing code that consumes every field present on this envelope?* If yes → deliver (with a deprecation-warning log if the shape is not the canonical `discord_send`). If no → publish a failed-delivery `Acknowledgment` to the sender immediately, with a note naming the unrecognized field(s) and the canonical equivalent. The validator is what closes the silent-drop *class*, not just the poster-child case.

**3. Old shapes as back-compat aliases.** The existing tool names (`send`, `send_discord_message`) and the existing `TextMessage` + `metadata.discord.*` routes continue to deliver. Each routed call emits one structured deprecation-warning log line. No bus-side schema changes; no in-flight sender breaks on day one.

### Components

#### 1. `packages/core/src/agent_core/bus/envelope.py` — no changes

Closed-union envelope schema stays. Explicit non-change anchors the no-schema-migration promise. No consumer that switches on `kind` needs to learn a new value. No discriminated-union update; no validator wiring; no tests touched at the bus layer.

#### 2. New `_DiscordSendArgs` Pydantic model in `packages/agent-core-discord/args.py`

```python
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class _DiscordSendArgs(BaseModel):
    """Canonical discord_send args. Unified field set; strict on extras.

    Pydantic extra='forbid' is the second strict layer behind the
    envelope-level shape_validator: a typo on the args side raises
    ValidationError at dispatch and surfaces as a yellow Acknowledgment
    via the existing _ToolError → _reply path, rather than being
    silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    files: list[str] | None = None
    reply_to: str | None = None
    allowed_mentions: dict[str, Any] | None = None
    components: list[dict[str, Any]] | None = None
    cleanup_inbound_message_id: str | None = None
```

Field rationale: `channel_id` is the one required field — every discord-send needs a destination. `text`, `embeds`, `files` are the three payload kinds (at least one required at dispatch time, see Error Handling §5). `reply_to` triggers Discord's native reply UI. `cleanup_inbound_message_id` is the existing typing-cleanup field from `_SendArgs` (`#84`), preserved for parity.

**`allowed_mentions` and `components` are future-proof slots, not wired in v1.** They are Discord-supported send-time options that the adapter does not currently expose; the args model accepts them (so the strict-mode contract does not reject a forward-looking caller), but the v1 implementation does not pass them through to `discord.py` and the `_send` body does not consume them. A future ticket that earns the symptom adds one line in `_send` per field, with no args-model migration. Until then, callers passing them get a successful send with the option silently unapplied. (Acceptable because `extra="forbid"` is rejecting the *unknown* — these are *known but unwired*; the next ticket closes the wiring gap when there is named demand.)

#### 3. NEW `packages/agent-core-discord/shape_validator.py` module

Pure-function home for the recognized-shape catalog. Same architectural shape as the AI Cliché Detector's `ignore.js` / `clicktofix.js`: zero side effects, no I/O, no `chrome.*` / `discord.*` imports — fully unit-testable in isolation.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Recognized:
    """Validator outcome: the envelope matches a known shape."""
    shape_name: str
    deprecation_log_line: str | None  # None for canonical, str for legacy


@dataclass(frozen=True)
class Unrecognized:
    """Validator outcome: the envelope carries fields the adapter does
    not route. The Ack note names the first unrecognized prefix and the
    canonical equivalent so the sender has a teaching moment instead of
    a silent drop."""
    fields: list[str]            # first unrecognized prefix per branch
    canonical_equivalent: str    # "tool=discord_send with <X> in args"


ShapeValidation = Recognized | Unrecognized


def validate(envelope) -> ShapeValidation: ...
```

The catalog enumerates ~7–8 recognized shapes:
- `ToolInvocation + tool=discord_send + canonical args` → `Recognized("canonical", None)`.
- `ToolInvocation + tool=send + canonical args` → `Recognized("legacy_tool_send", deprecation_line)`.
- `ToolInvocation + tool=send_discord_message + canonical args` → `Recognized("legacy_tool_send_discord_message", deprecation_line)`.
- `TextMessage + metadata.discord.channel_id` (plain text) → `Recognized("legacy_textmessage_plain", deprecation_line)`.
- `TextMessage + metadata.discord.embeds` → `Recognized("legacy_textmessage_embeds", deprecation_line)` (poster-child, routed since `a278c68`).
- `TextMessage + metadata.discord.reply_to` → `Recognized("legacy_textmessage_reply", deprecation_line)`.
- `TextMessage + payload.attachments` (file list) → `Recognized("legacy_textmessage_files", deprecation_line)`.

Nested-path handling: the catalog operates at the field-path level; an unknown step short-circuits the walk and the Ack names the step where the catalog ran out of routes. `metadata.discord.foo.bar.baz` with `foo` unknown produces `Unrecognized(fields=['metadata.discord.foo'], ...)` — not an enumeration of leaves.

Multi-field handling: when multiple unrecognized fields exist at the same level, all are enumerated in one `Unrecognized.fields` list so the sender sees the full failure surface in one Ack, not in N redeliveries.

#### 4. `packages/agent-core-discord/src/agent_core_discord/endpoint.py` — three additions

**4a. `_TOOL_ALIASES` table** (around line 67): add `discord_send` as a passthrough; keep `send_discord_message → send` for back-compat.

```python
_TOOL_ALIASES: dict[str, str] = {
    "send_discord_message": "send",   # existing legacy alias
    "discord_send": "discord_send",   # NEW: canonical passthrough
    "edit_message": "edit",
    "add_reaction": "react",
    "fetch_messages": "fetch",
}
```

**4b. `_dispatch`** (around line 778): add the `discord_send` case routing to the existing internal send handler. `send` continues to route there too.

```python
if tool == "discord_send":
    return await self._send(_v(_DiscordSendArgs, _inject_channel_id(args)))
if tool == "send":
    return await self._send(_v(_SendArgs, _inject_channel_id(args)))
```

(`_DiscordSendArgs` is the new strict model; `_SendArgs` stays for back-compat through the legacy `send` route. Internal handler name `_send` stays; rename to `_canonical_send` is optional cleanup and not load-bearing.)

**4c. `deliver()`** (around line 655): call `shape_validator.validate(envelope)` at the top, before the kind-branch.

```python
async def deliver(self, envelope: Envelope) -> None:
    if self._handle is None:
        raise EndpointUnavailable(f"discord '{self.name}' not started")

    # Existing kind-gate: only TextMessage and ToolInvocation pass through
    # to the validator. Other kinds fall through to the existing
    # "unsupported kind" branch below.
    if envelope.kind in ("TextMessage", "ToolInvocation"):
        validation = shape_validator.validate(envelope)
        if isinstance(validation, Unrecognized):
            log.warning(
                "discord(%s): unrecognized_shape event",
                self.name,
                extra={
                    "event": "unrecognized_shape",
                    "envelope_kind": envelope.kind,
                    "unrecognized_fields": validation.fields,
                    "sender": envelope.from_,
                    "envelope_id": envelope.id,
                    "canonical_equivalent": validation.canonical_equivalent,
                },
            )
            note = (
                f"Unrecognized field{'s' if len(validation.fields) > 1 else ''} "
                f"{validation.fields if len(validation.fields) > 1 else repr(validation.fields[0])} "
                f"on {envelope.kind}. Canonical: {validation.canonical_equivalent}"
            )
            await self._reply(envelope, note, urgency="yellow")
            await self._handle.ack(envelope.id)
            return
        if validation.deprecation_log_line:
            log.warning(
                "discord(%s): deprecated_shape event",
                self.name,
                extra={
                    "event": "deprecated_shape",
                    "shape_name": validation.shape_name,
                    "sender": envelope.from_,
                    "envelope_id": envelope.id,
                    "canonical_equivalent": "tool=discord_send",
                },
            )

    # ... existing dispatch path (unchanged) ...
```

#### 5. Failed-delivery `Acknowledgment` note shape

Uses the existing `_reply(incoming, note, urgency="yellow")` machinery (`endpoint.py` line 836). No new exception type, no new envelope kind — the failed-delivery Ack is structurally identical to today's tool-error Acks; only the `note` text shape is new.

Note text:
- **Single unrecognized field:** `"Unrecognized field 'metadata.discord.mystery_field' on TextMessage. Canonical: tool=discord_send with mystery_field in args."`
- **Multiple unrecognized fields:** `"Unrecognized fields ['metadata.discord.mystery_field', 'metadata.discord.other_unknown'] on TextMessage. Canonical: tool=discord_send with the corresponding args."`

Correlation: `_reply` already sets `in_reply_to=incoming.id` (line 847), so the sender's `Acknowledgment` listener correlates with the original envelope without any changes.

### Data Flow

Flow per envelope arriving at `DiscordEndpoint.deliver()`:

**1. Kind gate (existing).** If `envelope.kind` is not `TextMessage` or `ToolInvocation`, fall through to the existing else-branch (warning Ack, inbound acked). Validator not consulted.

**2. Validator runs.** `shape_validator.validate(envelope)` produces one of:

  a. **`Recognized` + canonical** (`ToolInvocation` + `tool=discord_send`): no log. Proceed to dispatch.

  b. **`Recognized` + deprecated** (every other recognized shape — `tool=send_discord_message`, `TextMessage` + `metadata.discord.{channel_id, embeds, files, reply_to}`): emit a structured deprecation log line (`event="deprecated_shape"`, `shape_name`, `sender`, `envelope_id`, `canonical_equivalent`). Proceed to dispatch.

  c. **`Unrecognized`**:
   - Emit a structured failure log line (`event="unrecognized_shape"`, `envelope_kind`, `unrecognized_fields`, `sender`, `envelope_id`, `canonical_equivalent`). The failure path is more telemetry-valuable than the warning path; both deserve coverage.
   - Publish a failed-delivery `Acknowledgment` via existing `_reply(incoming, note, urgency="yellow")` with the canonical-equivalent text in the note.
   - Ack the inbound (`self._handle.ack(envelope.id)`) so it does not redeliver.
   - Return without dispatching.

**3. Dispatch** (recognized cases only): existing routing path. `_deliver_text_message` for `TextMessage`; `_dispatch(tool, args, envelope)` for `ToolInvocation`. Both paths converge on `_send` for the canonical message-out.

**4. Result `Acknowledgment`s unchanged.** Successful sends (canonical or back-compat) produce the existing green-on-success / yellow-on-partial Ack pattern. The ONLY new Ack type is the validator-emitted yellow failed-delivery Ack from path 2c. Senders see no new Ack noise for normal sends; the deprecation signal lives in logs, not in result Acks.

Two design calls baked into the flow:

- **Structured log schema, not free-form prose.** Field names fixed: `event`, `shape_name`, `envelope_kind`, `sender`, `envelope_id`, `unrecognized_fields`, `canonical_equivalent`. Aggregation by `event + shape_name` answers "is anyone still using old shapes" for the eventual deprecation-removal ticket. Without a schema, telemetry can't make that call.
- **Unrecognized-path ordering.** Publish failed-delivery Ack first, then ack the inbound. Existing `_reply` (line 856 of `endpoint.py`) catches and logs its own publish failures, so the inbound-ack is reachable either way; the ordering is for log-trace clarity (the outbound Ack is the meaningful event; the inbound-ack is bookkeeping).

### Error Handling

Five error surfaces, defended in layers using existing machinery:

**1. Validator-internal exception.** If `shape_validator.validate()` itself raises (catalog bug, malformed envelope from a test fixture), `deliver()` catches via the existing exception handler and produces a yellow Ack: `"validator failed: <repr(exc)>"`. Inbound acked, no redelivery. Same pattern as today's `except Exception` around dispatch (`endpoint.py` lines 671–673).

**2. Pydantic `_DiscordSendArgs` `ValidationError`** (canonical-path-only — missing `channel_id`, typo, type mismatch). Caught at the existing `_v(model, raw)` helper (`endpoint.py` lines 781–785) which already translates `ValidationError → _ToolError → yellow Ack`. No new code; `extra="forbid"` makes the failure surface earlier and cleaner.

**3. Multi-field unrecognized envelope.** ONE failed-delivery Ack listing all unrecognized fields, not N Acks. Reasoning: senders should see the full failure surface in one delivery, not chase N redeliveries. Note text uses plural form: `"Unrecognized fields [...] on <Kind>. Canonical: tool=discord_send with the corresponding args."`

**4. Failed-delivery Ack publish failure** (network blip, bus down). Existing `_reply` already catches and logs (`"discord reply publish failed for %s"`, line 856–857). Inbound is acked regardless, so no redelivery storm. *Footnoted trade:* in the deeply-broken-bus scenario where both the original dispatch and the failed-delivery Ack fail to publish, the sender does not see the failure. Defensible trade — "no redelivery storm" chosen over "guaranteed failure visibility" for the rare double-failure case. Noted here explicitly so it is not forgotten.

**5. Empty-send guard** (`tool=discord_send` with no `text`, no `embeds`, no `files`). Existing `_send()` already raises `_ToolError("send: one of 'text' or 'embeds' is required")` (line 1387–1388). Spec extends the message to include `files`: `"send: one of 'text', 'embeds', or 'files' is required"`. Yellow Ack via the existing path. Single-line code change.

Two error-handling decisions baked in:

- **No new exception types.** `_ToolError` covers the user-error class; the failed-delivery Ack uses `_reply` directly without going through `_ToolError`. Failed-delivery is a shape error caught BEFORE dispatch, not a tool error.
- **No retries.** A bad shape stays bad on redelivery. Inbound is acked once the failed-delivery Ack is published; the sender owns the fix.

### Testing

**Load-bearing regression test** — the single test that proves we kept the silent-drop-class promise to Jeff:

`test_unrecognized_field_produces_failed_delivery_ack` in `packages/agent-core-discord/tests/test_endpoint_outbound.py`. End-to-end behavior test. Construct a `TextMessage` envelope with `metadata.discord.mystery_field`, hand it to `DiscordEndpoint.deliver()`, assert:
- (a) no Discord API call was made;
- (b) a yellow `Acknowledgment` envelope was published with `in_reply_to=<original_envelope_id>` and a note containing `"Unrecognized field 'metadata.discord.mystery_field'"`;
- (c) the inbound was acked;
- (d) `_send` was never reached.

**Validator unit tests** (NEW `packages/agent-core-discord/tests/test_shape_validator.py`):
- Per recognized shape (one row per catalog entry, ~7–8): validator returns `Recognized(shape_name, deprecation_log_line_or_None)`.
- Per unrecognized shape: `Unrecognized(fields, canonical_equivalent)` with the right text.
- Nested-path: `metadata.discord.foo.bar.baz` returns `Unrecognized(fields=['metadata.discord.foo'], ...)` — first unknown step.
- Multi-field at same level: enumerates all unrecognized fields in one return value.

**Args unit tests** (NEW `packages/agent-core-discord/tests/test_discord_send_args.py`):
- `extra="forbid"` rejects `_DiscordSendArgs(channel_id="X", mystery_field="Y")` with `ValidationError`.
- Required `channel_id` rejection.
- Each optional field independently accepted.

**Endpoint integration tests** (extending `test_endpoint_outbound.py`):
- Canonical path: `tool=discord_send` + canonical args delivers, no log, green Ack.
- Deprecated paths (one row per recognized old shape): delivers, structured deprecation log line emitted with all named fields (`event`, `shape_name`, `sender`, `envelope_id`, `canonical_equivalent`) — not just `event + shape_name`. The full-schema assertion is the contract test for the structured-log schema.
- Multi-field unrecognized: ONE failed-delivery Ack listing both fields.
- Empty-send guard: `discord_send` with no text/embeds/files → yellow Ack with the extended `"one of 'text', 'embeds', or 'files' is required"` message.
- Validator-internal-exception: catalog bug → yellow Ack with `"validator failed: ..."`.

**Back-compat regression tests** (each also asserts the deprecation log fires):
- `TextMessage + metadata.discord.channel_id` (plain text).
- `ToolInvocation + tool=send_discord_message + args.{channel_id, text, embeds}`.
- `ToolInvocation + tool=send_discord_message + args.{channel_id, text, files}`.
- `TextMessage + metadata.discord.embeds` (the poster-child, now also asserted via test).

The aggregation key (`event="deprecated_shape"` + `shape_name`) is what the deprecation-removal ticket later watches.

**Test count expectation:** ~15–20 new tests.

## Next-ticket triggers (deferred)

- **Deprecation removal of old shapes.** Triggered when the deprecation-readiness telemetry (aggregating on `event="deprecated_shape"` + `shape_name`) shows no usage of a given legacy shape for N days. Per-shape removal allowed; not all-or-nothing.
- **Generalization to other surfaces.** Triggered when a second adapter (Slack, email, Notion) earns a similar class of silent-drop bug. At N=2 of the pattern extract a shared `shape_validator` utility (rule of three).
- **Cross-adapter `.get()` audit.** Triggered if a second silent-drop bug is discovered elsewhere in this adapter or another. The pattern is bounded enough today; broaden when class-shape is named twice.
- **Args-model expansion** for newer Discord send-time features (interactions, modals, voice). Triggered by named symptom in agent code, not pre-emptively. The `_DiscordSendArgs` model with `extra="forbid"` makes each addition a one-line localized change.
