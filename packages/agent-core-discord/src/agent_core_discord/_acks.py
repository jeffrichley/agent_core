"""Ack-tracking mixin for DiscordEndpoint.

Move-only extraction from endpoint.py (issue #440, Step 2 of F-B6).
Imports nothing from endpoint.py to avoid circular imports.
"""

from __future__ import annotations

import contextlib
import logging
import time

log = logging.getLogger(__name__)


class _AcksMixin:
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
            self._handle.spawn(
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
