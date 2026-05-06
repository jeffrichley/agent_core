"""Real-OpenCV CameraBackend.

Stub until Task 12 implements the cv2 adapter. Importing this module
without instantiating ``OpenCVCameraBackend`` is safe — the cv2 import
lives inside the constructor.
"""

from __future__ import annotations

from agent_core_webcam.protocol import CameraBackend, CameraInfo


class OpenCVCameraBackend:
    """Real backend (cv2). Implementation lands in Task 12."""

    def __init__(self, *, timeout_seconds: float = 3.0):
        self._timeout = timeout_seconds
        # Defer cv2 import until first use so tests that inject a fake
        # backend never need cv2 installed at the import phase.

    def list_cameras(self) -> list[CameraInfo]:
        raise NotImplementedError("OpenCVCameraBackend.list_cameras lands in Task 12")

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        raise NotImplementedError("OpenCVCameraBackend.capture lands in Task 12")


# Runtime check: protocol satisfaction (the stub raises on use, but
# the shape is intact so isinstance(...) succeeds).
_: CameraBackend = OpenCVCameraBackend()  # noqa: F841
