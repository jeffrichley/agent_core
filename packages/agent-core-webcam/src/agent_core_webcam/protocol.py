"""Camera backend protocol + exception taxonomy.

The ``CameraBackend`` Protocol is the seam that lets ``WebcamEndpoint``
work against either ``OpenCVCameraBackend`` (real) or ``FakeCameraBackend``
(deterministic for tests). All failure modes the endpoint maps to
agent-readable error messages descend from ``WebcamError`` so the
endpoint's exception handling stays simple.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class WebcamError(Exception):
    """Base for every error a CameraBackend may raise."""


class CameraNotFoundError(WebcamError):
    """The requested camera index does not exist on this host."""


class CameraBusyError(WebcamError):
    """The camera opened but is in use by another process."""


class ReadTimeoutError(WebcamError):
    """The camera opened but did not return a frame within the timeout."""


@dataclass(frozen=True)
class CameraInfo:
    """One enumerated camera on the host."""

    index: int
    name: str
    available: bool


@runtime_checkable
class CameraBackend(Protocol):
    """Hardware-access seam used by WebcamEndpoint."""

    def list_cameras(self) -> list[CameraInfo]:
        """Enumerate cameras the host can see right now."""

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        """Capture one frame from camera ``index`` at ``resolution``.

        Returns PNG-encoded bytes (sRGB, RGB-ordered).

        Raises:
            CameraNotFoundError: index not present on this host.
            CameraBusyError: device opened but is in use elsewhere.
            ReadTimeoutError: device opened but read() returned no frame.
        """


__all__ = [
    "CameraBackend",
    "CameraBusyError",
    "CameraInfo",
    "CameraNotFoundError",
    "ReadTimeoutError",
    "WebcamError",
]
