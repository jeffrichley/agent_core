"""Face recognition core for the Phase-2 proof.

Split so the decision logic (cosine, threshold, best-match) is pure and
CI-testable without the heavy model, while the model itself (``insightface``)
is imported lazily and only exercised in model-marked tests and live use.

Nothing here is imported by the Phase-1 hook path — importing this module
pulls in numpy (already present via opencv-python) but NOT insightface.
"""

from __future__ import annotations

from collections.abc import Sequence

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
    return img
