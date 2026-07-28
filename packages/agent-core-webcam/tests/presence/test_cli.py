"""CLI wiring — enroll/recognize exercised with a fake camera session + model."""

from __future__ import annotations

import numpy as np
from agent_core_webcam.presence import cli
from agent_core_webcam.presence.enrollment import Template, save_template


class _FakeSession:
    """Stand-in for CameraSession: a context manager yielding blank frames."""

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def warmup(self, n: int = 3) -> None:
        return None

    def read_bgr(self) -> np.ndarray:
        return np.zeros((2, 2, 3), np.uint8)


def test_recognize_prints_verdict(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    # A template whose only embedding matches our fake face embedding exactly.
    emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    tpath = tmp_path / "jeff.json"
    save_template(Template(name="jeff", embeddings=[emb]), tpath)

    monkeypatch.setattr(cli, "_open_session", lambda _idx: _FakeSession())
    monkeypatch.setattr(cli, "load_analyzer", lambda: object())
    monkeypatch.setattr(cli, "embed_faces", lambda analyzer, frame: [(emb, (1, 2, 3, 4), 0.99)])

    rc = cli.main(["recognize", "--template", str(tpath), "--threshold", "0.5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "jeff" in out
    assert "cosine=1.0" in out or "cosine=1.00" in out


def test_recognize_no_template_errors(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = cli.main(["recognize", "--template", str(tmp_path / "missing.json")])
    assert rc != 0
    assert "enroll" in capsys.readouterr().err.lower()


def test_recognize_no_face_prints_message(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    emb = np.array([1.0, 0.0], dtype=np.float32)
    tpath = tmp_path / "jeff.json"
    save_template(Template(name="jeff", embeddings=[emb]), tpath)
    monkeypatch.setattr(cli, "_open_session", lambda _idx: _FakeSession())
    monkeypatch.setattr(cli, "load_analyzer", lambda: object())
    monkeypatch.setattr(cli, "embed_faces", lambda analyzer, frame: [])

    rc = cli.main(["recognize", "--template", str(tpath)])
    assert rc == 0
    assert "no face" in capsys.readouterr().out.lower()


def test_enroll_counts_down_and_writes_template(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    emb = np.array([1.0, 0.0], dtype=np.float32)
    out = tmp_path / "jeff.json"
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)  # don't actually wait
    monkeypatch.setattr(cli, "_open_session", lambda _idx: _FakeSession())
    monkeypatch.setattr(cli, "load_analyzer", lambda: object())
    monkeypatch.setattr(
        cli,
        "build_template",
        lambda analyzer, frames, name: Template(name=name, embeddings=[emb, emb]),
    )

    rc = cli.main(
        ["enroll", "--name", "jeff", "--frames", "2", "--interval", "3", "--out", str(out)]
    )
    text = capsys.readouterr().out
    assert rc == 0
    assert out.exists()
    assert "3..." in text and "2..." in text and "1..." in text  # countdown shown
    assert "shot 1 of 2" in text
    assert "usable" in text
