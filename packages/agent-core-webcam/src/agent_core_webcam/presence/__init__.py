"""Presence framework — the reader half of the camera-derived presence signal.

``state`` is the on-disk contract (written by the future CV watcher, read by
the hook). ``injector`` is the in-session hook that turns a reading into
per-being behavioral guidance. ``motion`` is the Tier-0 motion gate (fuel for
the Phase-2 watcher). ``levels`` is the pure text-selection policy.

Nothing in the hook's import path (``injector`` -> ``state`` -> ``levels``)
imports ``cv2`` or ``numpy`` — the hook loads every turn and must stay instant.
"""

from __future__ import annotations

from agent_core_webcam.presence.injector import PresenceInjector
from agent_core_webcam.presence.state import PresenceState, read_state, write_state

__all__ = ["PresenceInjector", "PresenceState", "read_state", "write_state"]
