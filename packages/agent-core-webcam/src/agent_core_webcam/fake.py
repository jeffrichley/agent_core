"""Deterministic camera backend for tests.

Mirrors ``OpenCVCameraBackend``'s behavior strictly — including refusing
argument shapes the real backend refuses (unknown camera index →
``CameraNotFoundError``). Produces minimal valid PNGs without any
imaging work, so tests stay fast.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

from agent_core_webcam.protocol import (
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
)


class _FluentMode:
    """Descriptor that makes a mode-setter work both as a classmethod
    (``FakeCameraBackend.with_busy(0)`` creates a fresh instance) and as
    an instance method (``fake.with_busy(0)`` mutates + returns the
    existing instance), so call-chains like
    ``FakeCameraBackend.with_cameras([0,1]).with_busy(0)`` compose
    correctly.
    """

    def __init__(self, attr: str) -> None:
        self._attr = attr

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        attr = self._attr

        if obj is None:
            # Called on the class: FakeCameraBackend.with_busy(0)
            def class_call(index: int) -> "FakeCameraBackend":
                inst = FakeCameraBackend()
                getattr(inst, attr).add(index)
                return inst

            return class_call
        else:
            # Called on an instance: fake.with_busy(0)
            def instance_call(index: int) -> "FakeCameraBackend":
                getattr(obj, attr).add(index)
                return obj

            return instance_call


@dataclass
class FakeCameraBackend:
    """In-memory camera backend with composable failure modes.

    Use the classmethods (``with_cameras``, ``with_busy``, etc.) to
    configure modes. Each returns a new or existing ``FakeCameraBackend``
    so test setup reads naturally.
    """

    available_indices: list[int] = field(default_factory=lambda: [0])
    _busy: set[int] = field(default_factory=set)
    _missing: set[int] = field(default_factory=set)
    _timeout: set[int] = field(default_factory=set)

    @classmethod
    def with_cameras(cls, indices: list[int]) -> "FakeCameraBackend":
        return cls(available_indices=list(indices))

    # Each _FluentMode below must have a matching `set[int]` field in the dataclass.
    # Adding a new `with_X = _FluentMode("_X")` without also declaring
    # `_X: set[int] = field(default_factory=set)` above will raise AttributeError
    # on first use.
    with_busy = _FluentMode("_busy")
    with_missing = _FluentMode("_missing")
    with_read_timeout = _FluentMode("_timeout")

    def list_cameras(self) -> list[CameraInfo]:
        """List available cameras with their status.

        Cameras marked `with_missing(idx)` where idx is NOT in `available_indices`
        are NOT enumerated — "missing" means "this index doesn't exist on the host,"
        so the fake mirrors real OpenCV's silence about indices it cannot open.
        Cameras marked `with_busy(idx)` where idx IS in `available_indices` DO
        appear in the list with `available=False` — "busy" means "exists but currently
        in use elsewhere."
        """
        out: list[CameraInfo] = []
        for idx in self.available_indices:
            available = idx not in self._busy and idx not in self._missing
            out.append(CameraInfo(index=idx, name=f"Fake Camera {idx}", available=available))
        return out

    def capture(self, index: int, resolution: tuple[int, int]) -> bytes:
        if index in self._missing:
            raise CameraNotFoundError(f"camera {index} marked missing")
        if index not in self.available_indices:
            raise CameraNotFoundError(f"camera {index} not in configured indices")
        if index in self._busy:
            raise CameraBusyError(f"camera {index} is busy")
        if index in self._timeout:
            raise ReadTimeoutError(f"camera {index} did not return a frame in time")
        return _make_minimal_png(resolution)


def _make_minimal_png(resolution: tuple[int, int]) -> bytes:
    """Produce a valid solid-gray PNG of the requested resolution.

    Uses pure-Python PNG construction (no PIL/cv2 dependency) so the
    fake stays light. The image is solid mid-gray RGB; tests only need
    the bytes to be a valid PNG with the right dimensions.
    """
    width, height = resolution
    # IHDR: width, height, bit depth=8, color type=2 (RGB), compression, filter, interlace
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    row_bytes = width * 3
    for _ in range(height):
        raw += b"\x00" + (b"\x80" * row_bytes)  # filter byte + gray pixels
    idat = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return length + tag + data + crc


__all__ = ["FakeCameraBackend"]
