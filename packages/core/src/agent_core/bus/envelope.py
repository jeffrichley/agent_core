"""Envelope wire format — Pydantic models for the bus's universal message shape.

Every message that crosses the bus is an Envelope. `kind` is a closed structural
discriminator; for kind=Event, the inner `Event.payload.type` is open-ended for
domain events.

The `from_` field defaults to "" because the bus stamps it at publish time
(see BusHandle in handle.py). Endpoints do not need to know their own name.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)


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


EnvelopePayload = Annotated[
    Union[
        TextMessagePayload,
        EventPayload,
        ToolInvocationPayload,
        CancellationPayload,
        ProgressPayload,
        AcknowledgmentPayload,
    ],
    Field(discriminator="kind"),
]


class Envelope(BaseModel):
    """The bus's universal wire format."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    correlation_id: str
    in_reply_to: str | None = None
    from_: str = Field(default="", alias="from")
    to: str
    kind: Literal[
        "TextMessage",
        "Event",
        "ToolInvocation",
        "Cancellation",
        "Progress",
        "Acknowledgment",
    ]
    payload: EnvelopePayload
    metadata: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["green", "yellow", "red"] = "green"
    expires_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_kind_matches_payload(self) -> "Envelope":
        """Enforce that the outer kind matches the payload's kind."""
        if self.payload.kind != self.kind:
            raise ValueError(
                f"Envelope kind '{self.kind}' does not match payload kind '{self.payload.kind}'"
            )
        return self


class EndpointInfo(BaseModel):
    """Directory entry exposed by BusHandle.endpoints()."""

    name: str
    description: str = ""
