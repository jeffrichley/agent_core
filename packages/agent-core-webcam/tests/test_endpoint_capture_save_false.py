"""save=False — capture returns bytes inline but does not touch disk."""
from __future__ import annotations

import json

from agent_core_webcam.endpoint import CaptureSuccess, WebcamEndpoint


async def test_save_false_returns_png_with_no_file(endpoint: WebcamEndpoint):
    result = await endpoint.capture_frame_safe(camera_index=0, save=False)
    assert isinstance(result, CaptureSuccess)
    assert result.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.file_path is None
    # The captures dir might exist from a previous test run; the important
    # assertion is that THIS capture wrote nothing under it.
    if endpoint.captures_root.exists():
        files = [p for p in endpoint.captures_root.rglob("*.png")]
        assert files == []


async def test_save_false_audit_records_save_flag_and_null_file_path(endpoint: WebcamEndpoint):
    await endpoint.capture_frame_safe(camera_index=0, save=False)
    line = json.loads(endpoint.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["result"] == "ok"
    assert line["data"]["save"] is False
    assert line["data"]["file_path"] is None
