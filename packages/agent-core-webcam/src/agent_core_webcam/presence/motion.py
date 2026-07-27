"""Tier 0 — the motion gate.

The cheapest question in the pipeline: "did anything change since the last
frame?" Absolute difference of consecutive grayscale frames, thresholded. When
the room is still the gate stays shut and the (far more expensive) detector and
recognizer never run — a still room costs essentially nothing.

Pure NumPy, no camera and no model, so the gate logic is fully unit-testable on
synthetic frames.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

GrayFrame = npt.NDArray[np.uint8]


class MotionGate:
    """Detect motion by comparing each grayscale frame to the previous one.

    Args:
        pixel_threshold: A pixel counts as "changed" when its absolute
            intensity difference from the previous frame exceeds this.
        min_changed_fraction: Motion is reported when the fraction of changed
            pixels reaches this. Small enough to catch a person shifting in a
            chair; large enough to ignore sensor noise.
    """

    def __init__(self, *, pixel_threshold: int = 25, min_changed_fraction: float = 0.002) -> None:
        self._prev: GrayFrame | None = None
        self._pixel_threshold = pixel_threshold
        self._min_changed_fraction = min_changed_fraction

    def update(self, gray: GrayFrame) -> bool:
        """Feed the next grayscale frame; return whether motion was detected.

        The first frame (and any resolution change) reports motion so the
        detector runs at least once before the gate can settle.
        """
        prev = self._prev
        self._prev = gray
        if prev is None or prev.shape != gray.shape:
            return True
        diff = np.abs(gray.astype(np.int16) - prev.astype(np.int16))
        changed = int(np.count_nonzero(diff > self._pixel_threshold))
        return changed / gray.size >= self._min_changed_fraction
