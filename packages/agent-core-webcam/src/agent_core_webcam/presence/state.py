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


class WatcherHeartbeat(BaseModel):
    """Proof the watch LOOP is turning, written separately from any reading.

    Deliberately carries no presence data. It answers one question the state
    file cannot: *is the watcher alive?* Without it, "the reading is old"
    and "the process is gone" are the same observation — which is exactly how
    a dead sensor went unnoticed for 56 hours on 2026-08-14.

    Attributes:
        beat_at: Epoch seconds, written EVERY cycle whether or not the frame
            read succeeded. A fresh beat beside a stale reading means the
            watcher is alive and the camera is failing — a different fault,
            and a different message, from the watcher being gone.
        last_frame_at: Epoch seconds of the last SUCCESSFUL frame, or ``None``
            if no frame has ever succeeded this run. Never advanced on failure.
        consecutive_failures: Cycles since the last success. Lets a reader see
            a camera degrading before the reading ages out.
        pid: Owning process id, so a reader can distinguish "stale heartbeat"
            from "a heartbeat some other process is still writing".
    """

    beat_at: float
    last_frame_at: float | None = None
    consecutive_failures: int = 0
    pid: int = 0


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


def heartbeat_path_for(state_path: Path) -> Path:
    """Return the heartbeat path that pairs with ``state_path``.

    Single-sourced so the writer and the reader can never disagree about where
    it lives — the failure this whole change exists to prevent is two halves of
    a contract drifting apart silently.
    """
    return state_path.with_name("watcher-heartbeat.json")


def write_heartbeat(beat: WatcherHeartbeat, path: Path) -> None:
    """Atomically write ``beat`` to ``path`` (temp sibling + :func:`os.replace`).

    Uses a distinct temp suffix from :func:`write_state` so a heartbeat write
    and a state write can never collide on the same temp name mid-cycle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".hb.tmp")
    tmp.write_text(beat.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)


def read_heartbeat(path: Path) -> WatcherHeartbeat | None:
    """Best-effort read of the watcher heartbeat; ``None`` if absent or invalid.

    ``None`` does NOT mean the watcher is dead — it may predate this feature.
    Callers must treat absent-heartbeat as *unknown liveness*, never as death,
    or the fix reintroduces the very confusion it removes.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return WatcherHeartbeat.model_validate_json(raw)
    except ValidationError:
        return None
