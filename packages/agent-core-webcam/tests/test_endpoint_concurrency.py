"""Concurrency: same camera serializes; different cameras parallel."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend


@dataclass
class _SlowFake:
    delay: float
    available_indices: list[int]
    busy_calls: dict[int, list[tuple[float, float]]] = None  # type: ignore

    def __post_init__(self):
        self.busy_calls = {i: [] for i in self.available_indices}

    def list_cameras(self):
        return FakeCameraBackend.with_cameras(self.available_indices).list_cameras()

    # Minimal valid 1x1 PNG — avoids zlib.compress GIL cost at full resolution.
    _TINY_PNG = FakeCameraBackend.with_cameras([0]).capture(0, (1, 1))

    def capture(self, index, resolution):
        if index not in self.available_indices:
            from agent_core_webcam.protocol import CameraNotFoundError
            raise CameraNotFoundError(f"camera {index} not configured")
        start = time.monotonic()
        time.sleep(self.delay)  # blocking sleep — runs in to_thread
        end = time.monotonic()
        self.busy_calls[index].append((start, end))
        return self._TINY_PNG


async def test_same_camera_calls_serialize(tmp_path):
    backend = _SlowFake(delay=0.10, available_indices=[0])
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=backend,
    )
    # Two parallel capture calls on the SAME camera should NOT overlap.
    await asyncio.gather(
        ep.capture_frame_safe(camera_index=0),
        ep.capture_frame_safe(camera_index=0),
    )
    intervals = backend.busy_calls[0]
    assert len(intervals) == 2
    # Second call's start must be >= first call's end (no overlap).
    assert intervals[1][0] >= intervals[0][1] - 0.005  # 5ms slop


async def test_different_cameras_run_in_parallel(tmp_path):
    backend = _SlowFake(delay=0.10, available_indices=[0, 1])
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=backend,
    )
    start = time.monotonic()
    await asyncio.gather(
        ep.capture_frame_safe(camera_index=0),
        ep.capture_frame_safe(camera_index=1),
    )
    elapsed = time.monotonic() - start
    # If serialized, would take ~0.20s. Parallel should be ~0.10s.
    # Use 0.18 as the threshold to guard against system jitter.
    assert elapsed < 0.18
