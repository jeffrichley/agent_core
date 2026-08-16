"""The presence watcher — reads the camera on a cadence and writes state.json.

Owns a single long-lived :class:`CameraSession`, and each cycle: reads a frame,
detects+embeds faces, recognizes each against the enrolled template, aggregates
to a :class:`PresenceState`, and atomically writes it. Per-cycle errors are
caught and the write is skipped so the loop never dies on a transient failure.

**2026-08-16 — what the 56-hour outage actually taught.** That per-cycle catch
worked: it swallowed eight consecutive allocation failures and kept going. The
process stopped anyway, for a reason that was **never established**, and nothing
brought it back. Three things changed here as a result, and only the first two
are about this file:

1. A heartbeat is written EVERY cycle regardless of frame outcome, so a reader
   can tell "camera failing" from "process gone". Before this they were the same
   observation, and the outage was invisible for 56 hours because of it.
2. Repeated failures now tear down and reopen the capture, degraded to
   640x360, and that degraded state SURVIVES A RESTART — otherwise supervision
   would restart at full resolution every time and a box that reliably kills
   720p would loop fail-degrade-die-restart forever, looking like progress.
   Full resolution is restored after sustained success.
3. Restarting a stopped watcher is NOT this file's job — see ``supervisor.py``.
   A process cannot supervise its own death.

**On the degrade: it is insurance, not a targeted fix.** The failing allocation
was 2 764 800 bytes (one 720p BGR frame) through OpenCV's HOST allocator, which
invited a memory-starvation story. Measured 2026-08-16 with the suspected
pressure source resident and active: **15.1 GB free of 63.8 GB.** A 2.6 MB
allocation cannot fail with that headroom, so whole-box RAM pressure does not
explain it, and the mechanism behind the eight caught failures is **unknown**
too — not merely the process's death. Quartering the frame is cheap and helps
against any allocation failure; do not record it as aimed at a diagnosed cause.

Every collaborator is an injectable seam (defaulted to the real implementation)
so the loop is fully testable without a camera or the model.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy.typing as npt

from agent_core_webcam.presence.aggregate import Bbox, aggregate
from agent_core_webcam.presence.camera_session import CameraSession
from agent_core_webcam.presence.enrollment import Template
from agent_core_webcam.presence.recognition import (
    MIN_BEST_SCORE,
    MIN_MARGIN,
    identify,
)
from agent_core_webcam.presence.recognition import (
    embed_faces as _real_embed_faces,
)
from agent_core_webcam.presence.recognition import (
    load_analyzer as _real_load_analyzer,
)
from agent_core_webcam.presence.state import (
    WatcherHeartbeat,
    heartbeat_path_for,
    write_heartbeat,
    write_state,
)

log = logging.getLogger(__name__)

_EmbedFn = Callable[[object, npt.NDArray[Any]], list[tuple[Any, Bbox, float]]]

#: Consecutive failed cycles before the camera handle is torn down and
#: reopened. A handle can go bad in a way no amount of re-``read()`` fixes, and
#: the pre-2026-08-16 loop would retry a dead handle forever while looking
#: perfectly healthy from the outside.
REOPEN_AFTER_FAILURES = 5

#: Consecutive successful cycles at degraded resolution before trying full size
#: again. ~10 minutes at the default 2s interval — long enough that a brief
#: recovery does not flap the camera open and closed, short enough that a
#: degraded session is not a permanent one.
RESTORE_AFTER_SUCCESSES = 300


def run_watch(
    *,
    templates: dict[str, Template],
    state_path: Path,
    principal: str = "jeff",
    min_best: float = MIN_BEST_SCORE,
    min_margin: float = MIN_MARGIN,
    interval: float = 2.0,
    source: str = "desk-cam",
    camera_index: int = 0,
    iterations: int | None = None,
    session_factory: Callable[[int], CameraSession] = lambda idx: CameraSession(idx),
    analyzer_factory: Callable[[], object] = _real_load_analyzer,
    embed_faces_fn: _EmbedFn = _real_embed_faces,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> None:
    """Run the presence watch loop, writing ``state_path`` every ~``interval`` s.

    Each face is identified against ALL enrolled templates rather than tested
    against the principal's alone. That is a different question — "which of these
    people is this?" instead of "is this above a line" — and it is the one that
    survives lighting and camera changes, because a domain shift moves every
    gallery's score together and leaves the ranking intact. It also names
    bystanders instead of reporting them as strangers.

    ``iterations=None`` loops until interrupted; an int runs exactly that many
    cycles (used by tests). All heavy collaborators are injectable seams.
    """
    galleries = {name: t.embeddings for name, t in templates.items()}
    analyzer = analyzer_factory()
    hb_path = heartbeat_path_for(state_path)
    pid = os.getpid()
    last_frame_at: float | None = None
    consecutive_failures = 0
    consecutive_successes = 0
    with session_factory(camera_index) as cam:
        # A restart must not have to relearn what the last run already paid to
        # discover. Supervision restarts a stopped watcher at full resolution,
        # so without this the loop would fail its way back down through
        # REOPEN_AFTER_FAILURES cycles on EVERY restart — the degraded state
        # would never survive the restart that supervision exists to provide,
        # and a box that reliably kills 720p would produce an endless
        # fail-degrade-die-restart cycle that looks like progress and is not.
        if _degraded_hint_path(state_path).exists():
            log.info("previous run ended degraded; starting at reduced resolution")
            _try_reopen(cam, degrade=True)
        cam.warmup()
        count = 0
        while iterations is None or count < iterations:
            count += 1
            try:
                frame = cam.read_bgr()
                faces: list[tuple[str, Bbox]] = []
                for emb, bbox, _det in embed_faces_fn(analyzer, frame):
                    verdict, _ranked = identify(
                        emb, galleries, min_best=min_best, min_margin=min_margin
                    )
                    faces.append((verdict, bbox))
                now = clock()
                state = aggregate(faces, principal=principal, source=source, now=now)
                write_state(state, state_path)
                last_frame_at = now
                consecutive_failures = 0
                consecutive_successes += 1
                # Come back up to full resolution once the box has clearly
                # recovered. Without this a degraded session never returns, and
                # the accuracy cost of one bad afternoon becomes permanent and
                # invisible — the reading stays plausible, just quietly coarser
                # forever. Only attempted while actually degraded.
                if consecutive_successes >= RESTORE_AFTER_SUCCESSES and cam.degraded:
                    consecutive_successes = 0
                    _try_reopen(cam, degrade=False)
                    _set_degraded_hint(state_path, degraded=False)
            except Exception:
                # Skip this cycle's write; the loop survives and the staleness
                # guard covers persistent failures. Never crash on a bad frame.
                consecutive_failures += 1
                consecutive_successes = 0
                log.exception(
                    "presence watch cycle failed; skipping write (consecutive=%d)",
                    consecutive_failures,
                )
                if consecutive_failures % REOPEN_AFTER_FAILURES == 0:
                    _try_reopen(cam, degrade=True)
                    _set_degraded_hint(state_path, degraded=True)
            # The heartbeat is written on EVERY path, success or failure, and
            # deliberately outside the try above — it is the one thing that must
            # not depend on the camera working. Its whole purpose is to say "the
            # loop is turning" while the reading is stale, so a reader can tell a
            # failing camera from a dead process. Its own failure is swallowed:
            # never let bookkeeping kill the loop it exists to observe.
            try:
                write_heartbeat(
                    WatcherHeartbeat(
                        beat_at=clock(),
                        last_frame_at=last_frame_at,
                        consecutive_failures=consecutive_failures,
                        pid=pid,
                    ),
                    hb_path,
                )
            except Exception:
                log.exception("heartbeat write failed; loop continues")
            if iterations is None or count < iterations:
                sleep_fn(interval)


def _try_reopen(cam: CameraSession, *, degrade: bool) -> None:
    """Tear down and reopen the capture, degraded or restored to full size.

    Best-effort and never raises: if reopening also fails, the loop keeps
    running and keeps beating, so the reader still sees ALIVE-but-failing
    rather than silence. Swallowing here is deliberate — the caller's next
    cycle will fail again and the failure counter keeps climbing, which is the
    signal we actually want surfaced.
    """
    try:
        cam.reopen(degrade=degrade)
    except Exception:
        log.exception("camera reopen failed; continuing with the existing handle")


def _degraded_hint_path(state_path: Path) -> Path:
    """Marker file recording that the last run ended at reduced resolution."""
    return state_path.with_name("watcher-degraded")


def _set_degraded_hint(state_path: Path, *, degraded: bool) -> None:
    """Create or remove the degraded marker; never raises.

    A hint, deliberately, not authoritative state: if it is wrong the loop
    self-corrects within RESTORE_AFTER_SUCCESSES cycles either way. Bookkeeping
    that could kill the loop it serves would be a worse bug than the one it
    prevents, so every failure here is swallowed.
    """
    path = _degraded_hint_path(state_path)
    try:
        if degraded:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("degraded", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except Exception:
        # Deliberately broader than OSError. An earlier version caught only
        # OSError and a malformed path raised ValueError straight through the
        # guard — the docstring promised "never raises" while the code did, and
        # only a test with a genuinely hostile path found the difference. The
        # loop must survive ANY failure of its own bookkeeping.
        log.exception("could not update degraded hint; loop continues")
