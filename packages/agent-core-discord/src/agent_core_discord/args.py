"""Pydantic args models for the DiscordEndpoint tool surface.

Each tool's args dict is validated through one of these models inside the
tool dispatcher. Validation errors become user-facing 'error: ...' notes on
the Acknowledgment reply.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class _SendArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None
    reply_to: str | None = None
    files: list[str] | None = None


class _EditArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    text: str | None = None
    embeds: list[dict[str, Any]] | None = None


class _ReactArgs(BaseModel):
    channel_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    emoji: str = Field(min_length=1)


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
