"""WebcamEndpoint — bus endpoint that exposes capture tools via MCP.

Implements the standard Endpoint protocol but ``deliver`` is a no-op:
webcam is tool-only, no inbox, no agent-to-agent envelopes. The
endpoint exists so MCP tools have somewhere to live and config to read.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_core_webcam.audit import AuditEvent, AuditLog
from agent_core_webcam.protocol import (
    CameraBackend,
    CameraBusyError,
    CameraInfo,
    CameraNotFoundError,
    ReadTimeoutError,
)

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


@dataclass(frozen=True)
class CaptureSuccess:
    """Successful capture result."""

    png_bytes: bytes
    file_path: Path | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CaptureError:
    """Failed capture — message is the agent-readable string."""

    message: str


@dataclass(frozen=True)
class ListCamerasSuccess:
    """list_cameras_safe success."""

    cameras: list[CameraInfo]


@dataclass(frozen=True)
class ListCamerasError:
    """list_cameras_safe failure — message is the agent-readable string."""

    message: str


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
        self._handle: BusHandle | None = None
        self._camera_locks: dict[int, asyncio.Lock] = {}
        self._camera_locks_guard = asyncio.Lock()

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        log.info("WebcamEndpoint(name=%s) started; captures=%s", self.name, self.captures_root)

    async def deliver(self, envelope: Envelope) -> None:
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

    async def _lock_for(self, camera_index: int) -> asyncio.Lock:
        async with self._camera_locks_guard:
            if camera_index not in self._camera_locks:
                self._camera_locks[camera_index] = asyncio.Lock()
            return self._camera_locks[camera_index]

    async def capture_frame(
        self,
        *,
        camera_index: int | None = None,
        resolution: tuple[int, int] | list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> tuple[bytes, Path | None, dict[str, Any]]:
        """Capture one frame; return (png_bytes, file_path, metadata).

        Returns ``file_path=None`` when ``save=False``. Always appends an
        audit entry. Errors raise — Task 7 maps them to user-facing
        messages at the MCP boundary.
        """
        idx = camera_index if camera_index is not None else self.default_camera_index
        res = _to_tuple(resolution) if resolution is not None else self.default_resolution
        lock = await self._lock_for(idx)
        async with lock:
            # Enforce capture_timeout_seconds at the async layer: a hung
            # backend read (OpenCV's cap.read() is uninterruptible) would
            # otherwise hold this per-camera lock forever, wedging every future
            # capture on this camera. wait_for releases the lock and surfaces a
            # ReadTimeoutError; the orphaned worker thread is the lesser evil
            # than a permanently-stuck endpoint.
            try:
                png_bytes = await asyncio.wait_for(
                    asyncio.to_thread(self._backend.capture, idx, res),
                    timeout=self.capture_timeout_seconds,
                )
            except TimeoutError as exc:
                raise ReadTimeoutError(
                    f"camera {idx} did not return a frame within "
                    f"{self.capture_timeout_seconds}s"
                ) from exc
        timestamp = datetime.now(UTC).astimezone()
        try:
            all_cams = await asyncio.to_thread(self._backend.list_cameras)
            cam_name = next((c.name for c in all_cams if c.index == idx), f"camera {idx}")
        except Exception:
            cam_name = f"camera {idx}"
        file_path: Path | None = None
        if save:
            file_path = self.captures_root / timestamp.strftime("%Y-%m-%d") / (
                timestamp.strftime("%H%M%S-") + f"{timestamp.microsecond // 1000:03d}.png"
            )
            await asyncio.to_thread(self._write_png, file_path, png_bytes)
        meta = {
            "camera_index": idx,
            "camera_name": cam_name,
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
                    "camera_name": cam_name,
                    "resolution": list(res),
                    "save": save,
                    "note": note,
                    "file_path": str(file_path) if file_path else None,
                    "filesize": len(png_bytes),
                },
            )
        )
        return png_bytes, file_path, meta

    async def capture_frame_safe(
        self,
        *,
        camera_index: int | None = None,
        resolution: tuple[int, int] | list[int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> CaptureSuccess | CaptureError:
        """Like ``capture_frame`` but maps every error to a CaptureError + audit.

        The MCP tool wrapper uses this — it never wants to raise into FastMCP.
        """
        if not self.enabled:
            return await self._error(
                user_message=(
                    "error: webcam endpoint is disabled (enabled=false in config). "
                    "Ask the operator if this is unexpected."
                ),
                audit_error="endpoint disabled",
                camera_index=camera_index,
                resolution=_to_tuple(resolution) if resolution is not None else None,
                save=save,
                note=note,
            )

        # Resolution validation + cap.
        idx = camera_index if camera_index is not None else self.default_camera_index
        try:
            res = _to_tuple(resolution) if resolution is not None else self.default_resolution
        except ValueError as exc:
            return await self._error(
                user_message=(
                    f"error: invalid resolution: {exc}. "
                    f"Expected [width, height] (a list of two ints)."
                ),
                audit_error=f"invalid resolution: {exc}",
                camera_index=idx,
                save=save,
                note=note,
            )
        if res[0] > self.max_resolution[0] or res[1] > self.max_resolution[1]:
            return await self._error(
                user_message=(
                    f"error: requested resolution {res[0]}x{res[1]} exceeds "
                    f"configured max {self.max_resolution[0]}x{self.max_resolution[1]}."
                ),
                audit_error="resolution capped",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )

        try:
            png_bytes, file_path, meta = await self.capture_frame(
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except CameraNotFoundError:
            try:
                cams = await asyncio.to_thread(self._backend.list_cameras)
                available = [c.index for c in cams]
            except Exception:
                available = []
            return await self._error(
                user_message=(
                    f"error: no camera at index {idx} "
                    f"(host has {len(available)} cameras: indices {available}). "
                    f"Call list_cameras to see what's available."
                ),
                audit_error=f"camera {idx} not found",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except CameraBusyError:
            try:
                cams = await asyncio.to_thread(self._backend.list_cameras)
                cam_name = next(
                    (c.name for c in cams if c.index == idx),
                    f"camera {idx}",
                )
            except Exception:
                cam_name = f"camera {idx}"
            return await self._error(
                user_message=(
                    f"error: camera {idx} ({cam_name}) is busy. "
                    f"Likely in use by another application (Zoom, browser, etc.). "
                    f"Try again in a moment."
                ),
                audit_error="camera busy",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except ReadTimeoutError:
            return await self._error(
                user_message=(
                    f"error: camera opened but returned no frame within "
                    f"{self.capture_timeout_seconds}s. Camera may be initializing or obstructed."
                ),
                audit_error="read timeout",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        except OSError as exc:
            return await self._error(
                user_message=(
                    f"error: capture succeeded but failed to write to disk: {exc}. "
                    f"Image is unavailable; retry or set save=false."
                ),
                audit_error=f"disk write failed: {exc}",
                camera_index=idx,
                resolution=res,
                save=save,
                note=note,
            )
        return CaptureSuccess(png_bytes=png_bytes, file_path=file_path, metadata=meta)

    async def list_cameras_safe(self) -> ListCamerasSuccess | ListCamerasError:
        """Enumerate cameras, return list with audit. Best-effort — never raises."""
        if not self.enabled:
            timestamp = datetime.now(UTC).astimezone()
            await self.audit_log.write(
                AuditEvent(
                    timestamp=timestamp,
                    tool="list_cameras",
                    result="error",
                    data={"error": "endpoint disabled"},
                )
            )
            return ListCamerasError(
                message=(
                    "error: webcam endpoint is disabled (enabled=false in config). "
                    "Ask the operator if this is unexpected."
                )
            )
        try:
            cams = await asyncio.to_thread(self._backend.list_cameras)
        except Exception as exc:
            timestamp = datetime.now(UTC).astimezone()
            await self.audit_log.write(
                AuditEvent(
                    timestamp=timestamp,
                    tool="list_cameras",
                    result="error",
                    data={"error": f"backend list_cameras failed: {exc}"},
                )
            )
            return ListCamerasError(
                message=(
                    f"error: failed to enumerate cameras: {exc}. "
                    f"Check that the camera driver is responsive."
                )
            )
        timestamp = datetime.now(UTC).astimezone()
        await self.audit_log.write(
            AuditEvent(
                timestamp=timestamp,
                tool="list_cameras",
                result="ok",
                data={"camera_count": len(cams)},
            )
        )
        return ListCamerasSuccess(cameras=list(cams))

    async def _error(
        self,
        *,
        user_message: str,
        audit_error: str,
        camera_index: int | None = None,
        resolution: tuple[int, int] | None = None,
        save: bool = True,
        note: str | None = None,
    ) -> CaptureError:
        timestamp = datetime.now(UTC).astimezone()
        await self.audit_log.write(
            AuditEvent(
                timestamp=timestamp,
                tool="capture_webcam_frame",
                result="error",
                data={
                    "camera_index": camera_index,
                    "resolution": list(resolution) if resolution else None,
                    "save": save,
                    "note": note,
                    "error": audit_error,
                },
            )
        )
        return CaptureError(message=user_message)

    @staticmethod
    def _write_png(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


__all__ = [
    "CaptureError",
    "CaptureSuccess",
    "ListCamerasError",
    "ListCamerasSuccess",
    "WebcamEndpoint",
]
