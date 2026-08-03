"""The presence-state file contract, shared by the CV watcher and the hook.

The watcher (Phase 2) is the sole writer; the hook (Phase 1) is the sole
reader. Both go through this module so the on-disk shape is single-sourced.

Privacy invariant baked into the shape: recognized people are named only from
the consent-enrolled set (``known``); everyone else is an opaque *count*
(``unknown_count``) — never an identity, never a descriptor. The state carries
no imagery and no per-unknown data.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class PresenceState(BaseModel):
    """A single presence reading produced by the CV watcher.

    Attributes:
        updated_at: Epoch seconds when the watcher produced this reading. The
            hook uses it as a staleness guard — an old reading degrades to
            "unknown" rather than asserting a stale identity.
        at_desk: Whether the primary user is present at the desk camera.
        known: Names of consent-enrolled people recognized in frame. Only
            enrolled identities ever appear here.
        unknown_count: Number of unrecognized people in frame. Presence only —
            unknowns are never identified, templated, or profiled.
        source: The sensor that produced the reading (e.g. ``"desk-cam"``).
    """

    updated_at: float
    at_desk: bool = False
    known: list[str] = Field(default_factory=list)
    unknown_count: int = 0
    source: str = "desk-cam"


def write_state(state: PresenceState, path: Path) -> None:
    """Atomically write ``state`` to ``path`` (temp sibling + :func:`os.replace`).

    The parent directory is created if missing. The temp-then-replace dance
    means a reader never observes a half-written file — it sees either the old
    reading or the new one, never a torn one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(state.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)


def read_state(path: Path) -> PresenceState | None:
    """Best-effort read of the presence state.

    Returns the parsed :class:`PresenceState`, or ``None`` when the file is
    absent, unreadable, or fails validation. A ``None`` return is the caller's
    signal to treat presence as unknown — reads never raise.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return PresenceState.model_validate_json(raw)
    except ValidationError:
        return None
