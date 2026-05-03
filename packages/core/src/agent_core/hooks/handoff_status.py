"""Handoff status sidecar — coordinates SessionEnd / HandoffWriter with SessionStart injection.

``HandoffInjector`` reads ``handoff-status.json`` next to ``handoff.md`` so startup
context does not treat a stale on-disk handoff as authoritative while a background
write is still in progress.

Schema (JSON object, UTF-8):

- ``schema_version`` (int): currently ``1``.
- ``state`` (str): ``pending`` | ``ready`` | ``failed``.
- ``session_id`` (str): Claude session that last touched handoff lifecycle.
- ``correlation_id`` (str): idempotency / tracing (UUID).
- ``updated_at`` (str): ISO-8601 UTC timestamp.
- ``error`` (str | null): human-readable failure when ``state`` is ``failed``.
- ``content_sha256`` (str | null): hex SHA-256 of committed ``handoff.md`` when ``ready``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def path_for_handoff(handoff_path: Path) -> Path:
    """Default sidecar path: ``handoff-status.json`` beside ``handoff.md``."""
    return handoff_path.parent / "handoff-status.json"


def read_status(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def write_status(path: Path, payload: dict[str, Any]) -> None:
    """Write status atomically (write temp + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {**payload, "schema_version": SCHEMA_VERSION}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_pending(path: Path, session_id: str, correlation_id: str | None = None) -> None:
    cid = correlation_id or str(uuid.uuid4())
    write_status(
        path,
        {
            "state": "pending",
            "session_id": session_id,
            "correlation_id": cid,
            "updated_at": datetime.now(UTC).isoformat(),
            "error": None,
            "content_sha256": None,
        },
    )


def write_ready(path: Path, session_id: str, handoff_text: str, preserve_correlation: bool = True) -> None:
    prev = read_status(path) if preserve_correlation else None
    cid = (prev or {}).get("correlation_id") or str(uuid.uuid4())
    write_status(
        path,
        {
            "state": "ready",
            "session_id": session_id,
            "correlation_id": cid,
            "updated_at": datetime.now(UTC).isoformat(),
            "error": None,
            "content_sha256": sha256_text(handoff_text),
        },
    )


def write_failed(path: Path, session_id: str, error: str) -> None:
    prev = read_status(path) or {}
    cid = prev.get("correlation_id") or str(uuid.uuid4())
    write_status(
        path,
        {
            "state": "failed",
            "session_id": session_id,
            "correlation_id": cid,
            "updated_at": datetime.now(UTC).isoformat(),
            "error": error,
            "content_sha256": None,
        },
    )
