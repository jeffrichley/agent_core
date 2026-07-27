"""Tests for the Tier 0 motion gate (pure, synthetic frames — no camera)."""

from __future__ import annotations

import numpy as np
from agent_core_presence.motion import MotionGate


def _blank(value: int = 0) -> np.ndarray:
    """A uniform 120x160 grayscale frame."""
    return np.full((120, 160), value, dtype=np.uint8)


def test_first_frame_reports_motion() -> None:
    """The very first frame always reports motion (nothing to compare to)."""
    assert MotionGate().update(_blank()) is True


def test_identical_frames_report_no_motion() -> None:
    """Two identical frames after the first report no motion."""
    gate = MotionGate()
    gate.update(_blank(10))
    assert gate.update(_blank(10)) is False


def test_large_change_reports_motion() -> None:
    """A big bright block appearing trips the gate."""
    gate = MotionGate()
    gate.update(_blank(0))
    frame = _blank(0)
    frame[0:60, 0:80] = 255  # a quarter of the frame flips bright
    assert gate.update(frame) is True


def test_tiny_change_stays_below_threshold() -> None:
    """A handful of changed pixels is below the fraction threshold — no motion."""
    gate = MotionGate()
    gate.update(_blank(0))
    frame = _blank(0)
    frame[0, 0:3] = 255  # 3 pixels of ~19200 — well under 0.2%
    assert gate.update(frame) is False


def test_resolution_change_reports_motion() -> None:
    """A frame-size change reports motion rather than crashing on mismatched shapes."""
    gate = MotionGate()
    gate.update(_blank(10))
    assert gate.update(np.full((240, 320), 10, dtype=np.uint8)) is True
