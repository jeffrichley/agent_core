"""Real-OpenCV CameraBackend.

Imports cv2 lazily inside method bodies so test environments that inject
a fake backend never need cv2 at module-import time. cv2's failure modes
are mapped onto our protocol exception taxonomy.
"""

from __future__ import annotations

import logging

from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
)

log = logging.getLogger(__name__)

# Probe up to this many camera indices when listing. Most consumer hosts
# have 0–2 cameras; 8 is a generous ceiling without making list_cameras
# pay 100ms-each-failed-open across hundreds of indices.
_MAX_PROBE_INDEX = 8


class OpenCVCameraBackend:
    """cv2-backed CameraBackend.

    Open + release per call. No long-lived state — the OS hardware LED
    reflects "agent is looking right now" honestly.
    """

    def __init__(self, *, timeout_seconds: float = 3.0):
        self._timeout = timeout_seconds

    def list_cameras(self) -> list[CameraInfo]:
        import cv2  # lazy import so cv2 isn't required by importing this module

        out: list[CameraInfo] = []
        for idx in range(_MAX_PROBE_INDEX):
            cap = cv2.VideoCapture(idx)
            if cap is None:
                continue
            opened = cap.isOpened()
            if not opened:
                cap.release()
                # No camera at this index — stop probing further indices.
                # On Windows DirectShow, indices are dense from 0 upward.
                if idx == 0:
                    return []
                break
            name = self._best_effort_name(cap, idx)
            cap.release()
            out.append(CameraInfo(index=idx, name=name, available=True))
        return out

    @staticmethod
    def _best_effort_name(cap, idx: int) -> str:
        # cv2 doesn't expose device names cross-platform. On Windows
        # CAP_DSHOW we could probe via WMI but that's a rabbit hole.
        # Fall back to a generic label.
        return f"Camera {idx}"

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        import cv2

        cap = cv2.VideoCapture(index)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise CameraNotFoundError(f"camera {index} could not be opened")
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
            # Honor the timeout via cv2's own read; cv2 doesn't expose a
            # blocking timeout per se, so we rely on cv2 returning False
            # when no frame is available. A truly hung driver would
            # require process-level handling — out of scope for v1.
            ok, frame = cap.read()
            if not ok or frame is None:
                # On Windows, "device opened but read failed" most
                # commonly means another process holds the video pin
                # exclusively (Zoom, browser camera, etc.).
                raise CameraBusyError(f"camera {index} opened but read failed")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ok2, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            if not ok2:
                raise ReadTimeoutError(f"camera {index} returned a frame but PNG encode failed")
            return bytes(buf.tobytes())
        finally:
            cap.release()


# Static protocol satisfaction check (running the constructor is fine —
# cv2 is lazy-loaded inside method bodies).
_: CameraBackend = OpenCVCameraBackend()  # noqa: F841


__all__ = ["OpenCVCameraBackend"]
