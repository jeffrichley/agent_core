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
    await asyncio.gather(
        ep.capture_frame_safe(camera_index=0),
        ep.capture_frame_safe(camera_index=1),
    )
    # Parallelism is proven by OVERLAP, not by elapsed time: the two cameras'
    # busy intervals must intersect. This is threshold-independent — it holds
    # however slowly the runner schedules the threads — and mirrors the sibling
    # `test_same_camera_calls_serialize`'s use of busy_calls.
    (s0, e0) = backend.busy_calls[0][0]
    (s1, e1) = backend.busy_calls[1][0]
    assert max(s0, s1) < min(e0, e1), (
        f"camera intervals do not overlap (not parallel): cam0=({s0}, {e0}) cam1=({s1}, {e1})"
    )
    # NO WALL-CLOCK BOUND HERE, deliberately. There used to be an
    # `assert elapsed < X` sanity check. It was widened three times
    # (0.18 -> 0.25 -> 0.40) chasing Windows-runner scheduling latency and
    # still failed at 0.61 on 2026-07-26, wedging foreman ticket 33262 at
    # NeedsHelp for nine days over a PR that does not touch this package.
    #
    # A duration threshold measures runner load, not behaviour, so every
    # widening buys time rather than correctness. The overlap assertion above
    # already proves the property under test and cannot flake. Re-adding a
    # timing bound would re-introduce the flake with a bigger number.
    # See agent_core#564.
