"""DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

The bulk of the implementation lives in the five mixin classes imported below.
This module owns only the module-level live-endpoint registry and __init__.
"""

from __future__ import annotations

import asyncio
import json  # noqa: F401 — needed in deliver()'s __globals__ for the rebinding below
import logging
import types as _types
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core.bus.protocol import EndpointUnavailable  # noqa: F401 — deliver() globals
from agent_core_discord._acks import _AcksMixin
from agent_core_discord._handlers import _HandlersMixin
from agent_core_discord._lifecycle import _LifecycleMixin
from agent_core_discord._outbound import _OutboundMixin
from agent_core_discord._tools import _ToolsMixin
from agent_core_discord.access import AccessConfig
from agent_core_discord.send_retry import is_retryable_discord_send_error  # noqa: F401 — deliver()
from agent_core_discord.shape_validator import Recognized, Unrecognized  # noqa: F401 — deliver()
from agent_core_discord.shape_validator import (  # noqa: F401 — deliver() globals + patch target
    validate as validate_shape,
)

log = logging.getLogger(__name__)

# Rebind _OutboundMixin.deliver's __globals__ to this module's namespace so that
# monkeypatching agent_core_discord.endpoint.validate_shape in tests works as
# expected. deliver() was extracted to _outbound.py (issue #442) but an existing
# characterization test patches validate_shape on the endpoint module; the rebinding
# keeps the patch target working without modifying the test.
_deliver_orig = _OutboundMixin.__dict__["deliver"]
# Dynamic method rebind (runtime test seam, see comment above) — mypy can't
# model reassigning a method, and the behaviour is intentional.
_OutboundMixin.deliver = _types.FunctionType(  # type: ignore[method-assign]
    _deliver_orig.__code__,
    globals(),
    _deliver_orig.__name__,
    _deliver_orig.__defaults__,
    _deliver_orig.__closure__,
)
_OutboundMixin.deliver.__qualname__ = _deliver_orig.__qualname__
del _deliver_orig, _types


def _default_attachments_dir(endpoint_name: str) -> Path:
    """Predictable default attachments root, no target-name parsing."""
    return (Path("~/.agent-core/attachments").expanduser() / endpoint_name).resolve()


class DiscordEndpoint(_AcksMixin, _LifecycleMixin, _HandlersMixin, _OutboundMixin, _ToolsMixin):
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
        transcribe_voice: bool = True,
        whisper_model: str = "base",
        transcribe_max_duration_secs: float = 300.0,
        recent_inbounds_max: int = 5000,
        recent_inbounds_ttl_seconds: float = 3600.0,
        access_config_reload_interval: float = 5.0,
        _client_factory: Callable[..., Any] | None = None,
    ):
        self.name = name
        self.target = target
        self.token_env = token_env
        self.outbound_channel_id = outbound_channel_id
        # Attribute types are declared once on the shared _EndpointState base;
        # __init__ only assigns values (no inline annotations) so the mixins and
        # this class agree on a single source of truth for each attribute's type.
        self.env_file = Path(env_file).expanduser() if env_file else None
        self.access_config_path = (
            Path(access_config_path).expanduser() if access_config_path else None
        )
        self.attachments_dir = (
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
        self.transcribe_voice = transcribe_voice
        self.whisper_model = whisper_model
        self.transcribe_max_duration_secs = transcribe_max_duration_secs
        # Lazy-loaded WhisperModel cache. None until the first voice message
        # arrives. Reused for all subsequent transcriptions (warm-model pattern
        # mirrors _user_display_name_cache: first-miss-then-hit, instance-scoped).
        # Not pre-loaded at start() to avoid adding startup latency for endpoints
        # that never see voice messages.
        self._transcription_model = None
        self._client_factory = _client_factory  # test seam
        self._handle = None
        self._client = None
        # Sticky cache of ``user_id → display_name`` so raw events that
        # only carry IDs (poll votes, future engagement events) don't
        # have to re-do the get_user / fetch_user dance on every fire.
        # First miss → HTTP fetch; every subsequent vote from the same
        # user → cache hit. Failures are deliberately NOT cached so a
        # transient HTTP error doesn't lock the user at empty forever.
        self._user_display_name_cache = {}
        self._client_task = None
        self._sweep_task = None
        self._attachment_sweep_task = None
        self._ready_event = asyncio.Event()
        self._access = AccessConfig()
        # message_id → (ack_emoji, channel_id, monotonic_inserted_at).
        # OrderedDict so the head is the oldest entry — used for both LRU
        # eviction at the cap and TTL eviction in the sweep loop.
        self._pending_acks = OrderedDict()
        # Inbound bus envelope id → (Discord message id, channel id) for
        # outbound TextMessage replies that set in_reply_to but omit metadata.
        self._inbound_envelope_discord = OrderedDict()
        # Cache of recently-published inbounds keyed by envelope_id, for
        # _resolve_channel_id auto-echo (#83). Mirrors claude_code_mcp.py's
        # _recent_inbounds pattern at N=2 of this shape; extract to shared
        # utility when a third endpoint needs it (rule-of-three).
        self._recent_inbounds = OrderedDict()
        self._recent_inbounds_max = recent_inbounds_max
        self._recent_inbounds_ttl_seconds = recent_inbounds_ttl_seconds
        self._recent_inbounds_timestamps = {}
        # Discord message ids we published to the bus and have not "finished"
        # yet (cleared when ack reaction is removed or TTL/LRU evicts).
        self._awaiting_reply_ids = set()
        # Sibling timestamps map — same insertion/deletion pairs as
        # `_awaiting_reply_ids`. Per-task lazy TTL safety net inside
        # `_typing_while_pending` evicts orphan entries after
        # `_TYPING_TTL_SECONDS`. See spec doc for the pair-management
        # discipline (#84).
        self._awaiting_reply_ids_timestamps = {}
        self._typing_tasks = set()
        self.access_config_reload_interval = access_config_reload_interval
        self._access_reload_task = None
        # Stored as (st_mtime, st_size) so we detect changes even on
        # filesystems with coarse mtime granularity (e.g. overlay in CI).
        self._access_config_mtime = None


# ---------------------------------------------------------------------------
# Backward-compat re-exports.
# External code and existing tests import these names directly from this
# module.  They now live in the mixin modules from the F-B6 split; the
# imports below keep every `from agent_core_discord.endpoint import X`
# working without requiring any consumer change.  Do NOT remove.
# ---------------------------------------------------------------------------
from agent_core_discord._exceptions import _PersistError, _ToolError  # noqa: F401
from agent_core_discord._handlers import _redact_url_qs  # noqa: F401
from agent_core_discord._lifecycle import _active_endpoints  # noqa: F401
from agent_core_discord._outbound import (  # noqa: F401
    _TOOL_ALIASES,
    _canonical_tool,
    _check_embeds_within_caps,
    _embed_char_count,
    _serialize_poll,
)
from agent_core_discord._tools import _parse_iso_datetime, _safe_filename  # noqa: F401
