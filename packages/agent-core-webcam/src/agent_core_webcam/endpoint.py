"""WebcamEndpoint — bus endpoint that exposes capture tools via MCP.

Implements the standard Endpoint protocol but ``deliver`` is a no-op:
webcam is tool-only, no inbox, no agent-to-agent envelopes. The
endpoint exists so MCP tools have somewhere to live and config to read.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agent_core_webcam.audit import AuditEvent, AuditLog
from agent_core_webcam.protocol import CameraBackend

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


def _to_tuple(value: tuple[int, int] | list[int]) -> tuple[int, int]:
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError(f"resolution must be [width, height], got {value!r}")
        return value
    if isinstance(value, list) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    raise ValueError(f"resolution must be [width, height], got {value!r}")


class WebcamEndpoint:
    """Tool-only bus endpoint backing the webcam MCP tool surface."""

    def __init__(
        self,
        *,
        name: str,
        captures_root: Path | str | None = None,
        audit_log_path: Path | str | None = None,
        default_camera_index: int = 0,
        default_resolution: tuple[int, int] | list[int] = (1280, 720),
        max_resolution: tuple[int, int] | list[int] = (3840, 2160),
        capture_timeout_seconds: float = 3.0,
        enabled: bool = True,
        camera_backend: CameraBackend | None = None,
    ):
        self.name = name
        self.captures_root = (
            Path(captures_root)
            if captures_root is not None
            else Path.home() / ".agent-core" / "webcam" / name
        )
        audit_path = (
            Path(audit_log_path)
            if audit_log_path is not None
            else self.captures_root / "audit.jsonl"
        )
        self.audit_log = AuditLog(audit_path)
        self.default_camera_index = default_camera_index
        self.default_resolution = _to_tuple(default_resolution)
        self.max_resolution = _to_tuple(max_resolution)
        self.capture_timeout_seconds = capture_timeout_seconds
        self.enabled = enabled
        if camera_backend is None:
            from agent_core_webcam.opencv_backend import OpenCVCameraBackend
            camera_backend = OpenCVCameraBackend(timeout_seconds=capture_timeout_seconds)
        self._backend: CameraBackend = camera_backend
        self._handle: "BusHandle | None" = None

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        log.info("WebcamEndpoint(name=%s) started; captures=%s", self.name, self.captures_root)

    async def deliver(self, envelope: "Envelope") -> None:
        # Webcam is tool-only; envelopes addressed to us are unexpected.
        # Log at debug, then ack so the bus doesn't redeliver or dead-letter.
        log.debug(
            "WebcamEndpoint(name=%s) ignoring delivered envelope %s", self.name, envelope.id
        )
        if self._handle is not None:
            await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        self._handle = None
        log.info("WebcamEndpoint(name=%s) stopped", self.name)

    async def capture_frame(
        self,
        *,
        camera_index: int | None = None,
        resolution: tuple[int, int] | list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> tuple[bytes, Path | None, dict]:
        """Capture one frame; return (png_bytes, file_path, metadata).

        Returns ``file_path=None`` when ``save=False``. Always appends an
        audit entry. Errors raise — Task 7 maps them to user-facing
        messages at the MCP boundary.
        """
        idx = camera_index if camera_index is not None else self.default_camera_index
        res = _to_tuple(resolution) if resolution is not None else self.default_resolution
        png_bytes = await asyncio.to_thread(self._backend.capture, idx, res)
        timestamp = datetime.now(timezone.utc).astimezone()
        file_path: Path | None = None
        if save:
            file_path = self.captures_root / timestamp.strftime("%Y-%m-%d") / (
                timestamp.strftime("%H%M%S-") + f"{timestamp.microsecond // 1000:03d}.png"
            )
            await asyncio.to_thread(self._write_png, file_path, png_bytes)
        meta = {
            "camera_index": idx,
            "resolution": res,
            "timestamp": timestamp.isoformat(),
            "filesize": len(png_bytes),
            "file_path": str(file_path) if file_path else None,
        }
        await self.audit_log.write(
            AuditEvent(
                timestamp=timestamp,
                tool="capture_webcam_frame",
                result="ok",
                data={
                    "camera_index": idx,
                    "resolution": list(res),
                    "save": save,
                    "note": note,
                    "file_path": str(file_path) if file_path else None,
                    "filesize": len(png_bytes),
                },
            )
        )
        return png_bytes, file_path, meta

    @staticmethod
    def _write_png(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


__all__ = ["WebcamEndpoint"]
