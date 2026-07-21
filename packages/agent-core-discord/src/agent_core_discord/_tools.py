"""Tool-handler mixin for DiscordEndpoint.

Move-only extraction from endpoint.py (issue #442, Step 4 of F-B6).
Imports nothing from endpoint.py to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_core_discord._exceptions import _PersistError, _ToolError
from agent_core_discord._state import _EndpointState
from agent_core_discord.args import (
    _CancelScheduledEventArgs,
    _CreatePollArgs,
    _CreateScheduledEventArgs,
    _CreateThreadArgs,
    _DownloadAttachmentsArgs,
    _GetChannelInfoArgs,
    _ListChannelsArgs,
    _ListScheduledEventsArgs,
    _SendArgs,
    _SendBriefingArgs,
    _SendTypingArgs,
)
from agent_core_discord.briefing import build_briefing_embeds
from agent_core_discord.send_retry import channel_send_with_retries

log = logging.getLogger(__name__)


def _parse_iso_datetime(label: str, value: str) -> datetime:
    # Trailing-Z only — replacing every "Z" mangles inputs that legitimately
    # contain the letter elsewhere (e.g. "2026-05-04T07:00:00Z#anchorZ").
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _ToolError(f"{label}: invalid datetime {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


_FILENAME_ALLOWED = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(url: str) -> str:
    """Extract a safe filename from a URL.

    Strips path components on both POSIX and Windows, allowlists chars,
    caps length at 128, and falls back to a uuid stub if nothing usable
    remains. The returned name is intended to be appended to a directory
    that the caller has already validated; the caller MUST also assert
    `(target_dir / name).resolve()` stays inside `target_dir.resolve()`.
    """
    raw = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    # Path(...).name strips both / and \ on Windows.
    clean = Path(raw).name
    clean = _FILENAME_ALLOWED.sub("_", clean)
    clean = clean[:128]
    if not clean or clean in (".", ".."):
        clean = f"attach-{uuid.uuid4().hex[:8]}"
    return clean


class _ToolsMixin(_EndpointState):
    async def _download_url(self, url: str) -> tuple[bytes, str]:
        """Fetch a URL's bytes + Content-Type header.

        Returns ``(body, content_type)``. ``content_type`` is the value of
        the response's Content-Type header (empty string if missing).
        Override in tests to avoid network — return a 2-tuple.
        """
        try:
            import httpx
        except ImportError as exc:
            raise _ToolError("download_attachments: httpx not available") from exc
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "")

    async def _persist_attachment(self, *, url: str, subdir: str) -> tuple[Path, int]:
        """Download one URL into <attachments_dir>/<subdir>/ and return the
        resolved path plus byte count. Raises on download failure
        (propagated raw from _download_url) or unsafe path (_PersistError).

        Shared by the download_attachments MCP tool (subdir=message_id) and
        the inbound auto-download path (subdir=envelope_id).
        """
        target_dir = self.attachments_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_resolved = target_dir.resolve()
        filename = _safe_filename(url)
        path = (target_dir / filename).resolve()
        try:
            path.relative_to(target_resolved)
        except ValueError as exc:
            raise _PersistError(f"refused unsafe path for {url!r}") from exc
        # De-dup so two URLs ending in the same name don't silently overwrite.
        if path.exists():
            stem, suffix = path.stem, path.suffix
            path = (target_dir / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}").resolve()
            try:
                path.relative_to(target_resolved)
            except ValueError as exc:
                raise _PersistError(f"refused unsafe dedup path for {url!r}") from exc
        data, _content_type = await self._download_url(url)
        path.write_bytes(data)
        return path, len(data)

    async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict[str, Any]:
        if not args.attachment_urls:
            return {"saved": []}
        saved: list[dict[str, Any]] = []
        for url in args.attachment_urls:
            try:
                path, nbytes = await self._persist_attachment(
                    url=url, subdir=args.message_id
                )
            except _PersistError as exc:
                raise _ToolError(str(exc)) from exc
            except Exception as exc:
                raise _ToolError(f"download failed for {url}: {exc}") from exc
            saved.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    # content_type is now "" — _persist_attachment returns
                    # (path, nbytes) and does not surface the CDN response
                    # content-type. Agents that need the declared type should
                    # use metadata["attachments"][].content_type from the
                    # inbound envelope (Discord's value, more reliable than
                    # the CDN response header). See #76 Task 2.
                    "content_type": "",
                    "size_bytes": nbytes,
                }
            )
        return {"saved": saved}

    async def _list_channels(self, args: _ListChannelsArgs) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self._client is None:
            return out
        for g in self._client.guilds:
            if args.guild_id is not None and str(g.id) != args.guild_id:
                continue
            for ch in g.channels:
                out.append(
                    {
                        "id": str(ch.id),
                        "name": getattr(ch, "name", ""),
                        "type": str(getattr(ch, "type", "text")),
                        # We're iterating from ``g``, so ``g.id`` is always
                        # the right answer. Don't ``getattr(ch, "guild_id")``
                        # — discord.py text channels don't have that flat
                        # attribute (see _get_channel_info comment).
                        "guild_id": str(g.id),
                        "topic": getattr(ch, "topic", "") or "",
                    }
                )
        return out

    async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict[str, Any]:
        ch = await self._resolve_channel(args.channel_id)
        # discord.py text channels expose ``.guild`` (a Guild object), NOT a
        # flat ``.guild_id`` attribute. Reading ``getattr(ch, "guild_id", "")``
        # silently returned "" for every real text channel — caught on testbot
        # 2026-05-05 during Phase 6 verb-parity smoke. DM channels have
        # ``ch.guild is None`` and correctly return "".
        guild = getattr(ch, "guild", None)
        return {
            "id": str(ch.id),
            "name": getattr(ch, "name", ""),
            "type": str(getattr(ch, "type", "text")),
            "guild_id": str(guild.id) if guild is not None else "",
            "topic": getattr(ch, "topic", "") or "",
            "nsfw": bool(getattr(ch, "nsfw", False)),
        }

    async def _resolve_guild(self, guild_id: str) -> Any:
        if self._client is None:
            raise _ToolError("guild lookup: client not ready")
        get_guild = getattr(self._client, "get_guild", None)
        if get_guild is None:
            raise _ToolError("client has no get_guild")
        keys: list[Any] = [guild_id, str(guild_id)]
        if guild_id.isdigit():
            keys.insert(0, int(guild_id))
        seen: set[Any] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            try:
                g = get_guild(key)
            except TypeError:
                continue
            if g is not None:
                return g
        raise _ToolError(f"guild '{guild_id}' not found")

    async def _send_briefing(self, args: _SendBriefingArgs) -> dict[str, Any]:
        embeds = build_briefing_embeds(
            date_line=args.date_line,
            focus=args.focus,
            calendar=args.calendar,
            critical_items=list(args.critical_items),
            warning_items=list(args.warning_items),
        )
        return await self._send(
            _SendArgs(
                channel_id=args.channel_id,
                text=None,
                embeds=embeds,
                cleanup_inbound_message_id=args.cleanup_inbound_message_id,
            )
        )

    async def _create_poll(self, args: _CreatePollArgs) -> dict[str, Any]:
        try:
            import discord
        except ImportError as exc:
            raise _ToolError("create_poll requires discord.py") from exc
        ch = await self._resolve_channel(args.channel_id)
        poll = discord.Poll(
            args.question,
            timedelta(hours=args.duration_hours),
            multiple=args.multiple,
        )
        for ans in args.answers:
            text = (ans or "").strip()
            if not text:
                raise _ToolError("create_poll: empty answer text")
            poll = poll.add_answer(text=text[:300])
        new_msg = await channel_send_with_retries(ch, None, poll=poll)
        mid = str(new_msg.id)
        return {"status": "sent", "message_id": mid, "message_ids": [mid]}

    async def _create_scheduled_event(self, args: _CreateScheduledEventArgs) -> dict[str, Any]:
        try:
            import discord
        except ImportError as exc:
            raise _ToolError("create_scheduled_event requires discord.py") from exc
        guild = await self._resolve_guild(args.guild_id)
        start = _parse_iso_datetime("start_time", args.start_time)
        entity_map = {
            "stage": discord.EntityType.stage_instance,
            "voice": discord.EntityType.voice,
            "external": discord.EntityType.external,
        }
        kwargs: dict[str, Any] = {
            "name": args.name[:100],
            "start_time": start,
            "entity_type": entity_map[args.entity_type],
            "privacy_level": discord.PrivacyLevel.guild_only,
        }
        if args.description:
            kwargs["description"] = args.description[:1000]
        if args.entity_type == "external":
            kwargs["location"] = args.location.strip()
            kwargs["end_time"] = _parse_iso_datetime("end_time", args.end_time or "")
        else:
            ch = await self._resolve_channel(args.channel_id or "")
            # Reject channels whose type does not match the entity_type before
            # the API does. discord.py's ChannelType uses 'stage_voice' for
            # stage channels and 'voice' for voice channels; matching those
            # exact names keeps us in sync with discord.py's enum.
            ch_type = getattr(ch, "type", None)
            ch_type_name = str(getattr(ch_type, "name", ch_type) or "")
            expected = {"stage": "stage_voice", "voice": "voice"}[args.entity_type]
            if ch_type_name and ch_type_name != expected:
                raise _ToolError(
                    f"create_scheduled_event: {args.entity_type} event requires a "
                    f"{expected} channel, got {ch_type_name!r}"
                )
            kwargs["channel"] = ch
            if args.end_time:
                kwargs["end_time"] = _parse_iso_datetime("end_time", args.end_time)
        ev = await guild.create_scheduled_event(**kwargs)
        return {"status": "created", "event_id": str(ev.id), "name": ev.name}

    async def _cancel_scheduled_event(self, args: _CancelScheduledEventArgs) -> dict[str, Any]:
        guild = await self._resolve_guild(args.guild_id)
        ev = None
        if hasattr(guild, "get_scheduled_event"):
            keys: list[Any] = [args.event_id, str(args.event_id)]
            if args.event_id.isdigit():
                keys.insert(0, int(args.event_id))
            seen: set[Any] = set()
            for key in keys:
                if key in seen:
                    continue
                seen.add(key)
                try:
                    ev = guild.get_scheduled_event(key)
                except Exception:
                    ev = None
                if ev is not None:
                    break
        if ev is None and hasattr(guild, "fetch_scheduled_event"):
            try:
                lookup = int(args.event_id) if args.event_id.isdigit() else args.event_id
                ev = await guild.fetch_scheduled_event(lookup)
            except Exception:
                ev = None
        if ev is None:
            raise _ToolError(f"cancel_scheduled_event: event '{args.event_id}' not found")
        await ev.cancel()
        return {"status": "cancelled", "event_id": str(args.event_id)}

    async def _list_scheduled_events(self, args: _ListScheduledEventsArgs) -> list[dict[str, Any]]:
        guild = await self._resolve_guild(args.guild_id)
        if not hasattr(guild, "fetch_scheduled_events"):
            return []
        events = await guild.fetch_scheduled_events()
        out: list[dict[str, Any]] = []
        for ev in events:
            st = getattr(ev, "start_time", None)
            et = getattr(ev, "entity_type", None)
            et_s = str(getattr(et, "name", et) or "")
            out.append(
                {
                    "id": str(getattr(ev, "id", "")),
                    "name": getattr(ev, "name", ""),
                    "status": getattr(ev, "status", ""),
                    "entity_type": et_s,
                    "start_time": st.isoformat() if st is not None else "",
                }
            )
        return out

    async def _create_thread(self, args: _CreateThreadArgs) -> dict[str, Any]:
        """Create a thread in the channel, optionally anchored to a message.

        Note on ``thread_id`` vs ``message_id``: when a thread is anchored
        to a message (``args.message_id`` provided), Discord's API uses the
        parent message's snowflake as the thread's ID. So the returned
        ``thread_id`` will equal the ``message_id`` you passed in. This is
        Discord API design, not a collision — threads and messages share
        an ID space when one is created from the other. Callers that store
        thread IDs should not assume separate-namespace uniqueness vs
        message IDs in the same channel.
        """
        ch = await self._resolve_channel(args.channel_id)
        create = getattr(ch, "create_thread", None)
        if create is None:
            raise _ToolError("create_thread: channel does not support threads")
        msg = None
        if args.message_id:
            try:
                msg = await ch.fetch_message(args.message_id)
            except Exception as exc:
                raise _ToolError(f"create_thread: message not found: {exc}") from exc
            if msg is None:
                raise _ToolError(f"create_thread: message '{args.message_id}' not found")
        th = await create(name=args.name[:100], message=msg)
        return {
            "status": "created",
            "thread_id": str(getattr(th, "id", "")),
            "name": getattr(th, "name", args.name),
        }

    async def _send_typing(self, args: _SendTypingArgs) -> dict[str, Any]:
        ch = await self._resolve_channel(args.channel_id)
        seconds = float(args.duration_seconds)
        typing_factory = getattr(ch, "typing", None)
        if typing_factory is None:
            raise _ToolError("send_typing: channel has no typing()")

        async def _pulse() -> None:
            try:
                async with typing_factory():
                    await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("send_typing: pulse failed", exc_info=True)

        task = asyncio.create_task(_pulse())
        self._typing_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._typing_tasks.discard(task)

        task.add_done_callback(_done)
        return {"status": "typing_started", "duration_seconds": seconds}

    def _transcribe_audio_sync(self, path: Path) -> str:
        """Transcribe an audio file synchronously using ``faster-whisper``.

        Lazy-loads ``WhisperModel`` on the first call and caches it at
        ``self._transcription_model`` for reuse (warm-model pattern). Runs
        in a thread via :meth:`_transcribe_audio` so the event loop stays
        responsive during the 3–5 s inference window.

        Failure modes documented per acceptance criteria:

        - **faster-whisper not installed**: raises ``ImportError("faster-whisper
          not installed")``. The caller in ``_make_on_message_handler`` catches
          this and sets ``transcription_error``.
        - **Poor-quality audio**: best-effort transcription — Whisper returns
          what it can; no ``transcription_error`` raised.
        - **Non-English audio**: Whisper auto-detects the language; accuracy
          degrades for non-English speech but no ``transcription_error`` is
          raised.
        """
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise ImportError("faster-whisper not installed") from exc

        if self._transcription_model is None:
            # Cold-start: model load takes ~0.5–1 s extra on the first call.
            self._transcription_model = WhisperModel(self.whisper_model, device="cpu")

        segments, _ = self._transcription_model.transcribe(str(path))
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def _transcribe_audio(self, path: Path) -> str:
        """Async wrapper: runs :meth:`_transcribe_audio_sync` in the default executor.

        Keeps the event loop responsive while faster-whisper does CPU-bound
        inference (~3–5 s for 60 s of audio on a modern CPU with the base model).
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._transcribe_audio_sync, path)
