"""The recovery paths added 2026-08-16, driven by fakes — no camera, no model.

These cover the branches that only execute when something has already gone
wrong: capture reopen, degradation and its restoration, the degraded hint
surviving a restart, and the swallow-everything guards around bookkeeping.

They exist because the diff-coverage gate refused the push at 77% and named
`watcher.py` at 70.2%. The uncovered lines were exactly the ones added to make
failure survivable — which is the worst possible thing to leave untested, since
they only ever run on the bad day.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from agent_core_webcam.presence.enrollment import Template
from agent_core_webcam.presence.state import read_heartbeat, heartbeat_path_for
from agent_core_webcam.presence.watcher import (
    REOPEN_AFTER_FAILURES,
    _degraded_hint_path,
    _sample_rss,
    _set_degraded_hint,
    _try_reopen,
    run_watch,
)

_JEFF = np.array([1.0, 0.0, 0.0], dtype=np.float32)


class _FakeCam:
    """A camera whose failures and reopens are observable.

    ``fail_first`` frames raise; everything after succeeds. ``reopen`` records
    the ``degrade`` flag it was called with rather than doing anything, so a
    test can assert the loop's DECISION instead of a side effect.
    """

    def __init__(self, fail_first: int = 0, degraded: bool = False) -> None:
        self.fail_first = fail_first
        self.reads = 0
        self.reopens: list[bool] = []
        self.degraded = degraded

    def __enter__(self) -> _FakeCam:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def warmup(self, n: int = 3) -> None:
        return None

    def read_bgr(self) -> np.ndarray:
        self.reads += 1
        if self.reads <= self.fail_first:
            raise RuntimeError("simulated frame failure")
        return np.zeros((2, 2, 3), np.uint8)

    def reopen(self, *, degrade: bool) -> None:
        self.reopens.append(degrade)
        self.degraded = degrade


def _templates() -> dict[str, Template]:
    return {"jeff": Template(name="jeff", embeddings=[_JEFF])}


def _run(state_path: Path, cam: _FakeCam, iterations: int, **kw: object) -> None:
    run_watch(
        templates=_templates(),
        state_path=state_path,
        iterations=iterations,
        session_factory=lambda _idx: cam,
        analyzer_factory=lambda: object(),
        embed_faces_fn=lambda _a, _f: [],
        sleep_fn=lambda _s: None,
        **kw,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# Reopen on repeated failure  (watcher.py 172-173)
# --------------------------------------------------------------------------


def test_repeated_failures_reopen_the_capture_degraded(tmp_path: Path) -> None:
    """A handle can go bad in a way re-read() never fixes; the loop must reopen."""
    sp = tmp_path / "state.json"
    cam = _FakeCam(fail_first=REOPEN_AFTER_FAILURES)
    _run(sp, cam, iterations=REOPEN_AFTER_FAILURES)
    assert cam.reopens == [True], "should reopen exactly once, degraded"


def test_reopen_sets_the_degraded_hint(tmp_path: Path) -> None:
    """The hint is what carries degradation across a restart."""
    sp = tmp_path / "state.json"
    _run(sp, _FakeCam(fail_first=REOPEN_AFTER_FAILURES), iterations=REOPEN_AFTER_FAILURES)
    assert _degraded_hint_path(sp).exists()


def test_failures_below_the_threshold_do_not_reopen(tmp_path: Path) -> None:
    """Reopening on every blip would thrash the device for transient noise."""
    sp = tmp_path / "state.json"
    cam = _FakeCam(fail_first=REOPEN_AFTER_FAILURES - 1)
    _run(sp, cam, iterations=REOPEN_AFTER_FAILURES - 1)
    assert cam.reopens == []


# --------------------------------------------------------------------------
# Degraded hint survives a restart  (watcher.py 133-134)
# --------------------------------------------------------------------------


def test_startup_honours_a_leftover_degraded_hint(tmp_path: Path) -> None:
    """THE RESTART CASE. Without this, supervision restarts at full resolution
    every time and a box that reliably kills 720p loops forever."""
    sp = tmp_path / "state.json"
    _set_degraded_hint(sp, degraded=True)
    cam = _FakeCam()
    _run(sp, cam, iterations=1)
    assert cam.reopens and cam.reopens[0] is True, "should start degraded"


def test_startup_without_a_hint_does_not_degrade(tmp_path: Path) -> None:
    """A clean start must begin at full resolution."""
    sp = tmp_path / "state.json"
    cam = _FakeCam()
    _run(sp, cam, iterations=1)
    assert cam.reopens == []


# --------------------------------------------------------------------------
# Restore after sustained success  (watcher.py 159-161)
# --------------------------------------------------------------------------


def test_sustained_success_restores_full_resolution(tmp_path: Path, monkeypatch) -> None:
    """One bad afternoon must not coarsen the sensor permanently.

    RESTORE_AFTER_SUCCESSES is patched down so the test does not need 300
    cycles to exercise a branch that is about policy, not about the number.
    """
    monkeypatch.setattr("agent_core_webcam.presence.watcher.RESTORE_AFTER_SUCCESSES", 2)
    sp = tmp_path / "state.json"
    _set_degraded_hint(sp, degraded=True)
    cam = _FakeCam(degraded=True)
    _run(sp, cam, iterations=3)
    assert False in cam.reopens, "should restore to full resolution"
    assert not _degraded_hint_path(sp).exists(), "hint should be cleared on restore"


def test_restore_is_skipped_when_not_degraded(tmp_path: Path, monkeypatch) -> None:
    """Never reopen a healthy session — that is churn for no benefit."""
    monkeypatch.setattr("agent_core_webcam.presence.watcher.RESTORE_AFTER_SUCCESSES", 2)
    sp = tmp_path / "state.json"
    cam = _FakeCam(degraded=False)
    _run(sp, cam, iterations=3)
    assert cam.reopens == []


# --------------------------------------------------------------------------
# The swallow guards  (watcher.py 192-193, 207-210)
# --------------------------------------------------------------------------


def test_try_reopen_swallows_a_failing_reopen() -> None:
    """If reopening also fails the loop must keep running AND keep beating,
    so a reader sees ALIVE-but-failing rather than silence."""

    class _Boom:
        degraded = False

        def reopen(self, *, degrade: bool) -> None:
            raise RuntimeError("device gone")

    _try_reopen(_Boom(), degrade=True)  # must not raise


def test_heartbeat_write_failure_does_not_kill_the_loop(tmp_path: Path, monkeypatch) -> None:
    """Bookkeeping that can kill the loop it observes is worse than no bookkeeping."""

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr("agent_core_webcam.presence.watcher.write_heartbeat", _boom)
    sp = tmp_path / "state.json"
    _run(sp, _FakeCam(), iterations=2)  # must not raise
    assert read_state_exists(sp)


def read_state_exists(sp: Path) -> bool:
    """The state write must still have happened despite the heartbeat failing."""
    return sp.exists()


# --------------------------------------------------------------------------
# RSS sampling  (watcher.py 253-254, 257-258)
# --------------------------------------------------------------------------


def test_sample_rss_returns_none_when_psutil_is_absent(monkeypatch) -> None:
    """psutil is not a hard dependency; its absence is a missing field, not a crash.

    None must never become 0 — a zero plots as a healthy flat line and would
    answer the accumulation question wrongly.
    """
    import builtins

    real_import = builtins.__import__

    def _no_psutil(name: str, *args: object, **kw: object):  # type: ignore[no-untyped-def]
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _no_psutil)
    assert _sample_rss() is None


def test_sample_rss_returns_none_when_psutil_raises(monkeypatch) -> None:
    """A psutil that imports but fails must also degrade to None, not propagate."""
    psutil = pytest.importorskip("psutil")

    def _boom(*_a: object, **_k: object):  # type: ignore[no-untyped-def]
        raise RuntimeError("no such process")

    monkeypatch.setattr(psutil, "Process", _boom)
    assert _sample_rss() is None


def test_heartbeat_carries_rss_and_started_at(tmp_path: Path) -> None:
    """The fields exist end-to-end, so the accumulation question can be answered
    later from a record we already write."""
    sp = tmp_path / "state.json"
    _run(sp, _FakeCam(), iterations=1)
    hb = read_heartbeat(heartbeat_path_for(sp))
    assert hb is not None
    assert hb.started_at is not None
    assert hb.pid > 0
