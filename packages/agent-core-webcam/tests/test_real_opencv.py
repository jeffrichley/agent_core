"""Real-OpenCV integration test (gated).

Skipped by default. Set WEBCAM_INTEGRATION_TEST=1 to enable. Verifies
the cv2 adapter compiles and behaves consistently with the Fake backend
for the no-camera-present case.
"""
from __future__ import annotations

import os

import pytest
from agent_core_webcam.opencv_backend import OpenCVCameraBackend
from agent_core_webcam.protocol import CameraNotFoundError

pytestmark = pytest.mark.skipif(
    os.environ.get("WEBCAM_INTEGRATION_TEST") != "1",
    reason="set WEBCAM_INTEGRATION_TEST=1 to run real cv2 tests",
)


def test_capture_unknown_index_raises_not_found():
    """Asking for a clearly absent camera index raises CameraNotFoundError.
    Uses index=99 which essentially never exists. If a host actually has 100
    cameras, switch to a higher number. If THAT also fails, we'll laugh."""
    backend = OpenCVCameraBackend(timeout_seconds=2.0)
    with pytest.raises(CameraNotFoundError):
        backend.capture(99, (640, 480))


def test_list_cameras_returns_a_list():
    """Just verifies the call returns without raising. Result count is
    host-dependent; at minimum it should be a list."""
    backend = OpenCVCameraBackend(timeout_seconds=2.0)
    cams = backend.list_cameras()
    assert isinstance(cams, list)
