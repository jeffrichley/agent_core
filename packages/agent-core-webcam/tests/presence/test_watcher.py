"""Watcher loop — driven by fakes, no camera and no model."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from agent_core_webcam.presence.enrollment import Template
from agent_core_webcam.presence.state import read_state
from agent_core_webcam.presence.watcher import run_watch

_JEFF = np.array([1.0, 0.0, 0.0], dtype=np.float32)
_STRANGER = np.array([0.0, 1.0, 0.0], dtype=np.float32)
_BIG = (100, 100, 300, 400)
_SMALL = (10, 10, 40, 50)


class _FakeSession:
    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def warmup(self, n: int = 3) -> None:
        return None

    def read_bgr(self) -> np.ndarray:
        return np.zeros((2, 2, 3), np.uint8)


def _template() -> Template:
    return Template(name="jeff", embeddings=[_JEFF])


def _run(tmp_path: Path, frames: list, iterations: int) -> Path:
    """Run the watcher `iterations` cycles; `frames[i]` is embed_faces output for cycle i."""
    state_path = tmp_path / "state.json"
    calls = {"i": 0}

    def fake_embed(_analyzer, _frame):  # type: ignore[no-untyped-def]
        out = frames[min(calls["i"], len(frames) - 1)]
        calls["i"] += 1
        return out

    run_watch(
        template=_template(),
        state_path=state_path,
        principal="jeff",
        threshold=0.5,
        interval=0.0,
        source="test-cam",
        camera_index=0,
        iterations=iterations,
        session_factory=lambda _idx: _FakeSession(),
        analyzer_factory=lambda: object(),
        embed_faces_fn=fake_embed,
        sleep_fn=lambda _s: None,
        clock=lambda: 1234.0,
    )
    return state_path


def test_jeff_alone_writes_trusted_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = _run(tmp_path, frames=[[(_JEFF, _BIG, 0.9)]], iterations=1)
    s = read_state(path)
    assert s is not None
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 0
    assert s.updated_at == 1234.0 and s.source == "test-cam"


def test_stranger_present_raises_unknown_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = _run(tmp_path, frames=[[(_JEFF, _BIG, 0.9), (_STRANGER, _SMALL, 0.9)]], iterations=1)
    s = read_state(path)
    assert s is not None
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 1


def test_empty_frame_is_nobody(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = _run(tmp_path, frames=[[]], iterations=1)
    s = read_state(path)
    assert s is not None
    assert s.at_desk is False and s.known == [] and s.unknown_count == 0


def test_cycle_error_skips_write_and_continues(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # cycle 0 writes a good Jeff state; cycle 1's embed raises -> write skipped,
    # loop survives; the last good state remains readable.
    state_path = tmp_path / "state.json"
    calls = {"i": 0}

    def flaky_embed(_analyzer, _frame):  # type: ignore[no-untyped-def]
        i = calls["i"]
        calls["i"] += 1
        if i == 1:
            raise RuntimeError("transient frame failure")
        return [(_JEFF, _BIG, 0.9)]

    run_watch(
        template=_template(),
        state_path=state_path,
        principal="jeff",
        threshold=0.5,
        interval=0.0,
        source="test-cam",
        camera_index=0,
        iterations=2,
        session_factory=lambda _idx: _FakeSession(),
        analyzer_factory=lambda: object(),
        embed_faces_fn=flaky_embed,
        sleep_fn=lambda _s: None,
        clock=lambda: 1234.0,
    )
    s = read_state(state_path)
    assert s is not None and s.at_desk is True  # cycle-0 state survived cycle-1 error
    assert calls["i"] == 2  # loop ran both cycles (didn't crash on the error)
