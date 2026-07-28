"""Model-dependent recognition — self-skips when insightface is absent.

Never gates CI: if the `recognition` extra isn't installed, importorskip skips
the whole module. The blank-frame test exercises the real wrapper end-to-end
without any fixtures. The face-comparison tests need checked-in face images and
skip until those exist — recognition on real faces is proven live in Task 6.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("insightface")

from agent_core_webcam.presence.recognition import (
    cosine,
    embed_faces,
    load_analyzer,
)

FIXTURES = Path(__file__).parent / "fixtures"
_HAVE_FIXTURES = (FIXTURES / "face_a.png").exists()


@pytest.fixture(scope="module")
def analyzer():  # type: ignore[no-untyped-def]
    return load_analyzer()


def _load_bgr(name: str) -> np.ndarray:
    import cv2

    img = cv2.imread(str(FIXTURES / name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    return img


def test_analyzer_loads_and_embeds_empty(analyzer) -> None:  # type: ignore[no-untyped-def]
    """The wrapper loads the real model and returns [] on a face-less frame."""
    faces = embed_faces(analyzer, np.zeros((480, 640, 3), dtype=np.uint8))
    assert faces == []


@pytest.mark.skipif(not _HAVE_FIXTURES, reason="face fixtures pending; proven live in Task 6")
def test_detects_a_face(analyzer) -> None:  # type: ignore[no-untyped-def]
    faces = embed_faces(analyzer, _load_bgr("face_a.png"))
    assert len(faces) >= 1


@pytest.mark.skipif(not _HAVE_FIXTURES, reason="face fixtures pending; proven live in Task 6")
def test_same_face_scores_higher_than_a_stranger(analyzer) -> None:  # type: ignore[no-untyped-def]
    a1 = embed_faces(analyzer, _load_bgr("face_a.png"))[0][0]
    a2 = embed_faces(analyzer, _load_bgr("face_a2.png"))[0][0]  # same person, 2nd shot
    b = embed_faces(analyzer, _load_bgr("face_b.png"))[0][0]  # different person
    assert cosine(a1, a2) > cosine(a1, b)
