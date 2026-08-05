"""Pure recognition logic — cosine + decision, no model, no camera."""

from __future__ import annotations

import numpy as np
from agent_core_webcam.presence.recognition import (
    MIN_BEST_SCORE,
    MIN_MARGIN,
    cosine,
    decide,
    identify,
    match_embedding,
)


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


# --- multi-class identification -------------------------------------------
#
# The margin gate is the load-bearing one: a stranger resembles every enrolled
# person about equally (measured ~0.05 gap), while an enrolled person resembles
# exactly one distinctively (~0.50). Best-score alone cannot separate them —
# the distributions overlap in the tails. These lock that behaviour.


def _unit(*xs: float) -> np.ndarray:
    v = np.array(xs, dtype=np.float64)
    return v / np.linalg.norm(v)


def test_identify_picks_the_distinctive_gallery() -> None:
    q = _unit(1.0, 0.0, 0.0)
    verdict, ranked = identify(
        q, {"jeff": [_unit(1.0, 0.05, 0.0)], "cindy": [_unit(0.0, 1.0, 0.0)]}
    )
    assert verdict == "jeff"
    assert ranked[0][0] == "jeff"
    assert ranked[0][1] > ranked[1][1]


def test_identify_rejects_when_best_score_too_low() -> None:
    # Resembles nobody: every gallery is near-orthogonal.
    q = _unit(0.0, 0.0, 1.0)
    verdict, ranked = identify(q, {"jeff": [_unit(1.0, 0.0, 0.0)], "cindy": [_unit(0.0, 1.0, 0.0)]})
    assert verdict == "unknown"
    assert ranked, "ranking is still reported on rejection — the scores are the evidence"


def test_identify_rejects_a_stranger_who_resembles_everyone_equally() -> None:
    """The stranger signature: a decent best score with no runner-up gap.

    This is the case a single threshold CANNOT catch — best score clears any
    plausible floor, but the query is no more like one person than another.
    """
    q = _unit(1.0, 1.0, 0.0)
    verdict, ranked = identify(q, {"jeff": [_unit(1.0, 0.0, 0.0)], "cindy": [_unit(0.0, 1.0, 0.0)]})
    assert ranked[0][1] > MIN_BEST_SCORE, "precondition: the score gate alone would accept"
    assert ranked[0][1] - ranked[1][1] < MIN_MARGIN
    assert verdict == "unknown"


def test_identify_with_one_gallery_uses_score_gate_only() -> None:
    # No runner-up exists, so the margin gate cannot apply.
    q = _unit(1.0, 0.0, 0.0)
    assert identify(q, {"jeff": [_unit(1.0, 0.02, 0.0)]})[0] == "jeff"


def test_identify_no_galleries_is_unknown() -> None:
    assert identify(_unit(1.0, 0.0), {}) == ("unknown", [])


def test_identify_skips_empty_galleries() -> None:
    q = _unit(1.0, 0.0, 0.0)
    verdict, ranked = identify(q, {"jeff": [_unit(1.0, 0.0, 0.0)], "ghost": []})
    assert verdict == "jeff"
    assert [n for n, _ in ranked] == ["jeff"], "an empty gallery must not occupy a rank slot"
