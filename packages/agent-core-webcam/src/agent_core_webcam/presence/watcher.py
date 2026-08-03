"""The presence watcher — reads the camera on a cadence and writes state.json.

Owns a single long-lived :class:`CameraSession`, and each cycle: reads a frame,
detects+embeds faces, recognizes each against the enrolled template, aggregates
to a :class:`PresenceState`, and atomically writes it. Per-cycle errors are
caught and the write is skipped so the loop never dies on a transient failure;
if failures persist, the state file simply ages out and the Phase-1 hook's
staleness guard degrades to "unknown" (cautious). v1 is started by hand.

Every collaborator is an injectable seam (defaulted to the real implementation)
so the loop is fully testable without a camera or the model.
"""

from __future__ import annotations

import logging
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
from agent_core_webcam.presence.state import write_state

log = logging.getLogger(__name__)

_EmbedFn = Callable[[object, npt.NDArray[Any]], list[tuple[Any, Bbox, float]]]


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
    with session_factory(camera_index) as cam:
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
                state = aggregate(faces, principal=principal, source=source, now=clock())
                write_state(state, state_path)
            except Exception:
                # Skip this cycle's write; the loop survives and the staleness
                # guard covers persistent failures. Never crash on a bad frame.
                log.exception("presence watch cycle failed; skipping write")
            if iterations is None or count < iterations:
                sleep_fn(interval)
