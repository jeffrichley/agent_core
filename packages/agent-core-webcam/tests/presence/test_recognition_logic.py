"""Pure recognition logic — cosine + decision, no model, no camera."""

from __future__ import annotations

import numpy as np
from agent_core_webcam.presence.recognition import cosine, decide, match_embedding


def test_cosine_identical_is_one() -> None:
    v = np.array([1.0, 0.0, 0.0])
    assert cosine(v, v) == 1.0


def test_cosine_orthogonal_is_zero() -> None:
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert abs(cosine(a, b)) < 1e-9


def test_cosine_is_scale_invariant() -> None:
    a = np.array([1.0, 1.0])
    b = np.array([3.0, 3.0])
    assert abs(cosine(a, b) - 1.0) < 1e-9


def test_cosine_zero_norm_is_zero() -> None:
    assert cosine(np.zeros(3), np.array([1.0, 2.0, 3.0])) == 0.0


def test_decide_above_threshold_is_principal() -> None:
    assert decide(0.62, threshold=0.5, principal="jeff") == "jeff"


def test_decide_below_threshold_is_unknown() -> None:
    assert decide(0.40, threshold=0.5, principal="jeff") == "unknown"


def test_decide_exactly_at_threshold_is_principal() -> None:
    assert decide(0.50, threshold=0.5, principal="jeff") == "jeff"


def test_match_embedding_picks_best_of_several() -> None:
    emb = np.array([1.0, 0.0])
    gallery = [np.array([0.0, 1.0]), np.array([0.9, 0.1])]  # 2nd is close
    verdict, score = match_embedding(emb, gallery, principal="jeff", threshold=0.5)
    assert verdict == "jeff"
    assert score > 0.9


def test_match_embedding_empty_gallery_is_unknown() -> None:
    verdict, score = match_embedding(np.array([1.0, 0.0]), [], principal="jeff", threshold=0.5)
    assert verdict == "unknown"
    assert score == 0.0
