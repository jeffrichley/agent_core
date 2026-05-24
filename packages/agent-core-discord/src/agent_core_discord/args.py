"""Pydantic args models for the DiscordEndpoint tool surface.

Each tool's args dict is validated through one of these models inside the
tool dispatcher. Validation errors become user-facing 'error: ...' notes on
the Acknowledgment reply.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _SendArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    reply_to: str | None = None
    files: list[str] | None = None
    cleanup_inbound_message_id: str | None = None


class _EditArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    cleanup_inbound_message_id: str | None = None


class _ReactArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    emoji: str = Field(min_length=1)
    cleanup_inbound_message_id: str | None = None


class _FetchArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    # Discord's API caps a single history request at 100; discord.py paginates
    # underneath. 500 keeps a fetch under ~5 round-trips while still useful.
    limit: int = Field(default=50, ge=1, le=500)
    before: str | None = None


class _DownloadAttachmentsArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    # Cap to keep an agent from kicking off thousands of HTTP fetches.
    attachment_urls: list[str] = Field(max_length=50)


class _ListChannelsArgs(BaseModel):
    guild_id: str | None = None


class _GetChannelInfoArgs(BaseModel):
    channel_id: str = Field(min_length=1)


class _SendBriefingArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    date_line: str = Field(min_length=1)
    focus: str = ""
    calendar: str = ""
    critical_items: list[str] = Field(default_factory=list)
    warning_items: list[str] = Field(default_factory=list)
    cleanup_inbound_message_id: str | None = None


class _CreatePollArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=300)
    answers: list[str] = Field(min_length=2, max_length=10)
    duration_hours: int = Field(default=24, ge=1, le=168)
    multiple: bool = False


class _CreateScheduledEventArgs(BaseModel):
    guild_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    start_time: str = Field(min_length=1)
    end_time: str | None = None
    description: str = ""
    entity_type: Literal["stage", "voice", "external"] = "external"
    channel_id: str | None = None
    location: str = ""

    @model_validator(mode="after")
    def _entity_fields(self) -> Self:
        if self.entity_type == "external":
            if not (self.location or "").strip():
                raise ValueError("external events require non-empty location")
            if not (self.end_time or "").strip():
                raise ValueError("external events require end_time (ISO 8601)")
        elif self.entity_type in ("stage", "voice"):
            if not self.channel_id:
                raise ValueError("stage and voice events require channel_id")
        return self


class _CancelScheduledEventArgs(BaseModel):
    guild_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)


class _ListScheduledEventsArgs(BaseModel):
    guild_id: str = Field(min_length=1)


class _CreateThreadArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    message_id: str | None = None


class _SendTypingArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    duration_seconds: float = Field(default=5.0, ge=0.5, le=10.0)


class _DiscordSendArgs(BaseModel):
    """Canonical args for tool=discord_send (#114).

    Pydantic extra='forbid' is the second strict layer behind the
    envelope-level shape_validator. The validator catches unrecognized
    fields BEFORE dispatch (yellow Ack via _reply); ValidationError
    here catches them at dispatch (yellow Ack via _ToolError). Both
    paths surface to the sender — no silent drop.

    The canonical field set must stay in lockstep with
    shape_validator._KNOWN_CANONICAL_SEND_ARGS. The
    test_canonical_keys_in_sync_with_validator_catalog test asserts
    the invariant.

    allowed_mentions and components are future-proof slots: the args
    model accepts them, but the adapter's _send() does not yet pass
    them through to discord.py. A caller that sets them today gets a
    successful send with the option silently unapplied. A future
    ticket adds the wiring in _send when there is named demand; no
    args-model migration needed.
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
