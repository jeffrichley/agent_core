"""Envelope wire format — Pydantic models for the bus's universal message shape.

Every message that crosses the bus is an Envelope. `kind` is an open str
field. Built-in kinds (BUILTIN_KINDS) get strict Pydantic payload validation
via the EnvelopePayload discriminated union; plugin-registered kinds carry
dict payloads validated by the plugin's own code. See
docs/superpowers/specs/2026-05-25-envelope-extension-hookspec-design.md.

For kind=Event, the inner `Event.payload.type` is open-ended for domain events.

The `from_` field defaults to "" because the bus stamps it at publish time
(see BusHandle in handle.py). Endpoints do not need to know their own name.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FileAttachment(BaseModel):
    """File attachment on a TextMessage envelope.

    `path` is the local filesystem path discord-pepper reads via
    `discord.File(path)`. Validation runs at envelope publish time so
    typos / missing keys surface synchronously at the publishing
    agent's send() call, not as a later yellow Ack from the adapter.

    `extra='allow'` permits aspirational fields (filename override,
    description, spoiler) to pass validation; the adapter currently
    only consumes `path`. New fields wire to the adapter incrementally,
    named-symptom-bound.
    """

    path: str = Field(min_length=1)
    model_config = ConfigDict(extra="allow")


class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list[FileAttachment] = Field(default_factory=list)


class EventPayload(BaseModel):
    """Domain events. The `data` dict is intentionally open-ended; bus does not validate."""

    kind: Literal["Event"] = "Event"
    type: str
    schema_version: str = "1"
    data: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationPayload(BaseModel):
    kind: Literal["ToolInvocation"] = "ToolInvocation"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class CancellationPayload(BaseModel):
    kind: Literal["Cancellation"] = "Cancellation"
    reason: str | None = None


class ProgressPayload(BaseModel):
    kind: Literal["Progress"] = "Progress"
    status: Literal["working", "blocked", "complete"]
    note: str | None = None
    percent: float | None = None


class AcknowledgmentPayload(BaseModel):
    kind: Literal["Acknowledgment"] = "Acknowledgment"
    of: str
    note: str | None = None


class NotificationPayload(BaseModel):
    """External-source inbound notification.

    Published by the inbound-notifications router when a per-source
    connector classifies an event Allow. ``source`` is the connector
    name ("github", "gmail", "calendar"). ``reason`` is the
    connector-supplied human-readable justification (audit trail).
    ``landed_at`` is when the source event happened (e.g., the
    PR ``updated_at`` for GitHub, the ``Date:`` header for Gmail).
    ``poll_discovered_at`` is set only for cycle-based connectors
    (Gmail IMAP IDLE, calendar polls); absent for push sources.
    ``body`` is the connector-specific payload — the being's
    inbox-render handler dispatches on ``source`` to format it.

    See docs/superpowers/specs/2026-06-20-inbound-notifications-design.md.
    """

    kind: Literal["Notification"] = "Notification"
    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    landed_at: datetime
    poll_discovered_at: datetime | None = None
    body: dict[str, Any] = Field(default_factory=dict)


EnvelopePayload = Annotated[
    TextMessagePayload
    | EventPayload
    | ToolInvocationPayload
    | CancellationPayload
    | ProgressPayload
    | AcknowledgmentPayload
    | NotificationPayload,
    Field(discriminator="kind"),
]

# Built-in kinds with typed Pydantic payload models. Plugin-registered kinds
# (e.g., "Desire" from pepper-roots) carry dict payloads validated by the
# plugin's own code; the discriminated union does not match them.
BUILTIN_KINDS: frozenset[str] = frozenset({
    "TextMessage",
    "Event",
    "ToolInvocation",
    "Cancellation",
    "Progress",
    "Acknowledgment",
    "Notification",
})


class Envelope(BaseModel):
    """The bus's universal wire format.

    `kind` is an open str field. Built-in kinds in BUILTIN_KINDS get strict
    Pydantic payload validation via EnvelopePayload; plugin kinds carry
    dict payloads that the plugin's own code is responsible for validating.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    correlation_id: str
    in_reply_to: str | None = None
    from_: str = Field(default="", alias="from")
    to: str
    kind: str
    payload: EnvelopePayload | dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["green", "yellow", "red"] = "green"
    expires_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_kind_matches_payload(self) -> "Envelope":
        """Enforce that the outer kind matches the payload's kind.

        Built-in kinds: payload MUST validate as the typed Pydantic model
        (TextMessagePayload, EventPayload, etc.). If the typed branch of
        the discriminated union failed to match (missing required fields,
        wrong shape), Pydantic falls through to ``dict`` — we reject that
        here to preserve the pre-extension backward-compat invariant that
        built-in payloads are always strict.

        Plugin kinds: payload is a dict whose ``"kind"`` key must match
        the envelope's ``kind`` field. Plugin code is responsible for
        validating its own payload shape (no bus-side payload-validator
        hookspec in v0; see spec §1.5).
        """
        if self.kind in BUILTIN_KINDS and isinstance(self.payload, dict):
            raise ValueError(
                f"Envelope kind '{self.kind}' is a built-in; payload must validate "
                f"as the typed model. Got dict (typed validation likely failed — "
                f"check for missing required fields)."
            )

        if hasattr(self.payload, "kind"):
            payload_kind = self.payload.kind
        elif isinstance(self.payload, dict):
            payload_kind = self.payload.get("kind")
        else:
            payload_kind = None

        if payload_kind != self.kind:
            raise ValueError(
                f"Envelope kind '{self.kind}' does not match payload kind '{payload_kind}'"
            )
        return self


class EndpointInfo(BaseModel):
    """Directory entry exposed by BusHandle.endpoints()."""

    name: str
    description: str = ""
