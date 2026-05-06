"""Error-path mapping for capture_frame.

Each failure mode in the spec's error-handling table maps to:
- A ``CaptureError`` result with a user-readable message
- An audit entry with ``result: "error"`` plus structured detail
"""
from __future__ import annotations

import json

from agent_core_webcam.endpoint import (
    CaptureError,
    CaptureSuccess,
    WebcamEndpoint,
)
from agent_core_webcam.fake import FakeCameraBackend


async def test_disabled_endpoint_returns_kill_switch_error(tmp_path, fake_backend):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        enabled=False,
        camera_backend=fake_backend,
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureError)
    assert "disabled" in result.message
    assert "enabled=false" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["result"] == "error"
    assert line["data"]["error"] == "endpoint disabled"


async def test_camera_not_found_returns_clean_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]),
    )
    result = await ep.capture_frame_safe(camera_index=5)
    assert isinstance(result, CaptureError)
    assert "no camera at index 5" in result.message
    assert "list_cameras" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "camera 5 not found"


async def test_camera_busy_returns_clean_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0]).with_busy(0),
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureError)
    assert "busy" in result.message.lower()
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "camera busy"


async def test_read_timeout_returns_clean_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        capture_timeout_seconds=2.5,
        camera_backend=FakeCameraBackend.with_cameras([0]).with_read_timeout(0),
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureError)
    assert "no frame" in result.message.lower()
    assert "2.5" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "read timeout"


async def test_resolution_exceeds_max_returns_error(tmp_path, fake_backend):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        max_resolution=(1920, 1080),
        camera_backend=fake_backend,
    )
    result = await ep.capture_frame_safe(camera_index=0, resolution=(7680, 4320))
    assert isinstance(result, CaptureError)
    assert "exceeds configured max" in result.message
    assert "7680x4320" in result.message
    assert "1920x1080" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"] == "resolution capped"


async def test_disk_write_failure_returns_error(tmp_path, fake_backend, monkeypatch):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=fake_backend,
    )

    def _broken_write(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(WebcamEndpoint, "_write_png", staticmethod(_broken_write))
    result = await ep.capture_frame_safe(camera_index=0, save=True)
    assert isinstance(result, CaptureError)
    assert "disk full" in result.message
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["data"]["error"].startswith("disk write failed")


async def test_success_path_returns_capture_success(tmp_path, fake_backend):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=fake_backend,
    )
    result = await ep.capture_frame_safe(camera_index=0)
    assert isinstance(result, CaptureSuccess)
    assert result.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.file_path is not None
