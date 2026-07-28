"""Template save/load + PNG decode — no model, no camera."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from agent_core_webcam.presence.enrollment import (
    Template,
    load_template,
    save_template,
)
from agent_core_webcam.presence.recognition import decode_frame


def test_template_round_trips(tmp_path: Path) -> None:
    t = Template(name="jeff", embeddings=[np.array([0.1, 0.2, 0.3], dtype=np.float32)])
    path = tmp_path / "jeff.json"
    save_template(t, path)
    loaded = load_template(path)
    assert loaded.name == "jeff"
    assert len(loaded.embeddings) == 1
    np.testing.assert_allclose(loaded.embeddings[0], t.embeddings[0], rtol=1e-6)


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    t = Template(name="jeff", embeddings=[np.array([1.0], dtype=np.float32)])
    path = tmp_path / "deep" / "nested" / "jeff.json"
    save_template(t, path)
    assert path.exists()


def test_decode_frame_round_trips_a_png(tmp_path: Path) -> None:
    # A known BGR image -> PNG bytes (as the webcam backend produces) -> decode.
    bgr = np.zeros((4, 6, 3), dtype=np.uint8)
    bgr[0, 0] = (255, 0, 0)  # one blue pixel (BGR)
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    out = decode_frame(buf.tobytes())
    assert out.shape == (4, 6, 3)
    assert out.dtype == np.uint8
    np.testing.assert_array_equal(out, bgr)
