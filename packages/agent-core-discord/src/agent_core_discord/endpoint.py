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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
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

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


# Module-level registry. Lets discord.py event handlers look up the live
# endpoint by name. Populated in start(), drained in stop().
_active_endpoints: dict[str, "DiscordEndpoint"] = {}


def _default_attachments_dir(endpoint_name: str) -> Path:
    """Predictable default attachments root, no target-name parsing."""
    return (Path("~/.agent-core/attachments").expanduser() / endpoint_name).resolve()


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


class DiscordEndpoint:
    """Bus endpoint that bridges one Discord bot to one named agent (1:1)."""

    def __init__(
        self,
        *,
        name: str,
        target: str,
        token_env: str,
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

    # --- Endpoint Protocol ---

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        # Re-create the ready event each start so re-starts after stop() get a
        # fresh signal. asyncio.Event is bound to the running loop.
        self._ready_event = asyncio.Event()
        try:
            # 1. Load env_file (if set).
            if self.env_file is not None and self.env_file.exists():
                from dotenv import load_dotenv

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

            # 5. Wire event handlers (Task 4 fills the on_message body, Task 5 the
            #    on_reaction_add body).
            self._client.event(self._make_on_message_handler())
            self._client.event(self._make_on_reaction_add_handler())

            # An on_ready listener that flips the ready event so start() can
            # return once the gateway connection is live. discord.py keys
            # listeners by function name, so the inner function MUST be
            # named on_ready.
            ready_event = self._ready_event

            async def on_ready() -> None:
                ready_event.set()

            self._client.event(on_ready)

            # 6. Register in the live endpoint map BEFORE kicking off the gateway
            #    loop so racing on_ready callbacks find us.
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
        """Handle ToolInvocation envelopes; warn on others."""
        if self._handle is None:
            raise EndpointUnavailable(f"discord '{self.name}' not started")

        if envelope.kind != "ToolInvocation":
            await self._reply(envelope, f"warning: unsupported envelope kind '{envelope.kind}'")
            await self._handle.ack(envelope.id)
            return

        tool = envelope.payload.tool  # type: ignore[union-attr]
        args = envelope.payload.args  # type: ignore[union-attr]

        try:
            result = await self._dispatch(tool, args)
            await self._reply(envelope, json.dumps(result))
        except _ToolError as exc:
            await self._reply(envelope, f"error: {exc}")
        except Exception as exc:
            log.exception("discord tool '%s' raised", tool)
            await self._reply(envelope, f"error: {exc}")

        await self._handle.ack(envelope.id)

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

    async def _reply(self, incoming: Envelope, note: str) -> None:
        assert self._handle is not None
        ack = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=incoming.correlation_id,
            in_reply_to=incoming.id,
            to=incoming.from_,
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of=incoming.id, note=note),
            created_at=datetime.now(timezone.utc),
        )
        try:
            await self._handle.publish(ack)
        except Exception:
            log.exception("discord reply publish failed for %s", incoming.id)

    async def stop(self) -> None:
        _active_endpoints.pop(self.name, None)
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
                created_at=datetime.now(timezone.utc),
            )
            assert self._handle is not None
            await self._handle.publish(env)

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
                created_at=datetime.now(timezone.utc),
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
        entry = self._pending_acks.pop(message_id, None)
        if entry is None:
            return
        emoji, _channel_id, _ts = entry
        try:
            msg = await channel.fetch_message(message_id)
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

        files = None  # File handling is mechanical; integration test exercises real files.
        if args.files:
            try:
                import discord  # type: ignore

                files = [discord.File(f) for f in args.files]
            except ImportError:
                files = list(args.files)
            except Exception as exc:
                raise _ToolError(f"send: invalid files: {exc}") from exc

        new_msg = await ch.send(args.text, embeds=embeds, reference=reference, files=files)

        # Clear the eyes if this was a reply to a tracked inbound.
        if args.reply_to:
            await self._clear_pending_ack(ch, args.reply_to)

        return {"status": "sent", "message_id": str(new_msg.id)}

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
            try:
                import discord  # type: ignore

                embeds = [discord.Embed.from_dict(e) for e in args.embeds]
            except ImportError:
                embeds = list(args.embeds)
            except Exception as exc:
                raise _ToolError(f"edit: invalid embed: {exc}") from exc

        await msg.edit(content=args.text, embeds=embeds)
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
