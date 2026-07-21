"""Shared instance-state base for the five DiscordEndpoint mixins.

``DiscordEndpoint`` composes five mixins (``_AcksMixin``, ``_LifecycleMixin``,
``_HandlersMixin``, ``_OutboundMixin``, ``_ToolsMixin``). Every mixin method
reads ``self._X`` state that is assigned once in ``DiscordEndpoint.__init__``
and calls methods defined on *sibling* mixins. mypy type-checks each mixin
class standalone, so without a common base it cannot resolve either the shared
attributes or the cross-mixin method calls.

``_EndpointState`` is that common base. It carries:

* **class-level attribute annotations** (no assignments) for every shared
  ``self._X`` — the authoritative types, read from ``DiscordEndpoint.__init__``.
* **cross-mixin method declarations** under ``TYPE_CHECKING`` so a method
  defined on one mixin resolves when called from another. These are pure
  type declarations: the block never executes at runtime, so ``_EndpointState``
  is an empty class at runtime and adds no behaviour of its own.

Each mixin inherits ``_EndpointState``; ``DiscordEndpoint`` inherits all five
(a shared diamond that C3 resolves cleanly).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle
    from agent_core_discord.access import AccessConfig
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


class _EndpointState:
    """Type-only carrier for DiscordEndpoint's shared attributes and the
    cross-mixin method surface. Not instantiated directly; each mixin inherits
    it so standalone type-checking resolves shared state and sibling calls.
    """

    # --- Construction args / public config (set in DiscordEndpoint.__init__) ---
    name: str
    target: str
    token_env: str
    outbound_channel_id: str | None
    env_file: Path | None
    access_config_path: Path | None
    attachments_dir: Path
    pending_acks_max: int
    pending_acks_ttl_seconds: float
    pending_acks_sweep_seconds: float
    attachment_retention_days: int
    attachment_max_total_bytes: int
    attachment_sweep_seconds: float
    transcribe_voice: bool
    whisper_model: str
    transcribe_max_duration_secs: float
    access_config_reload_interval: float
    _TYPING_TTL_SECONDS: float

    # --- Internal runtime state ---
    _transcription_model: Any | None
    _client_factory: Callable[..., Any] | None
    _handle: BusHandle | None
    _client: Any
    _user_display_name_cache: dict[str, str]
    _client_task: asyncio.Task[Any] | None
    _sweep_task: asyncio.Task[Any] | None
    _attachment_sweep_task: asyncio.Task[Any] | None
    _ready_event: asyncio.Event
    _access: AccessConfig
    _pending_acks: OrderedDict[str, tuple[str, str, float]]
    _inbound_envelope_discord: OrderedDict[str, tuple[str, str]]
    _recent_inbounds: OrderedDict[str, Envelope]
    _recent_inbounds_max: int
    _recent_inbounds_ttl_seconds: float
    _recent_inbounds_timestamps: dict[str, float]
    _awaiting_reply_ids: set[str]
    _awaiting_reply_ids_timestamps: dict[str, float]
    _typing_tasks: set[asyncio.Task[Any]]
    _access_reload_task: asyncio.Task[Any] | None
    _access_config_mtime: tuple[float, int] | None

    if TYPE_CHECKING:
        # Cross-mixin method surface. Declared here so a method defined on one
        # mixin resolves when called from another; the concrete implementation
        # (and its docstring) lives on the owning mixin. Signatures must stay in
        # lockstep with those implementations.

        # _AcksMixin
        def _track_pending_ack(self, message_id: str, emoji: str, channel_id: str) -> None: ...
        async def _remote_remove_ack(
            self, message_id: str, emoji: str, channel_id: str
        ) -> None: ...
        async def _clear_pending_ack(self, channel: Any, message_id: str) -> None: ...

        # _HandlersMixin
        def _add_listener(self, handler: Callable[..., Any], event_name: str) -> None: ...
        def _resolve_channel_id(self, outbound: Envelope) -> str: ...
        def _make_on_message_handler(
            self,
        ) -> Callable[[Any], Coroutine[Any, Any, None]]: ...
        def _make_on_reaction_add_handler(
            self,
        ) -> Callable[[Any, Any], Coroutine[Any, Any, None]]: ...
        def _make_on_raw_poll_vote_handler(
            self, event_type: str
        ) -> Callable[[Any], Coroutine[Any, Any, None]]: ...
        def _make_on_raw_message_lifecycle_handler(
            self, event_type: str
        ) -> Callable[[Any], Coroutine[Any, Any, None]]: ...

        # _OutboundMixin
        async def _resolve_channel(self, channel_id: str) -> Any: ...
        async def _send(self, args: _SendArgs) -> dict[str, Any]: ...

        # _ToolsMixin
        async def _persist_attachment(self, *, url: str, subdir: str) -> tuple[Path, int]: ...
        async def _transcribe_audio(self, path: Path) -> str: ...
        async def _send_briefing(self, args: _SendBriefingArgs) -> dict[str, Any]: ...
        async def _send_typing(self, args: _SendTypingArgs) -> dict[str, Any]: ...
        async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict[str, Any]: ...
        async def _list_channels(self, args: _ListChannelsArgs) -> list[dict[str, Any]]: ...
        async def _get_channel_info(self, args: _GetChannelInfoArgs) -> dict[str, Any]: ...
        async def _create_poll(self, args: _CreatePollArgs) -> dict[str, Any]: ...
        async def _create_scheduled_event(
            self, args: _CreateScheduledEventArgs
        ) -> dict[str, Any]: ...
        async def _cancel_scheduled_event(
            self, args: _CancelScheduledEventArgs
        ) -> dict[str, Any]: ...
        async def _list_scheduled_events(
            self, args: _ListScheduledEventsArgs
        ) -> list[dict[str, Any]]: ...
        async def _create_thread(self, args: _CreateThreadArgs) -> dict[str, Any]: ...
