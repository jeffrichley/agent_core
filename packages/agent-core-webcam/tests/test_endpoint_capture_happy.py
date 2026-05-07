"""capture_frame happy path: PNG bytes + disk write + audit entry."""
from __future__ import annotations

import json
import struct

from agent_core_webcam.endpoint import WebcamEndpoint


async def test_capture_returns_png_bytes_and_path_and_metadata(endpoint: WebcamEndpoint):
    png_bytes, file_path, meta = await endpoint.capture_frame(
        camera_index=0,
        resolution=(1280, 720),
        save=True,
        note=None,
    )
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png_bytes[16:24])
    assert (width, height) == (1280, 720)
    assert file_path is not None
    assert file_path.exists()
    assert file_path.read_bytes() == png_bytes
    assert meta["camera_index"] == 0
    assert "camera_name" in meta
    assert meta["resolution"] == (1280, 720)
    assert meta["filesize"] == len(png_bytes)
    assert "timestamp" in meta


async def test_capture_writes_to_date_bucketed_directory(endpoint: WebcamEndpoint):
    _, file_path, _ = await endpoint.capture_frame(camera_index=0)
    # date bucket is YYYY-MM-DD
    date_bucket = file_path.parent.name
    assert len(date_bucket) == 10
    assert date_bucket[4] == "-" and date_bucket[7] == "-"
    # filename is HHMMSS-millis.png
    assert file_path.suffix == ".png"
    stem = file_path.stem
    assert "-" in stem and stem.replace("-", "").isdigit()


async def test_capture_uses_default_camera_when_omitted(endpoint: WebcamEndpoint):
    _, _, meta = await endpoint.capture_frame()
    assert meta["camera_index"] == endpoint.default_camera_index


async def test_capture_uses_default_resolution_when_omitted(endpoint: WebcamEndpoint):
    _, _, meta = await endpoint.capture_frame()
    assert meta["resolution"] == endpoint.default_resolution


async def test_capture_appends_audit_entry(endpoint: WebcamEndpoint):
    await endpoint.capture_frame(
        camera_index=0,
        resolution=(640, 480),
        note="checking the desk",
    )
    audit_text = endpoint.audit_log.path.read_text(encoding="utf-8")
    line = audit_text.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["tool"] == "capture_webcam_frame"
    assert parsed["result"] == "ok"
    assert parsed["data"]["camera_index"] == 0
    assert parsed["data"]["resolution"] == [640, 480]
    assert parsed["data"]["note"] == "checking the desk"
    assert parsed["data"]["save"] is True
    assert "file_path" in parsed["data"]
