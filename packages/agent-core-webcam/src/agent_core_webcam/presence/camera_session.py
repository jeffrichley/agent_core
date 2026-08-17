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
        # Remembered so a degraded session has a way home. Without this, one
        # memory-pressured afternoon would pin the camera to low resolution for
        # the life of the process and nothing would ever say why.
        self._full_resolution = resolution
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

    #: The degraded resolution used when the box cannot afford a full frame.
    #: One step only. The 2026-08-14 failure was a HOST-RAM allocation failure
    #: (OpenCV's ``core/src/alloc.cpp`` — nothing GPU-side): a 1280x720x3 frame
    #: is 2 764 800 bytes and could not be allocated while ~74% of 64 GB was
    #: resident elsewhere. 640x360x3 is 691 200 — a quarter. If THAT also fails,
    #: shrinking further does not save you, and a frame too small for reliable
    #: face detection produces confident-looking garbage, which is worse than an
    #: honest failure. So: one step down, then hold and report.
    DEGRADED_RESOLUTION = (640, 360)

    def reopen(self, *, degrade: bool = True) -> None:
        """Release the capture and open a fresh one, optionally degraded.

        A capture handle can enter a state no amount of re-``read()`` recovers
        from, and the pre-2026-08-16 watcher would retry such a handle forever
        while looking healthy from outside. This gives the loop a way to
        actually recover rather than merely survive.

        ``degrade`` drops to :attr:`DEGRADED_RESOLUTION`, which is a genuine
        mitigation for this failure rather than a token one: the question the
        watcher answers ("is anyone else in the room") is coarse and does not
        need 720p. **A CLEAR reading at 640x360 beats a STALE one at 1280x720.**

        Pass ``degrade=False`` to restore full resolution once the box recovers
        — without that the session would stay degraded forever after a single
        bad afternoon, silently trading accuracy it no longer needs to trade.
        """
        import cv2

        if self._cap is not None:
            try:
                self._cap.release()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - a failed release must not block the reopen
                pass
            self._cap = None
        self._resolution = self.DEGRADED_RESOLUTION if degrade else self._full_resolution
        cap = cv2.VideoCapture(self._index)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise CameraNotFoundError(f"camera {self._index} could not be reopened")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._resolution[1])
        self._cap = cap

    @property
    def degraded(self) -> bool:
        """Whether the session is currently running below its configured resolution."""
        return self._resolution != self._full_resolution

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._cap is not None:
            self._cap.release()  # type: ignore[attr-defined]
            self._cap = None
