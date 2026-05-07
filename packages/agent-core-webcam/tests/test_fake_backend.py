"""FakeCameraBackend behavior tests.

The fake is the workhorse for endpoint unit tests. Its modes
(with_busy, with_missing, with_read_timeout) drive every error path
in the endpoint's failure-mode table.
"""
from __future__ import annotations

import struct

import pytest
from agent_core_webcam.fake import FakeCameraBackend
from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraNotFoundError,
    ReadTimeoutError,
)


def test_fake_satisfies_protocol():
    assert isinstance(FakeCameraBackend(), CameraBackend)


def test_default_fake_has_one_camera():
    fake = FakeCameraBackend()
    cams = fake.list_cameras()
    assert len(cams) == 1
    assert cams[0].index == 0
    assert cams[0].available is True


def test_with_cameras_lists_each():
    fake = FakeCameraBackend.with_cameras([0, 1, 2])
    cams = fake.list_cameras()
    assert [c.index for c in cams] == [0, 1, 2]
    assert all(c.available for c in cams)


def test_capture_returns_valid_png_bytes():
    fake = FakeCameraBackend()
    data = fake.capture(0, (1280, 720))
    # PNG magic number: 89 50 4E 47 0D 0A 1A 0A
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR chunk follows; width/height are big-endian u32 at bytes 16–24
    width, height = struct.unpack(">II", data[16:24])
    assert width == 1280
    assert height == 720


def test_with_missing_raises_not_found():
    fake = FakeCameraBackend.with_cameras([0]).with_missing(3)
    with pytest.raises(CameraNotFoundError, match="3"):
        fake.capture(3, (640, 480))


def test_with_busy_raises_busy():
    fake = FakeCameraBackend.with_busy(0)
    with pytest.raises(CameraBusyError, match="0"):
        fake.capture(0, (640, 480))


def test_with_read_timeout_raises_timeout():
    fake = FakeCameraBackend.with_read_timeout(0)
    with pytest.raises(ReadTimeoutError):
        fake.capture(0, (640, 480))


def test_capture_unknown_index_raises_not_found_by_default():
    """Asking for a camera not in the configured set raises like real OpenCV."""
    fake = FakeCameraBackend.with_cameras([0])
    with pytest.raises(CameraNotFoundError, match="5"):
        fake.capture(5, (640, 480))


def test_modes_compose():
    """with_cameras, then with_busy on a configured camera."""
    fake = FakeCameraBackend.with_cameras([0, 1]).with_busy(0)
    # Camera 1 still works
    data = fake.capture(1, (320, 240))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # Camera 0 is busy
    with pytest.raises(CameraBusyError):
        fake.capture(0, (320, 240))


def test_partial_failure_lists_unavailable_cameras():
    """A camera marked busy still appears in list_cameras with available=False."""
    fake = FakeCameraBackend.with_cameras([0, 1]).with_busy(1)
    cams = fake.list_cameras()
    by_idx = {c.index: c for c in cams}
    assert by_idx[0].available is True
    assert by_idx[1].available is False
