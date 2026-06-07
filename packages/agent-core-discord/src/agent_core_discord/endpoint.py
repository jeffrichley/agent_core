"""DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

This module hosts the class and the module-level _active_endpoints registry
that lets discord.py event handlers find the live endpoint instance from
inside the asyncio loop.

Inbound (on_message, on_reaction_add) and outbound (8 tools dispatched via
ToolInvocation envelopes) handlers land in subsequent tasks; this scaffold
just owns lifecycle and dispatch entry points.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus.protocol import EndpointUnavailable
from agent_core_discord.access import AccessConfig, InboundContext, gate_message, load_access_config
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
from agent_core_discord.briefing import build_briefing_embeds
from agent_core_discord.chunking import smart_chunk_discord
from agent_core_discord.send_retry import channel_send_with_retries
from agent_core_discord.shape_validator import (
    Recognized,
    Unrecognized,
)
from agent_core_discord.shape_validator import (
    validate as validate_shape,
)
from agent_core_discord.sigil import parse_sigil

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

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


# Module-level registry. Lets discord.py event handlers look up the live
# endpoint by name. Populated in start(), drained in stop().
_active_endpoints: dict[str, DiscordEndpoint] = {}


def _default_attachments_dir(endpoint_name: str) -> Path:
    """Predictable default attachments root, no target-name parsing."""
    return (Path("~/.agent-core/attachments").expanduser() / endpoint_name).resolve()


_FILENAME_ALLOWED = re.compile(r"[^A-Za-z0-9._-]")

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


def _redact_url_qs(text: str) -> str:
    """Strip query strings from any URL in a message so signed Discord CDN
    tokens (?ex=&is=&hm=) never reach logs or persisted envelope metadata.
    """
    return re.sub(r"(https?://[^\s?]+)\?\S*", r"\1?<redacted>", text)


class DiscordEndpoint:
    """Bus endpoint that bridges one Discord bot to one named agent (1:1)."""

    # Per-task lazy TTL for `_awaiting_reply_ids`. Evicted inside
    # `_typing_while_pending` to prevent stale typing indicators when
    # explicit cleanup doesn't fire (cache miss, no in_reply_to,
    # dismissed-without-reply, etc.). 90s is the upper bound on observed
    # realistic compose windows with ~25% headroom. Class-attribute placement
    # is intentional: tests can construct an endpoint with a shorter TTL
    # without monkeypatching the module.
    _TYPING_TTL_SECONDS: float = 90.0

    def __init__(
        self,
        *,
        name: str,
        target: str,
        token_env: str,
        outbound_channel_id: str | None = None,
        env_file: str | Path | None = None,
        access_config_path: str | Path | None = None,
        attachments_dir: str | Path | None = None,
        pending_acks_max: int = 5000,
        pending_acks_ttl_seconds: float = 3600.0,
        pending_acks_sweep_seconds: float = 60.0,
        attachment_retention_days: int = 30,
        attachment_max_total_bytes: int = 1_073_741_824,
        attachment_sweep_seconds: float = 3600.0,
        recent_inbounds_max: int = 5000,
        recent_inbounds_ttl_seconds: float = 3600.0,
        _client_factory: Callable[..., Any] | None = None,
    ):
        self.name = name
        self.target = target
        self.token_env = token_env
        self.outbound_channel_id = outbound_channel_id
        self.env_file: Path | None = Path(env_file).expanduser() if env_file else None
        self.access_config_path: Path | None = (
            Path(access_config_path).expanduser() if access_config_path else None
        )
        self.attachments_dir: Path = (
            Path(attachments_dir).expanduser().resolve()
            if attachments_dir
            else _default_attachments_dir(name)
        )
        self.pending_acks_max = pending_acks_max
        self.pending_acks_ttl_seconds = pending_acks_ttl_seconds
        self.pending_acks_sweep_seconds = pending_acks_sweep_seconds
        self.attachment_retention_days = attachment_retention_days
        self.attachment_max_total_bytes = attachment_max_total_bytes
        self.attachment_sweep_seconds = attachment_sweep_seconds
        self._client_factory = _client_factory  # test seam
        self._handle: BusHandle | None = None
        self._client: Any = None
        # Sticky cache of ``user_id → display_name`` so raw events that
        # only carry IDs (poll votes, future engagement events) don't
        # have to re-do the get_user / fetch_user dance on every fire.
        # First miss → HTTP fetch; every subsequent vote from the same
        # user → cache hit. Failures are deliberately NOT cached so a
        # transient HTTP error doesn't lock the user at empty forever.
        self._user_display_name_cache: dict[str, str] = {}
        self._client_task: asyncio.Task | None = None
        self._sweep_task: asyncio.Task | None = None
        self._attachment_sweep_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._access: AccessConfig = AccessConfig()
        # message_id → (ack_emoji, channel_id, monotonic_inserted_at).
        # OrderedDict so the head is the oldest entry — used for both LRU
        # eviction at the cap and TTL eviction in the sweep loop.
        self._pending_acks: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
        # Inbound bus envelope id → (Discord message id, channel id) for
        # outbound TextMessage replies that set in_reply_to but omit metadata.
        self._inbound_envelope_discord: OrderedDict[str, tuple[str, str]] = OrderedDict()
        # Cache of recently-published inbounds keyed by envelope_id, for
        # _resolve_channel_id auto-echo (#83). Mirrors claude_code_mcp.py's
        # _recent_inbounds pattern at N=2 of this shape; extract to shared
        # utility when a third endpoint needs it (rule-of-three).
        self._recent_inbounds: OrderedDict[str, Envelope] = OrderedDict()
        self._recent_inbounds_max = recent_inbounds_max
        self._recent_inbounds_ttl_seconds = recent_inbounds_ttl_seconds
        self._recent_inbounds_timestamps: dict[str, float] = {}
        # Discord message ids we published to the bus and have not "finished"
        # yet (cleared when ack reaction is removed or TTL/LRU evicts).
        self._awaiting_reply_ids: set[str] = set()
        # Sibling timestamps map — same insertion/deletion pairs as
        # `_awaiting_reply_ids`. Per-task lazy TTL safety net inside
        # `_typing_while_pending` evicts orphan entries after
        # `_TYPING_TTL_SECONDS`. See spec doc for the pair-management
        # discipline (#84).
        self._awaiting_reply_ids_timestamps: dict[str, float] = {}
        self._typing_tasks: set[asyncio.Task] = set()

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

    def _sweep_recent_inbounds_once(self) -> int:
        """Evict entries older than TTL; return count evicted.

        Walks oldest-first by insertion order; breaks at first non-stale
        entry (same shape as _sweep_pending_acks_once).
        """
        now = time.monotonic()
        ttl = self._recent_inbounds_ttl_seconds
        evicted = 0
        while self._recent_inbounds:
            oldest_id = next(iter(self._recent_inbounds))
            if now - self._recent_inbounds_timestamps.get(oldest_id, now) <= ttl:
                break
            self._recent_inbounds.popitem(last=False)
            self._recent_inbounds_timestamps.pop(oldest_id, None)
            evicted += 1
        return evicted

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
                    # after _TYPING_TTL_SECONDS. Missing-timestamp self-heals
                    # via `get(mid, 0)` → huge delta → immediate eviction.
                    ts = self._awaiting_reply_ids_timestamps.get(message_id, 0)
                    if time.monotonic() - ts > self._TYPING_TTL_SECONDS:
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

    # --- Endpoint Protocol ---

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        # Re-create the ready event each start so re-starts after stop() get a
        # fresh signal. asyncio.Event is bound to the running loop.
        self._ready_event = asyncio.Event()
        try:
            # 1. Load env_file (if set). dotenv is convenience, not load-bearing —
            #    if it's missing from the install, log and skip rather than
            #    failing the whole bus boot.
            if self.env_file is not None and self.env_file.exists():
                try:
                    from dotenv import load_dotenv
                except ImportError:
                    log.warning(
                        "dotenv not installed; skipping env_file %s",
                        self.env_file,
                    )
                else:
                    load_dotenv(self.env_file, override=False)
                    log.info("loaded env file: %s", self.env_file)

            # 2. Read the bot token. Fail fast if missing.
            token = os.environ.get(self.token_env)
            if not token:
                raise RuntimeError(
                    f"discord endpoint '{self.name}': env var "
                    f"'{self.token_env}' is not set (env_file={self.env_file})"
                )

            # 3. Load access policy (or use permissive defaults).
            self._access = load_access_config(self.access_config_path)

            # 4. Create the Discord client. The factory seam lets tests inject a
            #    fake client without touching discord.py.
            if self._client_factory is None:
                import discord

                intents = discord.Intents.default()
                intents.message_content = True
                intents.reactions = True
                self._client = discord.Client(intents=intents)
            else:
                self._client = self._client_factory(intents=None)

            # 5. Wire event handlers. Use add_listener with explicit name=
            #    rather than @client.event so a future rename of the inner
            #    function can't silently mis-route the event.
            self._add_listener(self._make_on_message_handler(), "on_message")
            self._add_listener(self._make_on_reaction_add_handler(), "on_reaction_add")
            # Engagement events — wire the *raw* dispatch points so we
            # always fire, even when the underlying message has been
            # evicted from the client's message cache (the common case
            # for long-running agents). Caught on testbot 2026-05-05
            # Phase 6 verification: a vote on a bot-posted poll never
            # reached the agent because no listener was wired here.
            self._add_listener(
                self._make_on_raw_poll_vote_handler("discord.poll_vote_add"),
                "on_raw_poll_vote_add",
            )
            self._add_listener(
                self._make_on_raw_poll_vote_handler("discord.poll_vote_remove"),
                "on_raw_poll_vote_remove",
            )
            self._add_listener(
                self._make_on_raw_message_lifecycle_handler("discord.message_edit"),
                "on_raw_message_edit",
            )
            self._add_listener(
                self._make_on_raw_message_lifecycle_handler("discord.message_delete"),
                "on_raw_message_delete",
            )

            # An on_ready listener that flips the ready event so start() can
            # return once the gateway connection is live.
            ready_event = self._ready_event

            async def _ready_listener() -> None:
                ready_event.set()

            self._add_listener(_ready_listener, "on_ready")

            # 6. Register in the live endpoint map BEFORE kicking off the gateway
            #    loop so racing on_ready callbacks find us. Defense-in-depth
            #    name-collision guard — Bus.register also checks, but a stray
            #    second instance constructed in-process would otherwise silently
            #    shadow ours.
            existing = _active_endpoints.get(self.name)
            if existing is not None and existing is not self:
                raise RuntimeError(
                    f"discord endpoint '{self.name}': another live instance is "
                    f"already registered ({existing!r})"
                )
            _active_endpoints[self.name] = self

            # 7. Two-phase connect: login() returns once authenticated, then
            #    connect() runs the gateway loop until close. We park connect()
            #    in a background task so start() returns once on_ready fires.
            #    discord.Client.start(token) is the convenience equivalent of
            #    login() + connect() — and it never returns under normal
            #    operation, which would deadlock the bus boot loop.
            await self._client.login(token)
            self._client_task = asyncio.create_task(
                self._client.connect(),
                name=f"discord-endpoint-{self.name}-gateway",
            )

            # Race the ready event against the gateway task. Whichever
            # completes first wins. This avoids a 30s hang when connect()
            # raises immediately (bad token, network blip, gateway 401) —
            # the task completes with the exception and we surface the
            # real cause instead of a generic timeout.
            ready_wait = asyncio.create_task(self._ready_event.wait(), name="discord-ready-wait")
            done, _pending = await asyncio.wait(
                {ready_wait, self._client_task},
                timeout=30.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                # Timeout: cancel the ready waiter (the client task is
                # cleaned up by rollback below).
                ready_wait.cancel()
                raise RuntimeError(
                    f"discord endpoint '{self.name}': bot did not become ready within 30s"
                )
            if self._client_task in done:
                # connect() exited before on_ready fired — surface the real cause.
                ready_wait.cancel()
                exc = self._client_task.exception()
                if exc is not None:
                    raise RuntimeError(
                        f"discord endpoint '{self.name}': gateway connect failed before ready"
                    ) from exc
                raise RuntimeError(
                    f"discord endpoint '{self.name}': gateway connect returned before ready"
                )
            # ready_wait completed first — happy path. Kick off the
            # _pending_acks sweeper now that the loop is live.
            self._sweep_task = asyncio.create_task(
                self._pending_acks_sweep_loop(),
                name=f"discord-endpoint-{self.name}-acks-sweep",
            )
            self._attachment_sweep_task = asyncio.create_task(
                self._attachment_sweep_loop(),
                name=f"discord-endpoint-{self.name}-attach-sweep",
            )
        except BaseException:
            # Only pop if WE own this slot — never evict a sibling that may
            # have raced in.
            if _active_endpoints.get(self.name) is self:
                _active_endpoints.pop(self.name, None)
            # Cancel ALL background sweep tasks before awaiting any of them.
            # Awaiting one task gives the event loop a chance to start the
            # others; if asyncio.sleep is monkeypatched to a non-yielding stub,
            # any not-yet-cancelled task that starts running will spin forever.
            # Cancelling both first ensures each one receives CancelledError on
            # its very first step, regardless of scheduling order.
            if self._sweep_task is not None:
                self._sweep_task.cancel()
            if self._attachment_sweep_task is not None:
                self._attachment_sweep_task.cancel()
            if self._sweep_task is not None:
                try:
                    await self._sweep_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': sweep task raised during start rollback",
                        self.name,
                    )
                self._sweep_task = None
            if self._attachment_sweep_task is not None:
                try:
                    await self._attachment_sweep_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': attachment sweep raised during start rollback",
                        self.name,
                    )
                self._attachment_sweep_task = None
            if self._client_task is not None:
                self._client_task.cancel()
                try:
                    await self._client_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': gateway task raised during start rollback",
                        self.name,
                    )
                self._client_task = None
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:
                    log.exception(
                        "discord endpoint '%s': client.close() raised during start rollback",
                        self.name,
                    )
            self._client = None
            self._handle = None
            raise

        log.info(
            "DiscordEndpoint(name=%s) started; target=%s, attachments=%s",
            self.name,
            self.target,
            self.attachments_dir,
        )

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
                await self._reply(envelope, note, urgency="yellow")
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

    async def stop(self) -> None:
        if _active_endpoints.get(self.name) is self:
            _active_endpoints.pop(self.name, None)
        for t in list(self._typing_tasks):
            t.cancel()
        self._typing_tasks.clear()
        # Drop typing / threading state so background typing tasks exit promptly.
        self._awaiting_reply_ids.clear()
        self._awaiting_reply_ids_timestamps.clear()
        self._inbound_envelope_discord.clear()
        # Cancel ALL background sweep tasks before awaiting any of them.
        # Awaiting one task gives the event loop a chance to start the others;
        # if asyncio.sleep is monkeypatched to a non-yielding stub (as in the
        # retry tests), any not-yet-cancelled task that starts running will spin
        # forever.  Cancelling both first ensures each one receives CancelledError
        # on its very first step, regardless of scheduling order.
        if self._sweep_task is not None:
            self._sweep_task.cancel()
        if self._attachment_sweep_task is not None:
            self._attachment_sweep_task.cancel()
        if self._sweep_task is not None:
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': sweep task raised during stop",
                    self.name,
                )
            self._sweep_task = None
        if self._attachment_sweep_task is not None:
            try:
                await self._attachment_sweep_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': attachment sweep raised during stop",
                    self.name,
                )
            self._attachment_sweep_task = None
        if self._client_task is not None:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': gateway task raised during stop",
                    self.name,
                )
            self._client_task = None
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                log.exception("DiscordEndpoint(%s) error during client.close()", self.name)
            finally:
                self._client = None
        self._handle = None
        log.info("DiscordEndpoint(name=%s) stopped", self.name)

    # --- Internal handler factories — bodies land in Tasks 4 and 5. ---

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
                    }
                )

            # 6. Build and publish the envelope.
            #    Sigil-prefix urgency: '!' -> red, '?' -> yellow, plain -> green.
            #    The sigil is stripped from the published payload text. See issue #38.
            urgency, text = parse_sigil(message.content or "")

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
            asyncio.create_task(
                self._typing_while_pending(message.channel, mid),
                name=f"discord-{self.name}-typing-{mid}",
            )

        return on_message

    def _make_on_reaction_add_handler(self):
        async def on_reaction_add(reaction: Any, user: Any) -> None:
            # 1. Drop the bot's own reactions.
            if user == self._client.user or user.bot:
                return

            # 2. Drop the ack emoji (the bot's own 👀, even if user reacts with same).
            ack_emoji = self._access.ack_reaction
            if ack_emoji and str(reaction.emoji) == ack_emoji:
                return

            # 3. Build the Event envelope.
            message = reaction.message
            data: dict[str, Any] = {
                "emoji": str(reaction.emoji),
                "channel_id": str(message.channel.id),
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

    # --- Outbound tool handlers (Task 6: send, edit, react). ---

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

    def _track_pending_ack(self, message_id: str, emoji: str, channel_id: str) -> None:
        """Record a pending ack and evict the oldest entry if we hit the cap.

        When LRU eviction fires, the evicted entry's 👀 is removed from the
        original message in a fire-and-forget task — we don't want bookkeeping
        to slow down on_message dispatch.
        """
        self._pending_acks[message_id] = (emoji, channel_id, time.monotonic())
        while len(self._pending_acks) > self.pending_acks_max:
            old_id, (old_emoji, old_ch, _ts) = self._pending_acks.popitem(last=False)
            self._awaiting_reply_ids.discard(old_id)
            self._awaiting_reply_ids_timestamps.pop(old_id, None)
            asyncio.create_task(
                self._remote_remove_ack(old_id, old_emoji, old_ch),
                name=f"discord-endpoint-{self.name}-evict-ack",
            )

    async def _remote_remove_ack(self, message_id: str, emoji: str, channel_id: str) -> None:
        """Best-effort removal of an ack reaction from a Discord message."""
        if self._client is None:
            return
        try:
            channel = self._client.get_channel(channel_id)
            if channel is None:
                channel = await self._client.fetch_channel(channel_id)
            msg = await channel.fetch_message(message_id)
            if msg is None:
                return
            await msg.remove_reaction(emoji, self._client.user)
        except Exception:
            log.debug(
                "discord(%s): could not remove evicted ack on message %s",
                self.name,
                message_id,
                exc_info=True,
            )

    def _sweep_pending_acks_once(self, *, now: float | None = None) -> int:
        """One pass of TTL eviction. Returns count evicted.

        Walks the OrderedDict from oldest to newest. Since insertion order
        is monotonic, we can break as soon as we find a non-stale entry.
        Eviction fires `_remote_remove_ack` as a fire-and-forget task.
        """
        now = now if now is not None else time.monotonic()
        cutoff = now - self.pending_acks_ttl_seconds
        evicted = 0
        while self._pending_acks:
            head_id = next(iter(self._pending_acks))
            emoji, channel_id, ts = self._pending_acks[head_id]
            if ts >= cutoff:
                break
            self._pending_acks.pop(head_id)
            self._awaiting_reply_ids.discard(head_id)
            self._awaiting_reply_ids_timestamps.pop(head_id, None)
            asyncio.create_task(
                self._remote_remove_ack(head_id, emoji, channel_id),
                name=f"discord-endpoint-{self.name}-ttl-ack",
            )
            evicted += 1
        return evicted

    async def _pending_acks_sweep_loop(self) -> None:
        """Periodic TTL sweep. Runs until cancelled by stop()."""
        try:
            while True:
                await asyncio.sleep(self.pending_acks_sweep_seconds)
                try:
                    self._sweep_pending_acks_once()
                except Exception:
                    log.exception("discord endpoint '%s': sweep iteration failed", self.name)
        except asyncio.CancelledError:
            raise

    def _sweep_attachments_once(self) -> int:
        """One retention pass over <attachments_dir>.

        Age first: delete any <env_id>/ dir whose mtime is older than
        attachment_retention_days. Then size cap: while total bytes exceed
        attachment_max_total_bytes, delete whole dirs oldest-first by mtime.
        Whole-directory deletes only; never partial. A failed delete is
        logged and skipped — the sweep never raises into its loop.
        Returns the number of directories evicted.
        """
        import shutil

        root = self.attachments_dir
        try:
            if not root.exists():
                return 0
            root_resolved = root.resolve()
            entries = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            return 0

        def _safe_rmtree(d: Path) -> bool:
            try:
                if d.resolve().parent != root_resolved:
                    return False  # never walk outside the attachments root
                shutil.rmtree(d)
                return True
            except Exception:
                log.exception(
                    "discord(%s): attachment sweep failed to delete %s",
                    self.name,
                    d,
                )
                return False

        evicted = 0
        cutoff = time.time() - (self.attachment_retention_days * 86400)
        survivors: list[tuple[float, int, Path]] = []
        for d in entries:
            try:
                mtime = d.stat().st_mtime
                size = sum(
                    f.stat().st_size for f in d.rglob("*") if f.is_file()
                )
            except OSError:
                continue
            if mtime < cutoff:
                if _safe_rmtree(d):
                    evicted += 1
            else:
                survivors.append((mtime, size, d))

        total = sum(s for _, s, _ in survivors)
        if total > self.attachment_max_total_bytes:
            survivors.sort(key=lambda t: t[0])  # oldest first
            for _mtime, size, d in survivors:
                if total <= self.attachment_max_total_bytes:
                    break
                if _safe_rmtree(d):
                    total -= size
                    evicted += 1
        return evicted

    async def _attachment_sweep_loop(self) -> None:
        """Periodic attachment retention sweep. Runs until cancelled by stop()."""
        try:
            while True:
                await asyncio.sleep(self.attachment_sweep_seconds)
                try:
                    self._sweep_attachments_once()
                except Exception:
                    log.exception(
                        "discord endpoint '%s': attachment sweep iteration failed",
                        self.name,
                    )
        except asyncio.CancelledError:
            raise

    async def _clear_pending_ack(self, channel, message_id: str) -> None:
        mid = str(message_id)
        self._awaiting_reply_ids.discard(mid)
        self._awaiting_reply_ids_timestamps.pop(mid, None)
        entry = self._pending_acks.pop(mid, None)
        if entry is None:
            return
        emoji, _channel_id, _ts = entry
        try:
            msg = await channel.fetch_message(mid)
        except Exception:
            return
        if msg is None:
            return
        with contextlib.suppress(Exception):
            await msg.remove_reaction(emoji, self._client.user)

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

        if last_error is not None and message_ids:
            # Partial delivery: do not clear inbound ack — the conversation may still
            # be awaiting a complete reply; callers get message_ids for what landed.
            return {
                "status": "partial",
                "message_ids": message_ids,
                "error": str(last_error),
            }
        if last_error is not None:
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

    # _list_channels, _get_channel_info land in Task 8.

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

    async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict:
        if not args.attachment_urls:
            return {"saved": []}
        saved: list[dict] = []
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

    async def _list_channels(self, args: _ListChannelsArgs) -> list[dict]:
        out: list[dict] = []
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

    async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict:
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

    async def _send_briefing(self, args: _SendBriefingArgs) -> dict:
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

    async def _create_poll(self, args: _CreatePollArgs) -> dict:
        try:
            import discord  # type: ignore
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

    async def _create_scheduled_event(self, args: _CreateScheduledEventArgs) -> dict:
        try:
            import discord  # type: ignore
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

    async def _cancel_scheduled_event(self, args: _CancelScheduledEventArgs) -> dict:
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
                    ev = guild.get_scheduled_event(key)  # type: ignore[arg-type]
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

    async def _create_thread(self, args: _CreateThreadArgs) -> dict:
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

    async def _send_typing(self, args: _SendTypingArgs) -> dict:
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

        def _done(t: asyncio.Task) -> None:
            self._typing_tasks.discard(task)

        task.add_done_callback(_done)
        return {"status": "typing_started", "duration_seconds": seconds}


class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""


class _PersistError(Exception):
    """Attachment could not be persisted (unsafe path, etc.). Neutral —
    shared by the MCP tool and the inbound path; the tool layer
    translates it to _ToolError."""
