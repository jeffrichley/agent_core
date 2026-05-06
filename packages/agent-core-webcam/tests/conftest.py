"""Shared fixtures for webcam endpoint tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend


@pytest.fixture
def fake_backend() -> FakeCameraBackend:
    """Default fake — one camera at index 0, all calls succeed."""
    return FakeCameraBackend.with_cameras([0])


@pytest.fixture
def endpoint(tmp_path: Path, fake_backend: FakeCameraBackend) -> WebcamEndpoint:
    """A fresh WebcamEndpoint pointed at tmp_path with the default fake backend."""
    return WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=fake_backend,
    )
