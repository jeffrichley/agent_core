"""register_webcam_tools mounts the two tools on a FastMCP server."""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend
from agent_core_webcam.mcp import register_webcam_tools
from fastmcp import FastMCP
from pydantic import ValidationError


def _new_endpoint(tmp_path: Path, **overrides) -> WebcamEndpoint:
    return WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0, 1]),
        **overrides,
    )


async def _call(mcp: FastMCP, name: str, args: dict):
    """Invoke a tool via FastMCP's in-process path and return the content blocks."""
    result = await mcp.call_tool(name, args)
    return result.content


async def test_registers_both_tools(tmp_path: Path):
    mcp = FastMCP("test-server")
    register_webcam_tools(mcp=mcp, endpoint=_new_endpoint(tmp_path))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "capture_webcam_frame" in names
    assert "list_cameras" in names


async def test_capture_tool_returns_image_and_text(tmp_path: Path):
    mcp = FastMCP("test-server")
    ep = _new_endpoint(tmp_path)
    register_webcam_tools(mcp=mcp, endpoint=ep)
    result = await _call(mcp, "capture_webcam_frame", {"camera_index": 0})
    types = [getattr(b, "type", None) for b in result]
    assert "image" in types
    assert "text" in types


async def test_capture_tool_error_returns_text_only(tmp_path: Path):
    mcp = FastMCP("test-server")
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        enabled=False,
        camera_backend=FakeCameraBackend.with_cameras([0]),
    )
    register_webcam_tools(mcp=mcp, endpoint=ep)
    result = await _call(mcp, "capture_webcam_frame", {"camera_index": 0})
    types = [getattr(b, "type", None) for b in result]
    # Error path: only text, no image.
    assert "image" not in types
    assert "text" in types
    text_block = next(b for b in result if getattr(b, "type", None) == "text")
    assert "disabled" in text_block.text


async def test_list_cameras_tool_returns_json_text(tmp_path: Path):
    mcp = FastMCP("test-server")
    register_webcam_tools(mcp=mcp, endpoint=_new_endpoint(tmp_path))
    result = await _call(mcp, "list_cameras", {})
    text_block = next(b for b in result if getattr(b, "type", None) == "text")
    assert "0" in text_block.text  # at minimum, camera index 0 in the JSON


async def test_capture_tool_resolution_validation_at_mcp_boundary(tmp_path: Path):
    """Pydantic should reject a malformed resolution before reaching the endpoint."""
    mcp = FastMCP("test-server")
    register_webcam_tools(mcp=mcp, endpoint=_new_endpoint(tmp_path))
    with pytest.raises(ValidationError):
        # A dict where a [w, h] list is expected — should fail validation.
        await mcp.call_tool(
            "capture_webcam_frame",
            {"camera_index": 0, "resolution": "not-a-list"},
        )
