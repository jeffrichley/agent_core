"""A camera held open across multiple reads.

The webcam backend's ``capture()`` opens and releases the device on every call
(honest LED, one-shot tool use). Multi-shot flows — enrollment, and later the
continuous watcher — would pay a ~1-2s device-open on *every* frame that way.
This session opens the device once and reads many frames, so only the first
read pays warmup. Use as a context manager::

    with CameraSession(index=0) as cam:
        cam.warmup()
        frame = cam.read_bgr()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import numpy as np
import numpy.typing as npt

from agent_core_webcam.protocol import CameraBusyError, CameraNotFoundError

if TYPE_CHECKING:
    from types import TracebackType

BgrFrame = npt.NDArray[np.uint8]


class CameraSession:
    """Hold one camera open for the duration of a ``with`` block."""

    def __init__(self, index: int = 0, resolution: tuple[int, int] = (1280, 720)) -> None:
        self._index = index
        self._resolution = resolution
        self._cap: object | None = None

    def __enter__(self) -> Self:
        import cv2

        cap = cv2.VideoCapture(self._index)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise CameraNotFoundError(f"camera {self._index} could not be opened")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._resolution[1])
        self._cap = cap
        return self

    def read_bgr(self) -> BgrFrame:
        """Read one BGR frame; raise if the session is closed or the read fails."""
        if self._cap is None:
            raise RuntimeError("CameraSession.read_bgr() called outside its context")
        ok, frame = self._cap.read()  # type: ignore[attr-defined]
        if not ok or frame is None:
            raise CameraBusyError(f"camera {self._index} opened but read failed")
        return np.asarray(frame, dtype=np.uint8)

    def warmup(self, n: int = 3) -> None:
        """Read and discard a few frames so the first real shot is exposure-settled."""
        for _ in range(n):
            try:
                self.read_bgr()
            except CameraBusyError:
                return

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._cap is not None:
            self._cap.release()  # type: ignore[attr-defined]
            self._cap = None
