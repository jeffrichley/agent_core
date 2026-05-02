"""Bus hook: append each published envelope to vault ``daily/raw/*.jsonl``.

Cutover #04: bus traffic (Discord, scheduler, relay, etc.) lands in the same
append-only JSONL stream as ``SessionEndWriter`` (``pepper-requirements.md``
Tool 3 shape: ``ts``, ``dir``, ``src``, ``cid``, ``sender``, ``content``).

Register **only** under ``bus_hooks.pre_publish`` so each logical publish is
logged once (redelivery does not re-run ``pre_publish``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from pathlib import Path
from typing import Any, Literal

from agent_core.bus.envelope import Envelope
from agent_core.hooks.tools.session_end_writer import (
    _append_jsonl_record,
    _daily_log_path,
    _zone,
)

log = logging.getLogger(__name__)

_DEFAULT_SKIP_KINDS = frozenset({"Acknowledgment", "Progress", "Cancellation"})


class DailyRawJsonlHook:
    """``BusHook`` implementation — writes one JSONL object per ``pre_publish``."""

    def __init__(
        self,
        vault_path: str,
        *,
        daily_log_dir: str = "daily/raw",
        timezone: str = "US/Eastern",
        default_sender: str = "bus",
        skip_kinds: list[str] | None = None,
        skip_content_substrings: list[str] | None = None,
        pre_publish_only: bool = True,
    ) -> None:
        self._vault = Path(vault_path).expanduser()
        self._daily_log_dir = daily_log_dir
        self._tz_name = timezone
        self._default_sender = default_sender
        self._skip_kinds = frozenset(skip_kinds) if skip_kinds is not None else _DEFAULT_SKIP_KINDS
        self._skip_substrings = tuple(s.lower() for s in (skip_content_substrings or ()))
        self._pre_publish_only = pre_publish_only

    async def execute(
        self,
        stage: Literal["pre_publish", "pre_deliver"],
        envelope: Envelope,
        params: dict,
    ) -> Envelope | None:
        _ = params
        if self._pre_publish_only and stage != "pre_publish":
            return envelope

        if envelope.kind in self._skip_kinds:
            return envelope

        flat = _flatten_text_for_filter(envelope)
        if flat and any(s in flat.lower() for s in self._skip_substrings):
            return envelope

        record = _record_for_envelope(envelope, self._default_sender, self._tz_name)
        path = _daily_log_path(self._vault, self._daily_log_dir, self._tz_name)
        try:
            _append_jsonl_record(path, record)
        except OSError:
            log.exception("daily_raw_jsonl: failed to append to %s", path)
        return envelope


def _ts_iso(envelope: Envelope, tz_name: str) -> str:
    ts = envelope.created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(_zone(tz_name)).isoformat()


def _infer_src(envelope: Envelope) -> str:
    md = envelope.metadata or {}
    if md.get("scheduler_job"):
        return "scheduler"
    if md.get("channel_relay"):
        return "channel-relay"
    return f"bus-{envelope.kind}"


def _serialize_payload(envelope: Envelope) -> str:
    p = envelope.payload
    pk = p.kind
    if pk == "TextMessage":
        return p.text
    if pk == "Event":
        return f"[{p.type}] {json.dumps(p.data, ensure_ascii=False)}"
    if pk == "ToolInvocation":
        return f"{p.tool} {json.dumps(p.args, ensure_ascii=False)}"
    return json.dumps(p.model_dump(), ensure_ascii=False)[:4000]


def _flatten_text_for_filter(envelope: Envelope) -> str:
    p = envelope.payload
    if p.kind == "TextMessage":
        return p.text
    return ""


def _record_for_envelope(envelope: Envelope, default_sender: str, tz_name: str) -> dict[str, Any]:
    md = envelope.metadata or {}
    direction = md.get("daily_dir") or md.get("direction") or "bus"
    src = md.get("daily_src") or md.get("source") or _infer_src(envelope)
    sender = envelope.from_ or default_sender
    cid = envelope.correlation_id or envelope.id
    return {
        "ts": _ts_iso(envelope, tz_name),
        "dir": str(direction),
        "src": str(src),
        "cid": cid,
        "sender": sender,
        "content": _serialize_payload(envelope),
    }
