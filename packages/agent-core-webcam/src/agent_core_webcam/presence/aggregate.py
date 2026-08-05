"""Pure faces->state mapping for the presence watcher.

Turns one frame's per-face recognition results into a :class:`PresenceState`,
per the locked design: the LARGEST face (closest to the desk cam) decides
``at_desk``; the principal counts as ``known`` if recognized anywhere; everyone
else (strangers + low-confidence) is counted in ``unknown_count``. Pure and
fully unit-testable — no camera, no model, no clock (``now`` is passed in).
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_core_webcam.presence.recognition import UNKNOWN
from agent_core_webcam.presence.state import PresenceState

Bbox = tuple[int, int, int, int]


def bbox_area(bbox: Bbox) -> int:
    """Area of an ``(x1, y1, x2, y2)`` box (0 if degenerate)."""
    x1, y1, x2, y2 = bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


def aggregate(
    faces: Sequence[tuple[str, Bbox]],
    *,
    principal: str,
    source: str,
    now: float,
) -> PresenceState:
    """Reduce one frame's ``(verdict, bbox)`` faces to a :class:`PresenceState`.

    ``verdict`` is already resolved to an enrolled name or ``"unknown"`` by the
    caller (:func:`identify`). ``at_desk`` is whether the largest face is the
    principal; ``known`` lists EVERY enrolled person recognized in the frame,
    sorted; only genuinely unidentified faces increment ``unknown_count``.

    ``known`` is not principal-only: with several people enrolled, "Brandon is
    also in shot" is exactly the fact the caller needs, and collapsing it into
    ``unknown_count`` would throw away the identification we just did — and read
    as a stranger, which is a different and more alarming claim.
    """
    if not faces:
        return PresenceState(
            updated_at=now, at_desk=False, known=[], unknown_count=0, source=source
        )
    largest_verdict, _ = max(faces, key=lambda vb: bbox_area(vb[1]))
    at_desk = largest_verdict == principal
    known = sorted({verdict for verdict, _ in faces if verdict != UNKNOWN})
    unknown_count = sum(1 for verdict, _ in faces if verdict == UNKNOWN)
    return PresenceState(
        updated_at=now,
        at_desk=at_desk,
        known=known,
        unknown_count=unknown_count,
        source=source,
    )
