"""Face recognition core for the Phase-2 proof.

Split so the decision logic (cosine, threshold, best-match) is pure and
CI-testable without the heavy model, while the model itself (``insightface``)
is imported lazily and only exercised in model-marked tests and live use.

Nothing here is imported by the Phase-1 hook path — importing this module
pulls in numpy (already present via opencv-python) but NOT insightface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.floating]


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity of two vectors; 0.0 if either has zero norm."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def decide(cosine_score: float, *, threshold: float, principal: str) -> str:
    """Map a best-match cosine to a verdict: ``principal`` if >= threshold else 'unknown'."""
    return principal if cosine_score >= threshold else "unknown"


def match_embedding(
    embedding: Vector,
    gallery: Sequence[Vector],
    *,
    principal: str,
    threshold: float,
) -> tuple[str, float]:
    """Best-match ``embedding`` against a gallery; return (verdict, best_cosine).

    An empty gallery yields ('unknown', 0.0).
    """
    if not gallery:
        return "unknown", 0.0
    best = max(cosine(embedding, g) for g in gallery)
    return decide(best, threshold=threshold, principal=principal), best


#: Open-set rejection floors, calibrated 2026-08-03 by leave-one-PERSON-out over
#: 5 enrolled identities (93 faces / 64 images). Holding each person out in turn
#: gives genuine "not in the gallery" observations:
#:
#:                best score                     margin to runner-up
#:   STRANGER     median 0.185  p95 0.267  max 0.312      ~0.05
#:   ENROLLED     median 0.695  p05 0.456  min 0.282      ~0.50
#:
#: Best-score alone CANNOT separate them — the distributions overlap in the tails
#: (stranger reached 0.312, an enrolled person dipped to 0.282). The MARGIN
#: separates by an order of magnitude, because a stranger resembles everyone
#: equally badly while an enrolled person resembles exactly one person
#: distinctively. Hence two gates, with the margin carrying the weight.
#:
#: SCOPE (these numbers are only valid inside it): photo-domain galleries,
#: five identities, one household. Cross-domain (webcam query vs photo gallery)
#: measured 9/10 with margins shrinking ~2.7x — re-derive after enrolling
#: people at the camera. See docs/superpowers/specs/2026-08-03-presence-multiclass-tracks.md
MIN_BEST_SCORE = 0.35
MIN_MARGIN = 0.15

UNKNOWN = "unknown"


def identify(
    embedding: Vector,
    galleries: Mapping[str, Sequence[Vector]],
    *,
    min_best: float = MIN_BEST_SCORE,
    min_margin: float = MIN_MARGIN,
) -> tuple[str, list[tuple[str, float]]]:
    """Identify ``embedding`` against several named galleries.

    Returns ``(verdict, ranked)`` where ``ranked`` is every gallery's best cosine,
    highest first, and ``verdict`` is the winning name or ``UNKNOWN``.

    Unlike a single-gallery threshold, this asks "which of these people is it?"
    rather than "is this above a line" — a comparison that survives the
    photo-vs-webcam domain shift, which moves every class together and so leaves
    the ranking intact even when absolute scores collapse.

    Rejection needs BOTH gates. The runner-up gap is the load-bearing one; see
    ``MIN_MARGIN``. With fewer than two galleries there is no margin to measure,
    so the score gate alone decides.
    """
    ranked = sorted(
        (
            (name, max((cosine(embedding, g) for g in gal), default=0.0))
            for name, gal in galleries.items()
            if gal
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not ranked:
        return UNKNOWN, []
    best_name, best_score = ranked[0]
    if best_score < min_best:
        return UNKNOWN, ranked
    if len(ranked) > 1 and (best_score - ranked[1][1]) < min_margin:
        return UNKNOWN, ranked
    return best_name, ranked


def decode_frame(png_bytes: bytes) -> npt.NDArray[np.uint8]:
    """Decode PNG bytes (as produced by the webcam backend) to a BGR array.

    Returns an ``H x W x 3`` uint8 array in cv2's BGR channel order — exactly
    what ``insightface``'s ``FaceAnalysis.get`` expects.
    """
    import cv2  # local: cv2 is a webcam dep but keep this module's top light

    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode PNG bytes into an image")
    return np.asarray(img, dtype=np.uint8)


#: The only two heads presence needs. ``FaceAnalysis`` otherwise globs every
#: ``.onnx`` in the pack and runs each one per frame — for ``buffalo_s`` that
#: silently adds the 137 MB 3D-landmark model and gender/age estimation to
#: every tick. Measured 2026-08-03 on one 1280x720 frame, CPU:
#:
#:     all modules            rss=+188.4 MB   141.0 ms/frame
#:     detection+recognition  rss=+ 27.0 MB    24.9 ms/frame
#:
#: 5.7x faster, 7x smaller, identical bboxes and embeddings. ``allowed_modules``
#: filters at load (the extra model is constructed then ``del``'d), so the
#: saving is real resident memory, not just skipped work.
_NEEDED_MODULES = ["detection", "recognition"]


def load_analyzer(model_name: str = "buffalo_s") -> object:
    """Load a CPU InsightFace analyzer (SCRFD detect + ArcFace embed).

    Imported lazily so this module stays importable without the heavy stack.
    The model pack downloads to ``~/.insightface/models`` on first use.
    Only the detection and recognition heads are loaded — see
    ``_NEEDED_MODULES``.
    """
    from insightface.app import FaceAnalysis  # type: ignore[import-untyped]

    app = FaceAnalysis(
        name=model_name,
        providers=["CPUExecutionProvider"],
        allowed_modules=_NEEDED_MODULES,
    )
    app.prepare(ctx_id=-1)  # -1 => CPU
    return app


def embed_faces(
    analyzer: object,
    frame: npt.NDArray[np.uint8],
) -> list[tuple[Vector, tuple[int, int, int, int], float]]:
    """Detect + embed every face in a BGR frame.

    Returns one ``(normed_embedding, bbox, det_score)`` per detected face; an
    empty list when none are found. ``bbox`` is ``(x1, y1, x2, y2)`` ints.
    """
    faces = analyzer.get(frame)  # type: ignore[attr-defined]
    out: list[tuple[Vector, tuple[int, int, int, int], float]] = []
    for f in faces:
        emb = np.asarray(f.normed_embedding, dtype=np.float32)
        x1, y1, x2, y2 = (int(v) for v in f.bbox[:4])
        out.append((emb, (x1, y1, x2, y2), float(f.det_score)))
    return out
