"""Inbound-handler mixin for DiscordEndpoint.

Move-only extraction from endpoint.py (issue #441, Step 3 of F-B6).
Imports nothing from endpoint.py to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core_discord._exceptions import _ToolError
from agent_core_discord.access import InboundContext, gate_message
from agent_core_discord.sigil import parse_sigil
from agent_core_discord.text_sanitize import scrub_surrogates

log = logging.getLogger(__name__)


def _redact_url_qs(text: str) -> str:
    """Strip query strings from any URL in a message so signed Discord CDN
    tokens (?ex=&is=&hm=) never reach logs or persisted envelope metadata.
    """
    return re.sub(r"(https?://[^\s?]+)\?\S*", r"\1?<redacted>", text)


class _HandlersMixin:
    def _add_listener(self, handler: Callable[..., Any], event_name: str) -> None:
        """Register an event handler against the discord.py client.

        Real discord.py uses `Client.add_listener(coro, name=)`. Tests use a
        fake that exposes the same surface. Going through this helper lets us
        switch implementations without touching call sites.
        """
        if hasattr(self._client, "add_listener"):
            self._client.add_listener(handler, name=event_name)
        else:
            # Older fakes / minimal stubs: rebind the handler's name and use
            # the @client.event protocol as a fallback.
            handler.__name__ = event_name  # type: ignore[attr-defined]
            self._client.event(handler)

    def _remember_inbound_mapping(
        self, envelope_id: str, discord_message_id: str, channel_id: str
    ) -> None:
        """Map a published inbound envelope id to Discord ids (LRU-capped)."""
        self._inbound_envelope_discord[envelope_id] = (discord_message_id, channel_id)
        while len(self._inbound_envelope_discord) > self.pending_acks_max:
            self._inbound_envelope_discord.popitem(last=False)

    def _record_inbound(self, envelope: Envelope) -> None:
        """Cache an inbound envelope for auto-echo lookups (#83).

        Stores the full envelope so _resolve_channel_id can read
        metadata.discord.channel_id when an outbound's in_reply_to
        matches the cached id.
        """
        self._recent_inbounds[envelope.id] = envelope
        self._recent_inbounds.move_to_end(envelope.id)
        self._recent_inbounds_timestamps[envelope.id] = time.monotonic()
        while len(self._recent_inbounds) > self._recent_inbounds_max:
            oldest_id, _ = self._recent_inbounds.popitem(last=False)
            self._recent_inbounds_timestamps.pop(oldest_id, None)

    def _resolve_channel_id(self, outbound: Envelope) -> str:
        """Resolve channel_id with precedence:
        1. Explicit metadata.discord.channel_id (preserves current behavior).
        2. Fallback: in_reply_to -> _recent_inbounds lookup (auto-echo).
        3. Hard error -- refuse to guess.

        Sub-causes for the failure path are logged at WARNING; the
        agent-facing _ToolError message is unified.
        """
        # 1. Explicit always wins.
        discord_meta = (outbound.metadata or {}).get("discord") or {}
        if explicit := discord_meta.get("channel_id"):
            return explicit

        # 2. Auto-echo via in_reply_to cache lookup.
        if outbound.in_reply_to:
            inbound = self._recent_inbounds.get(outbound.in_reply_to)
            if inbound:
                inbound_discord = (inbound.metadata or {}).get("discord") or {}
                if cid := inbound_discord.get("channel_id"):
                    return cid
                log.warning(
                    "channel_id resolution failed: cached_inbound_missing_channel_id, "
                    "in_reply_to=%s", outbound.in_reply_to,
                )
            else:
                log.warning(
                    "channel_id resolution failed: cache_miss, in_reply_to=%s",
                    outbound.in_reply_to,
                )
        else:
            log.warning(
                "channel_id resolution failed: no_explicit_no_in_reply_to, "
                "outbound_id=%s", outbound.id,
            )

        raise _ToolError(
            "cannot determine channel — set metadata.discord.channel_id "
            "explicitly, or set in_reply_to so auto-echo can resolve."
        )

    async def _typing_while_pending(self, channel: Any, message_id: str) -> None:
        """Hold Discord 'typing…' until this message is cleared from the awaiting set."""
        typing_factory = getattr(channel, "typing", None)
        if typing_factory is None:
            return
        try:
            async with typing_factory():
                while message_id in self._awaiting_reply_ids:
                    # TTL safety net (#84): orphan entries (no explicit cleanup
                    # fired, agent dismissed without reply, cache miss) evict
                    # after _TYPING_TTL_SECONDS. A missing timestamp means the
                    # entry is an orphan → evict immediately.
                    #
                    # Detect "missing" with an explicit `is None`, NOT `get(mid, 0)`
                    # + `monotonic() - 0 > TTL`: time.monotonic()'s epoch is
                    # arbitrary (~boot), so on a freshly-booted host monotonic()
                    # can be < TTL and `monotonic() - 0 > TTL` is False — wedging
                    # the orphan in the set forever (a host-uptime-dependent hang,
                    # surfaced on fresh CI runners).
                    ts = self._awaiting_reply_ids_timestamps.get(message_id)
                    if ts is None or time.monotonic() - ts > self._TYPING_TTL_SECONDS:
                        self._awaiting_reply_ids.discard(message_id)
                        self._awaiting_reply_ids_timestamps.pop(message_id, None)
                        break
                    # Short poll so ack clear / stop() drops the id promptly; the
                    # typing context manager (discord.py) keeps the indicator fresh.
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug(
                "discord(%s): typing loop for message %s ended",
                self.name,
                message_id,
                exc_info=True,
            )

    def _make_on_message_handler(self):
        async def on_message(message: Any) -> None:
            # 1. Filter our OWN messages only — self-loops would feed the
            # bot its own posts. Other bots are NOT pre-filtered here:
            # the access gate's ``allowed_bot_ids`` field (added in
            # PR #158 for agent_core#143) is the right layer to decide
            # which other-bot authors should pass. Pre-filtering all
            # bots here made ``allowed_bot_ids`` structurally
            # unreachable; surfaced 2026-06-07 during the Pepper-Wren
            # cross-bot rollout smoke test, where Pepper's posts in a
            # shared channel never reached Wren's bus inbox.
            if message.author == self._client.user:
                return

            # 2. Build inbound context for the access gate. ``is_bot`` is
            # piped through from the discord.py Member/User flag so the
            # gate can route through the allowed_bot_ids branch
            # correctly (was hardcoded ``False`` before, which made the
            # ``ctx.is_bot`` check in ``gate_message`` permanently dead).
            is_dm = message.guild is None
            ctx = InboundContext(
                is_dm=is_dm,
                author_id=str(message.author.id),
                channel_id=str(message.channel.id),
                is_bot=bool(getattr(message.author, "bot", False)),
            )

            # 3. Run the access gate.
            if not gate_message(self._access, ctx):
                log.debug(
                    "discord(%s): gate denied message from %s in channel %s",
                    self.name,
                    message.author.id,
                    message.channel.id,
                )
                return

            # 4. Add ack reaction (best-effort) and track for later clearing.
            ack_emoji = self._access.ack_reaction
            if ack_emoji:
                added = False
                try:
                    await message.add_reaction(ack_emoji)
                    added = True
                except Exception:
                    log.warning(
                        "discord(%s): add_reaction failed on message %s — skipping pending ack",
                        self.name,
                        message.id,
                    )
                if added:
                    self._track_pending_ack(str(message.id), ack_emoji, str(message.channel.id))

            # 5. Collect attachment metadata.
            attachments: list[dict[str, Any]] = []
            for att in getattr(message, "attachments", []) or []:
                attachments.append(
                    {
                        "filename": att.filename,
                        "url": att.url,
                        "content_type": getattr(att, "content_type", None) or "unknown",
                        "size_bytes": int(getattr(att, "size", 0)),
                        # duration_secs is set by Discord on voice messages
                        # (audio/ogg); None for all other attachment types.
                        "duration_secs": getattr(att, "duration_secs", None),
                    }
                )

            # 6. Build and publish the envelope.
            #    Sigil-prefix urgency: '!' -> red, '?' -> yellow, plain -> green.
            #    The sigil is stripped from the published payload text. See issue #38.
            urgency, text = parse_sigil(message.content or "")
            text = scrub_surrogates(text)

            # Mint the envelope id up front so attachment files can be grouped
            # under <attachments_dir>/<envelope_id>/ and enrichment happens
            # before Envelope(...) construction (avoids pydantic copy aliasing).
            env_id = uuid.uuid4().hex

            # 5b. Auto-download each attachment (best-effort, per-attachment).
            #     Failure never blocks or loses the text message: the dict
            #     keeps its CDN url and gains a download_error marker.
            for entry in attachments:
                try:
                    local, _nbytes = await self._persist_attachment(
                        url=entry["url"], subdir=env_id
                    )
                    entry["local_path"] = str(local)
                except Exception as exc:  # best-effort by design
                    entry["local_path"] = None
                    safe_reason = _redact_url_qs(f"{type(exc).__name__}: {exc}")
                    entry["download_error"] = safe_reason
                    log.warning(
                        "discord(%s): attachment download failed for %s — %s",
                        self.name,
                        entry.get("filename"),
                        safe_reason,
                    )

            # 5c. Transcription pass (best-effort, per audio attachment).
            #     Runs only when transcribe_voice is True. Mirrors the
            #     download loop discipline: failures add a marker and let
            #     delivery continue — never block or drop the message.
            if self.transcribe_voice:
                for entry in attachments:
                    ct = entry.get("content_type", "") or ""
                    if not ct.startswith("audio/"):
                        continue
                    if entry.get("local_path") is None:
                        continue
                    # Duration gate: skip transcription for very long audio.
                    dur = entry.get("duration_secs")
                    if dur is not None and dur > self.transcribe_max_duration_secs:
                        entry["transcription_error"] = (
                            f"audio too long ({entry['duration_secs']:.0f}s)"
                        )
                        continue
                    try:
                        entry["transcription"] = await self._transcribe_audio(
                            Path(entry["local_path"])
                        )
                    except ImportError:
                        log.warning(
                            "discord(%s): faster-whisper not installed; "
                            "skipping voice transcription for %s",
                            self.name,
                            entry.get("filename"),
                        )
                        entry["transcription_error"] = "faster-whisper not installed"
                    except Exception as exc:
                        safe_msg = _redact_url_qs(f"{type(exc).__name__}: {exc}")
                        entry["transcription_error"] = safe_msg
                        log.warning(
                            "discord(%s): transcription failed for %s — %s",
                            self.name,
                            entry.get("filename"),
                            safe_msg,
                        )

            # Build voice lines from successful transcriptions and append to text.
            voice_lines = [
                entry["transcription"]
                for entry in attachments
                if "transcription" in entry
            ]
            if voice_lines:
                voice_block = "\n".join(f"[voice: {t}]" for t in voice_lines)
                if text:
                    text = f"{text}\n{voice_block}"
                else:
                    text = voice_block

            metadata: dict[str, Any] = {
                "discord": {
                    "channel_id": str(message.channel.id),
                    "message_id": str(message.id),
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "author_id": str(message.author.id),
                    "author_display_name": getattr(message.author, "display_name", "") or "",
                    "is_dm": is_dm,
                    # is_bot piped through so downstream beings can tell "this
                    # is from another agent-core being" vs "this is from Jeff"
                    # without inspecting bot id maps. Pairs with the
                    # allowed_bot_ids gate (agent_core#143).
                    "is_bot": ctx.is_bot,
                },
            }
            if attachments:
                metadata["attachments"] = attachments

            env = Envelope(
                id=env_id,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="TextMessage",
                payload=TextMessagePayload(text=text),
                metadata=metadata,
                urgency=urgency,
                created_at=datetime.now(UTC),
            )
            assert self._handle is not None
            self._remember_inbound_mapping(env.id, str(message.id), str(message.channel.id))
            mid = str(message.id)
            self._awaiting_reply_ids.add(mid)
            self._awaiting_reply_ids_timestamps[mid] = time.monotonic()
            try:
                await self._handle.publish(env)
            except BaseException:
                self._awaiting_reply_ids.discard(mid)
                self._awaiting_reply_ids_timestamps.pop(mid, None)
                self._inbound_envelope_discord.pop(env.id, None)
                raise
            self._record_inbound(env)
            self._handle.spawn(
                self._typing_while_pending(message.channel, mid),
                name=f"discord-{self.name}-typing-{mid}",
            )

        return on_message

    def _channel_allowed(self, channel_id: str) -> bool:
        """Return True if channel_id passes the configured channel allowlist.

        Mirrors the guild-channel branch of ``gate_message()`` in access.py:
          - Empty ``channels`` dict → allow all (unchanged allow-all default).
          - Non-empty → allow only if ``channel_id`` is an explicit key.

        Used by meta-event handlers (reaction, message lifecycle, poll vote)
        that share the same channel gate but do not go through the full
        ``gate_message()`` path (which also evaluates DM policy, bot-block,
        and author identity — not applicable to these events).
        """
        if not self._access.channels:
            return True
        return channel_id in self._access.channels

    def _make_on_reaction_add_handler(self):
        async def on_reaction_add(reaction: Any, user: Any) -> None:
            # 1. Drop the bot's own reactions.
            if user == self._client.user or user.bot:
                return

            # 2. Drop the ack emoji (the bot's own 👀, even if user reacts with same).
            ack_emoji = self._access.ack_reaction
            if ack_emoji and str(reaction.emoji) == ack_emoji:
                return

            # 3. Channel allowlist gate — same rule as on_message.
            message = reaction.message
            channel_id_str = str(message.channel.id)
            if not self._channel_allowed(channel_id_str):
                return

            # 4. Build the Event envelope.
            data: dict[str, Any] = {
                "emoji": str(reaction.emoji),
                "channel_id": channel_id_str,
                "message_id": str(message.id),
                "guild_id": str(message.guild.id) if message.guild else "",
                "user_id": str(user.id),
                "user_display_name": getattr(user, "display_name", "") or "",
            }
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="Event",
                payload=EventPayload(type="discord.reaction_add", data=data),
                created_at=datetime.now(UTC),
            )
            assert self._handle is not None
            await self._handle.publish(env)
            self._record_inbound(env)

        return on_reaction_add

    async def _resolve_user_display_name(self, user_id: int) -> str:
        """Resolve a Discord user's display name with sticky cache.

        Resolution order:
        1. Local cache (``self._user_display_name_cache``) — sticky for
           the lifetime of the endpoint. The whole point: a user fires
           100 votes, we fetch them once.
        2. ``client.get_user(user_id)`` — discord.py's own cache, populated
           opportunistically by message / reaction events whose
           dispatchers hydrate the User. Synchronous, cheap.
        3. ``client.fetch_user(user_id)`` — HTTP round-trip. Reliable but
           adds latency; only paid on first encounter with the user.

        Returns the empty string on any failure (uncached + fetch raises,
        or no client). Failures are deliberately NOT cached so a
        transient HTTP error doesn't lock the user at empty forever.
        """
        user_id_str = str(user_id)
        cached = self._user_display_name_cache.get(user_id_str)
        if cached is not None:
            return cached
        if self._client is None:
            return ""
        user = self._client.get_user(user_id)
        if user is None:
            try:
                user = await self._client.fetch_user(user_id)
            except Exception:
                # discord.NotFound, HTTPException, network error, etc.
                # Don't cache — try again next time.
                return ""
        name = str(getattr(user, "display_name", "") or "")
        if name:
            self._user_display_name_cache[user_id_str] = name
        return name

    def _make_on_raw_poll_vote_handler(self, event_type: str):
        """Build a handler for ``on_raw_poll_vote_add`` / ``on_raw_poll_vote_remove``.

        Both events have identical payload shape (``RawPollVoteActionEvent``
        with message_id / channel_id / user_id / guild_id / answer_id), so a
        single factory parameterised by ``event_type`` covers both. We use
        the *raw* variants rather than the cached ``poll_vote_add`` /
        ``poll_vote_remove`` so the agent gets notified even after the
        underlying message has been evicted from the client's cache.
        """

        async def on_raw_poll_vote(raw: Any) -> None:
            # Drop the bot's own votes — same policy as on_reaction_add.
            self_user = self._client.user if self._client else None
            self_id = getattr(self_user, "id", None) if self_user is not None else None
            if self_id is not None and str(getattr(raw, "user_id", "")) == str(self_id):
                return

            # Channel allowlist gate — same rule as on_message.
            if not self._channel_allowed(str(raw.channel_id)):
                return

            # Resolve the voter's display name with a sticky local cache.
            # Parity goal: ``discord.reaction_add`` Events carry
            # ``user_display_name`` for free (discord.py hydrates the
            # User object before dispatch). Raw poll vote events only
            # carry IDs, so the handler has to resolve. First miss →
            # HTTP fetch_user; subsequent votes from same user → cache
            # hit. Caught on testbot 2026-05-05 round-3 verification.
            user_display_name = await self._resolve_user_display_name(
                int(raw.user_id)
            )

            data: dict[str, Any] = {
                "message_id": str(raw.message_id),
                "channel_id": str(raw.channel_id),
                "user_id": str(raw.user_id),
                "user_display_name": user_display_name,
                "guild_id": str(raw.guild_id) if raw.guild_id else "",
                "answer_id": int(raw.answer_id),
            }
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="Event",
                payload=EventPayload(type=event_type, data=data),
                created_at=datetime.now(UTC),
            )
            assert self._handle is not None
            await self._handle.publish(env)
            self._record_inbound(env)

        return on_raw_poll_vote

    def _make_on_raw_message_lifecycle_handler(self, event_type: str):
        """Build a handler for ``on_raw_message_edit`` / ``on_raw_message_delete``.

        Both raw events expose ``message_id``, ``channel_id``, ``guild_id``
        (Optional). We deliberately don't try to surface message *content*
        in the envelope — for edits, the new content lives in
        ``raw.data`` (a partial gateway dict) and may not include the full
        message; for deletes, there's nothing to surface anyway. Agents
        that need content can refetch via ``fetch_messages``.
        """

        async def on_raw_message_lifecycle(raw: Any) -> None:
            # Channel allowlist gate — same rule as on_message.
            if not self._channel_allowed(str(raw.channel_id)):
                return

            data: dict[str, Any] = {
                "message_id": str(raw.message_id),
                "channel_id": str(raw.channel_id),
                "guild_id": str(raw.guild_id) if raw.guild_id else "",
            }
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="Event",
                payload=EventPayload(type=event_type, data=data),
                created_at=datetime.now(UTC),
            )
            assert self._handle is not None
            await self._handle.publish(env)
            self._record_inbound(env)

        return on_raw_message_lifecycle
