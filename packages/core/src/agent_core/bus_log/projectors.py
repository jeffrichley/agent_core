"""Projector protocol + registry + fallback.

A Projector renders a bus envelope into a Tool 3 summary row, or returns
None to skip the envelope from the projected stream (e.g., heartbeat noise
that should not appear in daily summaries).

Lookup priority:
1. ``payload.type`` for ``Event`` envelopes (e.g., "HandoffReady")
2. ``envelope.kind`` for non-Events (e.g., "TextMessage")
3. fallback_projector — never returns None; renders generic content
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload


def _render_ts(envelope: Envelope, timezone: str) -> str:
    """Render envelope.created_at in the requested IANA timezone."""
    src_dt = envelope.created_at
    if src_dt.tzinfo is None:
        src_dt = src_dt.replace(tzinfo=UTC)
    return src_dt.astimezone(ZoneInfo(timezone)).isoformat()


def _render_dir(envelope: Envelope, perspective: str) -> str:
    is_to = envelope.to == perspective
    is_from = envelope.from_ == perspective
    if is_to and is_from:
        return "self"
    if is_to:
        return "in"
    return "out"


@runtime_checkable
class Projector(Protocol):
    """Render a bus envelope into a Tool 3 row, or skip it."""

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None: ...


_REGISTRY: dict[str, Projector] = {}


def register_projector(key: str, projector: Projector) -> None:
    """Register a projector for an Event payload type or envelope kind.

    Re-registering the same key replaces the prior projector. This is
    intentional: pluggy entry points populate defaults at import time;
    application code may override programmatically (e.g., a test).
    """
    _REGISTRY[key] = projector


def reset_registry() -> None:
    """Clear all registrations. For tests + bootstrap re-init."""
    _REGISTRY.clear()


def get_projector(envelope: Envelope) -> Projector:
    """Resolve the projector for an envelope per the lookup priority.

    Returns the fallback projector if no specific projector is registered;
    never returns None — every envelope has a projector that will render
    *something*, possibly via the generic fallback shape.
    """
    if isinstance(envelope.payload, EventPayload):
        by_type = _REGISTRY.get(envelope.payload.type)
        if by_type is not None:
            return by_type
    by_kind = _REGISTRY.get(envelope.kind)
    if by_kind is not None:
        return by_kind
    return fallback_projector


class TextMessageProjector:
    """Default projector for ``kind="TextMessage"`` envelopes.

    Maps Discord/relay/scheduler text traffic into the Tool 3 row shape.
    Sender display name is taken from envelope metadata when present
    (e.g., ``discord_user_display_name`` set by DiscordEndpoint).
    """

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        if not isinstance(envelope.payload, TextMessagePayload):
            return None
        sender = envelope.metadata.get("discord_user_display_name") or envelope.from_
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": sender,
            "content": envelope.payload.text,
        }


class _FallbackProjector:
    """Last-resort projector — renders any envelope into a generic row.

    Never returns None: keeps unknown envelope kinds visible in summaries
    instead of silently dropping them. Specific projectors should override
    by registering against a specific kind / type id.
    """

    _MAX_DATA_CHARS = 200

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        if isinstance(envelope.payload, EventPayload):
            data_str = json.dumps(envelope.payload.data, sort_keys=True, default=str)
            if len(data_str) > self._MAX_DATA_CHARS:
                data_str = data_str[: self._MAX_DATA_CHARS - 1] + "…"
            content = f"event:{envelope.payload.type} data={data_str}"
        else:
            content = f"{envelope.kind}:{envelope.id}"
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": envelope.from_,
            "content": content,
        }


fallback_projector: Projector = _FallbackProjector()


class HandoffReadyProjector:
    """Projector for ``Event/HandoffReady`` envelopes published by the
    handoff daemon when continuity has been written."""

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        if not isinstance(envelope.payload, EventPayload):
            return None
        if envelope.payload.type != "HandoffReady":
            return None
        path = envelope.payload.data.get("handoff_path", "?")
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": envelope.from_,
            "content": f"continuity ready: handoff.md → {path}",
        }


class HandoffFailedProjector:
    """Projector for ``Event/HandoffFailed`` envelopes."""

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        if not isinstance(envelope.payload, EventPayload):
            return None
        if envelope.payload.type != "HandoffFailed":
            return None
        err = envelope.payload.data.get("error", "unknown")
        return {
            "ts": _render_ts(envelope, timezone),
            "dir": _render_dir(envelope, perspective),
            "src": envelope.from_,
            "cid": envelope.correlation_id,
            "sender": envelope.from_,
            "content": f"continuity failed: {err}",
        }


class AcknowledgmentSkipProjector:
    """Skip Acknowledgment envelopes from projected summaries.

    The hook also filters them at write time by default (see
    ``DailyRawJsonlHook.skip_kinds``), but if an operator opts to log
    acks too, this projector ensures they don't pollute the summary.
    """

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        return None


class SchedulerHeartbeatSkipProjector:
    """Skip scheduler-heartbeat envelopes from projected summaries while
    letting other scheduler-job text traffic through.

    Recognized as: TextMessage with ``metadata.scheduler_job == "heartbeat"``
    (set by ``SchedulerEndpoint._fire``).

    Registered against ``"TextMessage"`` *replacing* the plain
    ``TextMessageProjector`` — internally delegates to it for non-heartbeat
    traffic so we keep the registry a single key per envelope shape.
    """

    _HEARTBEAT_JOB_NAMES = frozenset({"heartbeat"})

    def __init__(self) -> None:
        self._delegate = TextMessageProjector()

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        job = envelope.metadata.get("scheduler_job")
        if job in self._HEARTBEAT_JOB_NAMES:
            return None
        return self._delegate.render(envelope, perspective=perspective, timezone=timezone)
