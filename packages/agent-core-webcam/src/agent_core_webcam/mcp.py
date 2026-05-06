"""Agent-facing MCP tool surface for the webcam endpoint.

Two tools: ``capture_webcam_frame`` and ``list_cameras``. Each returns
a list of MCP content blocks. On error, only TextContent is returned —
no image. The endpoint's ``capture_frame_safe`` / ``list_cameras_safe``
methods do the error mapping; this module just adapts the result type
to MCP content blocks.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from mcp.types import ImageContent, TextContent

from agent_core_webcam.endpoint import (
    CaptureError,
    CaptureSuccess,
    ListCamerasError,
    ListCamerasSuccess,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from agent_core_webcam.endpoint import WebcamEndpoint


def register_webcam_tools(*, mcp: "FastMCP", endpoint: "WebcamEndpoint") -> None:
    """Register the two webcam tools on a FastMCP server."""

    @mcp.tool(
        name="capture_webcam_frame",
        description=(
            "Capture a single frame from a connected webcam. Returns the image "
            "inline (so you can see it immediately) plus a saved file path you "
            "can later attach to a Discord message, archive, or revisit. Use "
            "camera_index=0 for the default camera; call list_cameras to "
            "enumerate other devices."
        ),
    )
    async def _capture(
        camera_index: int = 0,
        resolution: list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> list[Any]:
        """Capture one frame; return [ImageContent, TextContent] on success."""
        res_tuple = tuple(resolution) if resolution else None
        result = await endpoint.capture_frame_safe(
            camera_index=camera_index,
            resolution=res_tuple,
            save=save,
            note=note,
        )
        if isinstance(result, CaptureError):
            return [TextContent(type="text", text=result.message)]
        assert isinstance(result, CaptureSuccess)
        meta = result.metadata
        cam_name = next(
            (c.name for c in endpoint._backend.list_cameras() if c.index == meta["camera_index"]),
            f"camera {meta['camera_index']}",
        )
        text = (
            f"Captured frame from camera {meta['camera_index']} ({cam_name}) "
            f"at {meta['resolution'][0]}x{meta['resolution'][1]}.\n"
            f"Path: {meta['file_path']}\n"
            f"Timestamp: {meta['timestamp']}\n"
            f"Filesize: {meta['filesize']} bytes"
        )
        return [
            ImageContent(
                type="image",
                data=base64.b64encode(result.png_bytes).decode("ascii"),
                mimeType="image/png",
            ),
            TextContent(type="text", text=text),
        ]

    @mcp.tool(
        name="list_cameras",
        description=(
            "List all webcams detected on this host. Use the returned `index` "
            "as the `camera_index` argument to capture_webcam_frame."
        ),
    )
    async def _list_cameras() -> list[Any]:
        result = await endpoint.list_cameras_safe()
        if isinstance(result, ListCamerasError):
            return [TextContent(type="text", text=result.message)]
        assert isinstance(result, ListCamerasSuccess)
        payload = [
            {"index": c.index, "name": c.name, "available": c.available}
            for c in result.cameras
        ]
        return [TextContent(type="text", text=json.dumps(payload))]


__all__ = ["register_webcam_tools"]
