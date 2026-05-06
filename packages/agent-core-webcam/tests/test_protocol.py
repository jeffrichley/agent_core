"""Camera backend protocol shape + exception taxonomy.

These tests pin the surface that fake and real backends must implement.
"""
from __future__ import annotations

import dataclasses

import pytest

from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
    WebcamError,
)


def test_camera_info_is_a_simple_dataclass():
    info = CameraInfo(index=0, name="Integrated Camera", available=True)
    assert info.index == 0
    assert info.name == "Integrated Camera"
    assert info.available is True

    with pytest.raises(dataclasses.FrozenInstanceError):
        info.index = 99


def test_exception_hierarchy_descends_from_webcam_error():
    assert issubclass(CameraBusyError, WebcamError)
    assert issubclass(CameraNotFoundError, WebcamError)
    assert issubclass(ReadTimeoutError, WebcamError)


def test_camera_backend_is_runtime_checkable():
    class _MinimalBackend:
        def list_cameras(self): return []
        def capture(self, index, resolution): return b""

    assert isinstance(_MinimalBackend(), CameraBackend)


def test_camera_backend_rejects_object_missing_methods():
    class _NotABackend:
        def list_cameras(self): return []
        # missing capture()

    assert not isinstance(_NotABackend(), CameraBackend)
