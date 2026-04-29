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
import logging
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus.protocol import EndpointUnavailable

from agent_core_discord.access import AccessConfig, InboundContext, gate_message, load_access_config

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
        # Tool dispatch lands in Task 6. For now, just guard the not-started case.
        if self._handle is None:
            raise EndpointUnavailable(f"discord '{self.name}' not started")
        # Real dispatch in Task 6.
        await self._handle.ack(envelope.id)

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
        async def on_reaction_add(reaction, user):
            # Body in Task 5.
            return None

        return on_reaction_add
