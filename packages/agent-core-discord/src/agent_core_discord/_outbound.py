"""Outbound delivery mixin for DiscordEndpoint.

Move-only extraction from endpoint.py (issue #442, Step 4 of F-B6).
Imports nothing from endpoint.py to avoid circular imports.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope, TextMessagePayload
from agent_core.bus.protocol import EndpointUnavailable
from agent_core_discord._exceptions import _ToolError
from agent_core_discord.args import (
    _CancelScheduledEventArgs,
    _CreatePollArgs,
    _CreateScheduledEventArgs,
    _CreateThreadArgs,
    _DiscordSendArgs,
    _DownloadAttachmentsArgs,
    _EditArgs,
    _FetchArgs,
    _GetChannelInfoArgs,
    _ListChannelsArgs,
    _ListScheduledEventsArgs,
    _ReactArgs,
    _SendArgs,
    _SendBriefingArgs,
    _SendTypingArgs,
)
from agent_core_discord.chunking import smart_chunk_discord
from agent_core_discord.send_retry import channel_send_with_retries, is_retryable_discord_send_error
from agent_core_discord.shape_validator import Recognized, Unrecognized
from agent_core_discord.shape_validator import validate as validate_shape

log = logging.getLogger(__name__)

# Pepper-facing tool names from cutover #03 map to the internal dispatcher keys.
_TOOL_ALIASES: dict[str, str] = {
    "send_discord_message": "send",
    "discord_send": "discord_send",  # #114: canonical passthrough
    "edit_message": "edit",
    "add_reaction": "react",
    "fetch_messages": "fetch",
}


def _canonical_tool(tool: str) -> str:
    return _TOOL_ALIASES.get(tool, tool)


# Discord caps total embed content per message at 6000 characters across all
# embeds. Validate before send so we get a clear _ToolError rather than an
# opaque HTTPException from the API.
_DISCORD_EMBED_TOTAL_CHAR_CAP = 6000


def _embed_char_count(embed: dict[str, Any]) -> int:
    """Approximate Discord's len(Embed) for a raw dict.

    Sums title, description, footer.text, author.name, and the name+value of
    each field. This mirrors discord.Embed.__len__ on the real object.
    """
    n = 0
    n += len(embed.get("title") or "")
    n += len(embed.get("description") or "")
    footer = embed.get("footer") or {}
    n += len(footer.get("text") or "") if isinstance(footer, dict) else 0
    author = embed.get("author") or {}
    n += len(author.get("name") or "") if isinstance(author, dict) else 0
    for f in embed.get("fields") or []:
        if isinstance(f, dict):
            n += len(f.get("name") or "") + len(f.get("value") or "")
    return n


def _check_embeds_within_caps(embeds: list[dict[str, Any]]) -> None:
    """Raise _ToolError if the embeds dict list exceeds Discord's total cap."""
    total = sum(_embed_char_count(e) for e in embeds if isinstance(e, dict))
    if total > _DISCORD_EMBED_TOTAL_CHAR_CAP:
        raise _ToolError(
            f"embeds total {total} chars exceeds Discord cap of {_DISCORD_EMBED_TOTAL_CHAR_CAP}"
        )


def _serialize_poll(poll: Any) -> dict[str, Any] | None:
    """Convert a ``discord.Poll`` to a JSON-friendly dict.

    Returns ``None`` when ``poll`` is ``None`` (the common case — most
    messages aren't polls). When present, the dict carries the question
    text, answer list (each with id / text / emoji / vote_count if loaded),
    multiselect flag, duration in seconds, ISO expires_at, finalised
    flag, and total vote count. Mirrors the ``discord.Poll`` shape closely
    enough that agents reading the dict don't need access to discord.py's
    types to understand what they're looking at.
    """
    if poll is None:
        return None
    # ``discord.Poll.question`` is a ``@property`` returning a flat ``str``
    # (it reads ``self._question_media.text`` internally — see discord.py
    # poll.py). Earlier code here read ``.question.text`` which silently
    # returned ``""`` against every real poll; the regression was caught
    # on testbot 2026-05-05 Phase 6 verification.
    question_text = str(getattr(poll, "question", "") or "")
    answers_out: list[dict[str, Any]] = []
    for ans in getattr(poll, "answers", None) or []:
        emoji = getattr(ans, "emoji", None)
        answers_out.append(
            {
                "id": int(getattr(ans, "id", 0)) if getattr(ans, "id", None) is not None else 0,
                "text": str(getattr(ans, "text", "")),
                "emoji": str(emoji) if emoji is not None else None,
                "vote_count": int(getattr(ans, "vote_count", 0) or 0),
            }
        )
    duration = getattr(poll, "duration", None)
    duration_seconds: int | None = None
    if duration is not None:
        # ``discord.Poll.duration`` is a ``datetime.timedelta``; serialize
        # to seconds for transport. Tests may pass other shapes.
        total_seconds = getattr(duration, "total_seconds", None)
        if callable(total_seconds):
            duration_seconds = int(total_seconds())
    expires_at = getattr(poll, "expires_at", None)
    expires_iso: str = ""
    if expires_at is not None:
        iso = getattr(expires_at, "isoformat", None)
        if callable(iso):
            expires_iso = iso()
    is_finalised_method = getattr(poll, "is_finalised", None)
    is_finalised = bool(is_finalised_method()) if callable(is_finalised_method) else False
    return {
        "question": question_text,
        "answers": answers_out,
        "multiselect": bool(getattr(poll, "multiselect", False)),
        "duration_seconds": duration_seconds,
        "expires_at": expires_iso,
        "is_finalised": is_finalised,
        "total_votes": int(getattr(poll, "total_votes", 0) or 0),
    }


class _OutboundMixin:
    async def deliver(self, envelope: Envelope) -> None:
        """Handle ToolInvocation and TextMessage envelopes."""
        if self._handle is None:
            raise EndpointUnavailable(f"discord '{self.name}' not started")

        # #114: strict-mode validator. Only consulted for kinds the adapter
        # dispatches (TextMessage / ToolInvocation); other kinds fall through
        # to the existing else-branch unchanged.
        if envelope.kind in ("TextMessage", "ToolInvocation"):
            try:
                validation = validate_shape(envelope)
            except Exception as exc:
                log.exception("discord(%s): validator raised", self.name)
                await self._reply(
                    envelope,
                    f"validator failed: {exc!r}",
                    urgency="yellow",
                )
                await self._handle.ack(envelope.id)
                return
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
                field_list = validation.fields
                if len(field_list) == 1:
                    note = (
                        f"Unrecognized field {field_list[0]!r} on "
                        f"{envelope.kind}. Canonical: "
                        f"{validation.canonical_equivalent}"
                    )
                else:
                    note = (
                        f"Unrecognized fields {field_list} on "
                        f"{envelope.kind}. Canonical: "
                        f"{validation.canonical_equivalent}"
                    )
                await self._reply(envelope, note, urgency="red")
                await self._handle.ack(envelope.id)
                return
            if isinstance(validation, Recognized) and validation.deprecation_log_line:
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

        if envelope.kind == "TextMessage":
            try:
                result = await self._deliver_text_message(envelope)
                urg: Literal["green", "yellow", "red"] = (
                    "yellow"
                    if isinstance(result, dict) and result.get("status") == "partial"
                    else "green"
                )
                await self._reply(envelope, json.dumps(result), urgency=urg)
            except _ToolError as exc:
                await self._reply(envelope, f"error: {exc}", urgency="yellow")
            except Exception as exc:
                if is_retryable_discord_send_error(exc):
                    raise EndpointUnavailable(
                        f"discord '{self.name}': transient error: {exc}"
                    ) from exc
                log.exception("discord TextMessage delivery raised")
                await self._reply(envelope, f"error: {exc}", urgency="yellow")
            await self._handle.ack(envelope.id)
            return
        if envelope.kind != "ToolInvocation":
            await self._reply(envelope, f"warning: unsupported envelope kind '{envelope.kind}'")
            await self._handle.ack(envelope.id)
            return

        tool = envelope.payload.tool  # type: ignore[union-attr]
        args = envelope.payload.args  # type: ignore[union-attr]

        try:
            result = await self._dispatch(tool, args, envelope)
            urg2: Literal["green", "yellow", "red"] = (
                "yellow"
                if isinstance(result, dict) and result.get("status") == "partial"
                else "green"
            )
            await self._reply(envelope, json.dumps(result), urgency=urg2)
        except _ToolError as exc:
            await self._reply(envelope, f"error: {exc}", urgency="yellow")
        except Exception as exc:
            if is_retryable_discord_send_error(exc):
                raise EndpointUnavailable(
                    f"discord '{self.name}': transient error: {exc}"
                ) from exc
            log.exception("discord tool '%s' raised", tool)
            await self._reply(envelope, f"error: {exc}", urgency="yellow")

        await self._handle.ack(envelope.id)

    async def _deliver_text_message(self, envelope: Envelope) -> dict:
        """Route a bus TextMessage to Discord send.

        If ``metadata.discord.embeds`` is set, the embed dicts ride along
        on the send call. The text portion is optional in that case — an
        empty ``payload.text`` collapses to an embeds-only message; a
        non-empty text is sent as content alongside the embeds (chunked
        normally if it exceeds the Discord cap).
        """
        if not isinstance(envelope.payload, TextMessagePayload):
            raise _ToolError("TextMessage envelope payload is invalid")

        discord_meta = envelope.metadata.get("discord", {})
        channel_id = None
        reply_to: str | None = None
        embeds_data: list[dict[str, Any]] | None = None
        if isinstance(discord_meta, dict):
            channel_id = discord_meta.get("channel_id")
            # Prefer explicit reply_to; else message_id (legacy); else resolve
            # from bus in_reply_to → inbound envelope id (agent replies often
            # only set in_reply_to).
            reply_to = discord_meta.get("reply_to") or discord_meta.get("message_id")
            if reply_to is not None:
                reply_to = str(reply_to)
            raw_embeds = discord_meta.get("embeds")
            if isinstance(raw_embeds, list) and raw_embeds:
                # Defensive copy: caller may mutate metadata after publish.
                embeds_data = [dict(e) for e in raw_embeds if isinstance(e, dict)]
        if (
            not reply_to
            and envelope.in_reply_to
            and envelope.in_reply_to in self._inbound_envelope_discord
        ):
            mapped_mid, mapped_ch = self._inbound_envelope_discord[envelope.in_reply_to]
            reply_to = mapped_mid
            if not channel_id:
                channel_id = mapped_ch
        if not channel_id:
            channel_id = self.outbound_channel_id
        if not channel_id:
            raise _ToolError(
                "TextMessage requires metadata.discord.channel_id or endpoint outbound_channel_id"
            )

        # When embeds ride on the envelope, an empty ``payload.text`` is
        # the embeds-only path: collapse to ``None`` so ``_send`` skips
        # chunking and dispatches a single message with the embeds. Without
        # embeds, leave the text alone — pre-existing behavior was to pass
        # the raw payload text straight through.
        text_for_send: str | None = envelope.payload.text
        if embeds_data and not text_for_send:
            text_for_send = None

        # Translate bus-side FileAttachment list to verb-side files list.
        # Tight FileAttachment validation already ran at envelope publish
        # time, so payload.attachments is a list of validated models.
        files = [a.path for a in envelope.payload.attachments] or None

        # Translate bus-level in_reply_to to inbound's Discord message_id for
        # typing cleanup (#84). Cache miss / missing metadata / no in_reply_to
        # all degrade to None → cleanup no-ops → TTL safety net.
        cleanup_inbound_message_id: str | None = None
        if envelope.in_reply_to:
            inbound = self._recent_inbounds.get(envelope.in_reply_to)
            if inbound:
                discord_meta = (inbound.metadata or {}).get("discord") or {}
                cleanup_inbound_message_id = discord_meta.get("message_id")

        args = _SendArgs(
            channel_id=str(channel_id),
            text=text_for_send,
            embeds=embeds_data,
            reply_to=reply_to,
            files=files,
            cleanup_inbound_message_id=cleanup_inbound_message_id,
        )
        return await self._send(args)

    async def _dispatch(self, tool: str, args: dict, env: Envelope) -> Any:
        tool = _canonical_tool(tool)

        def _v(model: Any, raw: dict) -> Any:
            try:
                return model(**raw)
            except ValidationError as exc:
                raise _ToolError(f"{tool}: {exc}") from exc

        # For tools that require channel_id, inject it via _resolve_channel_id
        # when the caller omitted it (auto-echo via in_reply_to cache).
        def _inject_channel_id(raw: dict) -> dict:
            if "channel_id" not in raw or not raw["channel_id"]:
                raw = dict(raw)
                raw["channel_id"] = self._resolve_channel_id(env)
            # Typing-cleanup translation (#84): bus-level in_reply_to →
            # inbound's Discord message_id via _recent_inbounds. Cache miss
            # / missing metadata degrade to None → cleanup no-ops → TTL net.
            if env.in_reply_to and "cleanup_inbound_message_id" not in raw:
                inbound = self._recent_inbounds.get(env.in_reply_to)
                if inbound:
                    discord_meta = (inbound.metadata or {}).get("discord") or {}
                    cid = discord_meta.get("message_id")
                    if cid:
                        raw = dict(raw)  # copy-on-write if not already copied
                        raw["cleanup_inbound_message_id"] = cid
            return raw

        if tool == "discord_send":
            return await self._send(
                _v(_DiscordSendArgs, _inject_channel_id(args))
            )
        if tool == "send":
            return await self._send(_v(_SendArgs, _inject_channel_id(args)))
        if tool == "edit":
            return await self._edit(_v(_EditArgs, _inject_channel_id(args)))
        if tool == "react":
            return await self._react(_v(_ReactArgs, _inject_channel_id(args)))
        if tool == "fetch":
            return await self._fetch(_v(_FetchArgs, args))
        if tool == "download_attachments":
            return await self._download_attachments(_v(_DownloadAttachmentsArgs, args))
        if tool == "list_channels":
            return await self._list_channels(_v(_ListChannelsArgs, args))
        if tool == "get_channel_info":
            return await self._get_channel_info(_v(_GetChannelInfoArgs, args))
        if tool == "send_briefing":
            return await self._send_briefing(_v(_SendBriefingArgs, _inject_channel_id(args)))
        if tool == "create_poll":
            return await self._create_poll(_v(_CreatePollArgs, args))
        if tool == "create_scheduled_event":
            return await self._create_scheduled_event(_v(_CreateScheduledEventArgs, args))
        if tool == "cancel_scheduled_event":
            return await self._cancel_scheduled_event(_v(_CancelScheduledEventArgs, args))
        if tool == "list_scheduled_events":
            return await self._list_scheduled_events(_v(_ListScheduledEventsArgs, args))
        if tool == "create_thread":
            return await self._create_thread(_v(_CreateThreadArgs, args))
        if tool == "send_typing":
            return await self._send_typing(_v(_SendTypingArgs, _inject_channel_id(args)))
        raise _ToolError(f"unknown tool '{tool}'")

    async def _reply(
        self,
        incoming: Envelope,
        note: str,
        *,
        urgency: Literal["green", "yellow", "red"] = "green",
    ) -> None:
        assert self._handle is not None
        ack = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=incoming.correlation_id,
            in_reply_to=incoming.id,
            to=incoming.from_,
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of=incoming.id, note=note),
            urgency=urgency,
            created_at=datetime.now(UTC),
        )
        try:
            await self._handle.publish(ack)
        except Exception:
            log.exception("discord reply publish failed for %s", incoming.id)

    async def _resolve_channel(self, channel_id: str):
        ch = self._client.get_channel(channel_id) if self._client else None
        if ch is None and self._client is not None:
            try:
                ch = await self._client.fetch_channel(channel_id)
            except Exception as exc:
                raise _ToolError(f"channel '{channel_id}' not found: {exc}") from exc
        if ch is None:
            raise _ToolError(f"channel '{channel_id}' not found")
        return ch

    async def _send(self, args: _SendArgs) -> dict:
        if args.text is None and not args.embeds and not args.files:
            raise _ToolError(
                "send: one of 'text', 'embeds', or 'files' is required"
            )
        ch = await self._resolve_channel(args.channel_id)

        # Build embeds list (validate via discord.Embed.from_dict).
        embeds = None
        if args.embeds:
            _check_embeds_within_caps(args.embeds)
            try:
                import discord  # type: ignore

                embeds = [discord.Embed.from_dict(e) for e in args.embeds]
            except ImportError:
                # Tests with the fake client have no real discord — pass dicts through.
                embeds = list(args.embeds)
            except Exception as exc:
                raise _ToolError(f"send: invalid embed: {exc}") from exc

        # Build reply reference if reply_to provided.
        reference = None
        if args.reply_to:
            try:
                target = await ch.fetch_message(args.reply_to)
            except Exception:
                target = None
            if target is None:
                raise _ToolError(f"send: reply_to message '{args.reply_to}' not found")
            try:
                import discord  # type: ignore

                reference = discord.MessageReference.from_message(target)
            except (ImportError, AttributeError):
                # Fakes don't need a real reference; pass the message itself as a marker.
                reference = target

        files = None
        if args.files:
            # discord.File accepts a local path or a binary file-like object —
            # not an HTTP URL. Reject URL strings upfront with a clear message
            # so callers don't get confused FileNotFoundError errors.
            for f in args.files:
                if isinstance(f, str) and (f.startswith("http://") or f.startswith("https://")):
                    raise _ToolError(
                        f"send: 'files' must be local paths, not URLs (got {f!r}). "
                        "Use download_attachments first if you need URL bytes."
                    )
            try:
                import discord  # type: ignore

                files = [discord.File(f) for f in args.files]
            except ImportError:
                files = list(args.files)
            except Exception as exc:
                raise _ToolError(f"send: invalid files: {exc}") from exc

        # Only pass kwargs we actually have. discord.py uses MISSING sentinels
        # internally and chokes on explicit None for some fields (e.g. embeds=None
        # raises "object of type 'NoneType' has no len()"). Build the call dict.
        send_kwargs: dict[str, Any] = {}
        if embeds is not None:
            send_kwargs["embeds"] = embeds
        if reference is not None:
            send_kwargs["reference"] = reference
        if files is not None:
            send_kwargs["files"] = files

        if args.text is None:
            new_msg = await channel_send_with_retries(ch, args.text, **send_kwargs)
            if args.reply_to:
                await self._clear_pending_ack(ch, args.reply_to)
            if args.cleanup_inbound_message_id:
                await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)
            mid = str(new_msg.id)
            return {"status": "sent", "message_id": mid, "message_ids": [mid]}

        try:
            text_parts = smart_chunk_discord(args.text)
        except ValueError as exc:
            raise _ToolError(str(exc)) from exc

        message_ids: list[str] = []
        last_error: Exception | None = None
        n = len(text_parts)
        for i, part in enumerate(text_parts):
            is_first = i == 0
            is_last = i == n - 1
            send_part: dict[str, Any] = {}
            if embeds is not None and is_last:
                send_part["embeds"] = embeds
            if reference is not None and is_first:
                send_part["reference"] = reference
            if files is not None and is_first:
                send_part["files"] = files
            try:
                new_msg = await channel_send_with_retries(ch, part, **send_part)
            except Exception as exc:
                last_error = exc
                break
            message_ids.append(str(new_msg.id))

        if last_error is not None:
            if message_ids:
                # Partial delivery: some chunks landed before the error.
                # Return partial regardless of error kind — requeuing the
                # envelope would duplicate already-delivered chunks.
                # Do not clear inbound ack — the conversation may still be
                # awaiting a complete reply; callers get message_ids for
                # what landed.
                return {
                    "status": "partial",
                    "message_ids": message_ids,
                    "error": str(last_error),
                }
            # No messages delivered yet.  Transient HTTP errors (429, 5xx)
            # must propagate to deliver() so the bus can convert them to
            # EndpointUnavailable and requeue.  Wrapping them in _ToolError
            # would cause deliver() to ack, silently dropping recoverable mail.
            if is_retryable_discord_send_error(last_error):
                raise last_error
            raise _ToolError(f"send failed: {last_error}") from last_error

        if args.reply_to:
            await self._clear_pending_ack(ch, args.reply_to)
        if args.cleanup_inbound_message_id:
            await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)

        out: dict[str, Any] = {"status": "sent", "message_ids": message_ids}
        if message_ids:
            out["message_id"] = message_ids[-1]
        return out

    async def _edit(self, args: _EditArgs) -> dict:
        if args.text is None and not args.embeds:
            raise _ToolError("edit: one of 'text' or 'embeds' is required")
        ch = await self._resolve_channel(args.channel_id)
        try:
            msg = await ch.fetch_message(args.message_id)
        except Exception as exc:
            raise _ToolError(f"edit: message '{args.message_id}' not found: {exc}") from exc
        if msg is None:
            raise _ToolError(f"edit: message '{args.message_id}' not found")

        embeds = None
        if args.embeds:
            _check_embeds_within_caps(args.embeds)
            try:
                import discord  # type: ignore

                embeds = [discord.Embed.from_dict(e) for e in args.embeds]
            except ImportError:
                embeds = list(args.embeds)
            except Exception as exc:
                raise _ToolError(f"edit: invalid embed: {exc}") from exc

        # Only pass kwargs we actually have. Real discord.py.Message.edit
        # rejects `embeds=None` ("object of type 'NoneType' has no len()");
        # the right way to "leave embeds alone" is to omit the kwarg.
        edit_kwargs: dict[str, Any] = {}
        if args.text is not None:
            edit_kwargs["content"] = args.text
        if embeds is not None:
            edit_kwargs["embeds"] = embeds
        await msg.edit(**edit_kwargs)
        if args.cleanup_inbound_message_id:
            await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)
        return {"status": "edited", "message_id": args.message_id}

    async def _react(self, args: _ReactArgs) -> dict:
        ch = await self._resolve_channel(args.channel_id)
        try:
            msg = await ch.fetch_message(args.message_id)
        except Exception as exc:
            raise _ToolError(f"react: message '{args.message_id}' not found: {exc}") from exc
        if msg is None:
            raise _ToolError(f"react: message '{args.message_id}' not found")
        await msg.add_reaction(args.emoji)
        # Clear the eyes if this reaction is on a tracked inbound message.
        await self._clear_pending_ack(ch, args.message_id)
        if args.cleanup_inbound_message_id:
            await self._clear_pending_ack(ch, args.cleanup_inbound_message_id)
        return {"status": "reacted", "emoji": args.emoji}

    async def _fetch(self, args: _FetchArgs) -> list[dict]:
        ch = await self._resolve_channel(args.channel_id)
        out: list[dict] = []
        # discord.py's history() returns an async iterator; the fake provides one.
        before = None
        if args.before is not None:
            try:
                before = await ch.fetch_message(args.before)
            except Exception:
                before = None
        async for m in ch.history(limit=args.limit, before=before):
            embeds = [
                e.to_dict() if hasattr(e, "to_dict") else e
                for e in (getattr(m, "embeds", None) or [])
            ]
            attachments = []
            for att in getattr(m, "attachments", None) or []:
                attachments.append(
                    {
                        "filename": att.filename,
                        "url": att.url,
                        "content_type": getattr(att, "content_type", None) or "unknown",
                        "size_bytes": int(getattr(att, "size", 0)),
                    }
                )
            author = getattr(m, "author", None)
            out.append(
                {
                    "id": str(m.id),
                    "channel_id": str(getattr(m, "channel_id", args.channel_id)),
                    "author_id": str(getattr(author, "id", "")),
                    "author_display_name": getattr(author, "display_name", "")
                    or getattr(author, "name", "")
                    or "",
                    "is_bot": bool(getattr(author, "bot", False)),
                    "content": getattr(m, "content", "") or "",
                    "created_at": m.created_at.isoformat()
                    if getattr(m, "created_at", None)
                    else "",
                    "embeds": embeds,
                    "attachments": attachments,
                    # ``message.poll`` is a first-class Discord attribute that
                    # returns a ``discord.Poll`` when the message carries a
                    # poll, otherwise ``None``. Surface it so agents reading
                    # channel state can see active polls — without this, a
                    # poll message comes back as ``content: ""`` and looks
                    # empty (caught on testbot 2026-05-05 Phase 6 verb-parity
                    # smoke).
                    "poll": _serialize_poll(getattr(m, "poll", None)),
                }
            )
        return out
