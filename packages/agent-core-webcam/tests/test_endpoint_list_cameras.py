"""list_cameras_safe: enumeration + kill switch + audit."""
from __future__ import annotations

import json

from agent_core_webcam.endpoint import (
    ListCamerasError,
    ListCamerasSuccess,
    WebcamEndpoint,
)
from agent_core_webcam.fake import FakeCameraBackend


async def test_list_returns_enumeration(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]),
    )
    result = await ep.list_cameras_safe()
    assert isinstance(result, ListCamerasSuccess)
    assert [c.index for c in result.cameras] == [0, 1]
    line = json.loads(ep.audit_log.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["tool"] == "list_cameras"
    assert line["result"] == "ok"
    assert line["data"]["camera_count"] == 2


async def test_list_returns_partial_failure_subset(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]).with_busy(1),
    )
    result = await ep.list_cameras_safe()
    assert isinstance(result, ListCamerasSuccess)
    by_idx = {c.index: c for c in result.cameras}
    assert by_idx[0].available is True
    assert by_idx[1].available is False


async def test_list_disabled_returns_kill_switch_error(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        enabled=False,
        camera_backend=FakeCameraBackend(),
    )
    result = await ep.list_cameras_safe()
    assert isinstance(result, ListCamerasError)
    assert "disabled" in result.message
