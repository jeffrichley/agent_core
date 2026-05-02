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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote, urlparse

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus.protocol import EndpointUnavailable
from agent_core_discord.access import AccessConfig, InboundContext, gate_message, load_access_config
from agent_core_discord.args import (
    _DownloadAttachmentsArgs,
    _EditArgs,
    _FetchArgs,
    _GetChannelInfoArgs,
    _ListChannelsArgs,
    _ReactArgs,
    _SendArgs,
)
from agent_core_discord.chunking import smart_chunk_discord

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


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


class DiscordEndpoint:
    """Bus endpoint that bridges one Discord bot to one named agent (1:1)."""

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
        self._client_factory = _client_factory  # test seam
        self._handle: BusHandle | None = None
        self._client: Any = None
        self._client_task: asyncio.Task | None = None
        self._sweep_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event = asyncio.Event()
        self._access: AccessConfig = AccessConfig()
        # message_id → (ack_emoji, channel_id, monotonic_inserted_at).
        # OrderedDict so the head is the oldest entry — used for both LRU
        # eviction at the cap and TTL eviction in the sweep loop.
        self._pending_acks: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
        # Inbound bus envelope id → (Discord message id, channel id) for
        # outbound TextMessage replies that set in_reply_to but omit metadata.
        self._inbound_envelope_discord: OrderedDict[str, tuple[str, str]] = OrderedDict()
        # Discord message ids we published to the bus and have not "finished"
        # yet (cleared when ack reaction is removed or TTL/LRU evicts).
        self._awaiting_reply_ids: set[str] = set()

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

    async def _typing_while_pending(self, channel: Any, message_id: str) -> None:
        """Hold Discord 'typing…' until this message is cleared from the awaiting set."""
        typing_factory = getattr(channel, "typing", None)
        if typing_factory is None:
            return
        try:
            async with typing_factory():
                while message_id in self._awaiting_reply_ids:
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
        except BaseException:
            # Only pop if WE own this slot — never evict a sibling that may
            # have raced in.
            if _active_endpoints.get(self.name) is self:
                _active_endpoints.pop(self.name, None)
            if self._sweep_task is not None:
                self._sweep_task.cancel()
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
            result = await self._dispatch(tool, args)
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
        """Route a bus TextMessage to Discord send."""
        if not isinstance(envelope.payload, TextMessagePayload):
            raise _ToolError("TextMessage envelope payload is invalid")

        discord_meta = envelope.metadata.get("discord", {})
        channel_id = None
        reply_to: str | None = None
        if isinstance(discord_meta, dict):
            channel_id = discord_meta.get("channel_id")
            # Prefer explicit reply_to; else message_id (legacy); else resolve
            # from bus in_reply_to → inbound envelope id (agent replies often
            # only set in_reply_to).
            reply_to = discord_meta.get("reply_to") or discord_meta.get("message_id")
            if reply_to is not None:
                reply_to = str(reply_to)
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

        args = _SendArgs(
            channel_id=str(channel_id),
            text=envelope.payload.text,
            reply_to=reply_to,
        )
        return await self._send(args)

    async def _dispatch(self, tool: str, args: dict) -> Any:
        if tool == "send":
            return await self._send(_SendArgs(**args))
        if tool == "edit":
            return await self._edit(_EditArgs(**args))
        if tool == "react":
            return await self._react(_ReactArgs(**args))
        if tool == "fetch":
            return await self._fetch(_FetchArgs(**args))
        if tool == "download_attachments":
            return await self._download_attachments(_DownloadAttachmentsArgs(**args))
        if tool == "list_channels":
            return await self._list_channels(_ListChannelsArgs(**args))
        if tool == "get_channel_info":
            return await self._get_channel_info(_GetChannelInfoArgs(**args))
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
        # Drop typing / threading state so background typing tasks exit promptly.
        self._awaiting_reply_ids.clear()
        self._inbound_envelope_discord.clear()
        if self._sweep_task is not None:
            self._sweep_task.cancel()
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
            # 1. Filter our own messages and other bots.
            if message.author == self._client.user or message.author.bot:
                return

            # 2. Build inbound context for the access gate.
            is_dm = message.guild is None
            ctx = InboundContext(
                is_dm=is_dm,
                author_id=str(message.author.id),
                channel_id=str(message.channel.id),
                is_bot=False,
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

            # 5. Collect attachment metadata (no auto-download).
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
            #    Apply the urgency-red regex rule. Empty string disables.
            #    Per-message compile is fine for v1 throughput; a future v2
            #    can pre-compile in start() if profiling shows it matters.
            urgency: Any = "green"
            regex = self._access.urgency_red_regex
            if regex:
                try:
                    if re.search(regex, message.content or ""):
                        urgency = "red"
                except re.error:
                    log.warning(
                        "discord(%s): invalid urgency_red_regex %r — skipping",
                        self.name,
                        regex,
                    )

            metadata: dict[str, Any] = {
                "discord": {
                    "channel_id": str(message.channel.id),
                    "message_id": str(message.id),
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "author_id": str(message.author.id),
                    "author_display_name": getattr(message.author, "display_name", "") or "",
                    "is_dm": is_dm,
                },
            }
            if attachments:
                metadata["attachments"] = attachments

            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="TextMessage",
                payload=TextMessagePayload(text=message.content or ""),
                metadata=metadata,
                urgency=urgency,
                created_at=datetime.now(UTC),
            )
            assert self._handle is not None
            self._remember_inbound_mapping(
                env.id, str(message.id), str(message.channel.id)
            )
            mid = str(message.id)
            self._awaiting_reply_ids.add(mid)
            try:
                await self._handle.publish(env)
            except BaseException:
                self._awaiting_reply_ids.discard(mid)
                self._inbound_envelope_discord.pop(env.id, None)
                raise
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

        return on_reaction_add

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

    async def _clear_pending_ack(self, channel, message_id: str) -> None:
        mid = str(message_id)
        self._awaiting_reply_ids.discard(mid)
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
        if args.text is None and not args.embeds:
            raise _ToolError("send: one of 'text' or 'embeds' is required")
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
            new_msg = await ch.send(args.text, **send_kwargs)
            if args.reply_to:
                await self._clear_pending_ack(ch, args.reply_to)
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
                new_msg = await ch.send(part, **send_part)
            except Exception as exc:
                last_error = exc
                break
            message_ids.append(str(new_msg.id))

        if last_error is not None and message_ids:
            if args.reply_to:
                await self._clear_pending_ack(ch, args.reply_to)
            return {
                "status": "partial",
                "message_ids": message_ids,
                "error": str(last_error),
            }
        if last_error is not None:
            raise _ToolError(f"send failed: {last_error}") from last_error

        if args.reply_to:
            await self._clear_pending_ack(ch, args.reply_to)

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
                }
            )
        return out

    async def _download_url(self, url: str) -> bytes:
        """Fetch a URL's bytes. Override in tests to avoid network."""
        try:
            import httpx
        except ImportError as exc:
            raise _ToolError("download_attachments: httpx not available") from exc
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict:
        if not args.attachment_urls:
            return {"saved": []}
        target_dir = self.attachments_dir / args.message_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_resolved = target_dir.resolve()
        saved: list[dict] = []
        for url in args.attachment_urls:
            filename = _safe_filename(url)
            path = (target_dir / filename).resolve()
            try:
                path.relative_to(target_resolved)
            except ValueError as exc:
                raise _ToolError(f"download_attachments: refused unsafe path for {url!r}") from exc
            # De-dup so two URLs ending in the same name don't silently overwrite.
            if path.exists():
                stem, suffix = path.stem, path.suffix
                path = (target_dir / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}").resolve()
                try:
                    path.relative_to(target_resolved)
                except ValueError as exc:
                    raise _ToolError(
                        f"download_attachments: refused unsafe dedup path for {url!r}"
                    ) from exc
            try:
                data = await self._download_url(url)
            except Exception as exc:
                raise _ToolError(f"download failed for {url}: {exc}") from exc
            path.write_bytes(data)
            saved.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "content_type": "",
                    "size_bytes": len(data),
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
                        "guild_id": str(getattr(ch, "guild_id", g.id)),
                        "topic": getattr(ch, "topic", "") or "",
                    }
                )
        return out

    async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict:
        ch = await self._resolve_channel(args.channel_id)
        return {
            "id": str(ch.id),
            "name": getattr(ch, "name", ""),
            "type": str(getattr(ch, "type", "text")),
            "guild_id": str(getattr(ch, "guild_id", "") or ""),
            "topic": getattr(ch, "topic", "") or "",
            "nsfw": bool(getattr(ch, "nsfw", False)),
        }


class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""
