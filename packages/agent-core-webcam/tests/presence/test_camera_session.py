"""CameraSession guard behavior — the open/read camera path is proven live."""

from __future__ import annotations

import pytest
from agent_core_webcam.presence.camera_session import CameraSession


def test_read_outside_context_raises() -> None:
    """Reading before entering the context (no open device) is a clear error."""
    session = CameraSession(index=0)
    with pytest.raises(RuntimeError, match="outside its context"):
        session.read_bgr()
