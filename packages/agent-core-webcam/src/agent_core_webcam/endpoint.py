"""WebcamEndpoint — bus endpoint that exposes capture tools via MCP.

Implements the standard Endpoint protocol but ``deliver`` is a no-op:
webcam is tool-only, no inbox, no agent-to-agent envelopes. The
endpoint exists so MCP tools have somewhere to live and config to read.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_core_webcam.audit import AuditLog
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


__all__ = ["WebcamEndpoint"]
