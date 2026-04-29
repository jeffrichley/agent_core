"""DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

This module hosts the class and the module-level _active_endpoints registry
that lets discord.py event handlers find the live endpoint instance from
inside the asyncio loop.

Inbound (on_message, on_reaction_add) and outbound (8 tools dispatched via
ToolInvocation envelopes) handlers land in subsequent tasks; this scaffold
just owns lifecycle and dispatch entry points.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        self._client_factory = _client_factory  # test seam
        self._handle: BusHandle | None = None
        self._client: Any = None
        self._access: AccessConfig = AccessConfig()
        self._pending_acks: dict[str, str] = {}  # message_id → ack emoji (Task 4)

    # --- Endpoint Protocol ---

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
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

            # 6. Register in the live endpoint map BEFORE awaiting client.start, so
            #    racing on_ready callbacks find us.
            _active_endpoints[self.name] = self

            # 7. Connect the bot. discord.Client.start() runs the event loop until
            #    closed; tests' fake client returns immediately after on_ready.
            await self._client.start(token)
        except BaseException:
            _active_endpoints.pop(self.name, None)
            if self._client is not None:
                with contextlib.suppress(Exception):
                    await self._client.close()
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

            # 4. Add ack reaction (best-effort).
            ack_emoji = self._access.ack_reaction
            if ack_emoji:
                with contextlib.suppress(Exception):
                    await message.add_reaction(ack_emoji)
                    self._pending_acks[str(message.id)] = ack_emoji

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

    async def _clear_pending_ack(self, channel, message_id: str) -> None:
        emoji = self._pending_acks.pop(message_id, None)
        if not emoji:
            return
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
        saved: list[dict] = []
        for url in args.attachment_urls:
            filename = url.split("/")[-1].split("?")[0] or "unknown"
            path = target_dir / filename
            try:
                data = await self._download_url(url)
            except Exception as exc:
                raise _ToolError(f"download failed for {url}: {exc}") from exc
            path.write_bytes(data)
            saved.append(
                {
                    "filename": filename,
                    "path": str(path),
                    "content_type": "",
                    "size_bytes": len(data),
                }
            )
        return {"saved": saved}

    async def _list_channels(self, args: _ListChannelsArgs) -> list[dict]:
        raise _ToolError("list_channels: not implemented yet (Task 8)")

    async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict:
        raise _ToolError("get_channel_info: not implemented yet (Task 8)")


class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""
