# Presence Awareness — Phase 2: Recognition Proof — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove ArcFace recognizes Jeff on his real camera — enroll him, then detect + recognize a face in a single frame via a one-shot CLI — with the least machinery possible (no watcher, no state file, no tracking, no Bayesian).

**Architecture:** New `recognition.py` (pure cosine/decide/match + a lazy InsightFace `buffalo_s` wrapper), `enrollment.py` (build + persist Jeff's plaintext template), and `cli.py` (`enroll` / `recognize` subcommands) — all under `agent-core-webcam`'s `presence/` subpackage, import-isolated from the Phase-1 hook. Recognition deps are an optional `recognition` extra so the hook install stays tiny.

**Tech Stack:** Python 3.12, InsightFace `buffalo_s` (SCRFD detect + ArcFace embed, one `.get()` call), ONNXRuntime CPU, numpy (already present via opencv-python), cv2 for PNG decode, argparse. Frames come from the existing `OpenCVCameraBackend.capture()`.

## Global Constraints

- **Gate:** `just check` green from the worktree (already `uv sync`'d in Phase 1): `lint` + `typecheck` (mypy) + `contracts` + `test` (85%-if-that's-the-floor; the live gate prints the real number) + `patch-cov` (≥80% of changed lines vs `origin/main`).
- **`just lint` skips webcam** — lint the package explicitly: `uv run --no-sync ruff check packages/agent-core-webcam` and keep `ruff format` clean on the touched files.
- **mypy** covers `packages/agent-core-webcam/src` (lighter flag set, not `--strict`). Fully type every new symbol.
- **Import isolation (load-bearing):** nothing in the Phase-1 hook path (`presence/injector` → `state`, `levels`) may import `insightface`, `onnxruntime`, or `cv2`. `recognition.py`/`enrollment.py`/`cli.py` may import numpy at module top, but **`insightface` must be imported lazily** (inside functions) so importing `recognition.py` never requires the heavy stack. Verify with an import test.
- **CI never depends on the model:** model-dependent tests use `pytest.importorskip("insightface")` and self-skip. Only pure-logic tests gate CI.
- **Docstrings:** Google-style. **Commits:** conventional-commit lowercase subject, **NO `Co-Authored-By`**.
- **Plaintext template is intentional (this slice only):** every template write site carries a `# SECURITY TODO: encrypt at rest before Phase 4 / live-wire / family enrollment`.

---

## File Structure

**Created (under `packages/agent-core-webcam/`):**
- `src/agent_core_webcam/presence/recognition.py` — `cosine`, `decide`, `match_embedding`, `decode_frame`, lazy `load_analyzer`, `embed_faces`.
- `src/agent_core_webcam/presence/enrollment.py` — `Template`, `save_template`, `load_template`, `build_template`.
- `src/agent_core_webcam/presence/cli.py` — argparse `enroll` / `recognize`, `main()`.
- `tests/presence/test_recognition_logic.py` — pure cosine/decide/match (no model).
- `tests/presence/test_enrollment.py` — template round-trip + `decode_frame` (no model).
- `tests/presence/test_recognition_model.py` — model-marked (self-skips), detect/separate on fixtures.
- `tests/presence/fixtures/` — one or two small face images for the model-marked tier (added in Task 3).

**Modified:**
- `packages/agent-core-webcam/pyproject.toml` — add `[project.optional-dependencies] recognition = [...]`.

---

## Task 0: Install spike — go/no-go on the model stack (risk-first)

**Files:**
- Modify: `packages/agent-core-webcam/pyproject.toml`

The whole slice rests on `insightface` + `onnxruntime` installing and running on this Windows host. Prove that before building anything.

- [ ] **Step 1: Add the optional `recognition` extra**

In `packages/agent-core-webcam/pyproject.toml`, after `[project.entry-points."agent_core"]`, add:

```toml
[project.optional-dependencies]
# Heavy CV recognition stack — kept OUT of the base install so the presence
# hook stays tiny. Imported lazily; never pulled in by the hook path.
recognition = [
    "insightface>=0.7",
    "onnxruntime>=1.17",
    "numpy>=1.26",
]
```

- [ ] **Step 2: Install the extra into the worktree venv**

```bash
cd E:/workspaces/ai/agents/agent_core/.worktrees/presence-injector
uv sync --dev --extra recognition
```
Expected: resolves + installs insightface, onnxruntime, and their deps.

**If this fails** (insightface needs C++ build tools, a wheel is unavailable for this Python/OS, etc.): **STOP. Do not work around it silently.** Capture the error and surface to Jeff — the recognition approach may need WSL, a prebuilt wheel, or a raw-ONNX ArcFace fallback. This is the go/no-go gate.

- [ ] **Step 3: Smoke-test `buffalo_s` on one synthetic image**

```bash
uv run --no-sync python - <<'PY'
import numpy as np
from insightface.app import FaceAnalysis
app = FaceAnalysis(name="buffalo_s", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1)  # CPU
# A blank frame has no face — we're only proving the model loads + runs.
faces = app.get(np.zeros((480, 640, 3), dtype=np.uint8))
print("model loaded + ran; faces on blank frame:", len(faces))
PY
```
Expected: first run downloads the `buffalo_s` pack to `~/.insightface/models`, then prints `... faces on blank frame: 0` with no exception. That proves load + inference work on this host.

No commit (env + pyproject only — the pyproject change commits with Task 1).

---

## Task 1: Pure recognition logic — cosine, decide, match

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py`
- Test: `packages/agent-core-webcam/tests/presence/test_recognition_logic.py`
- Also stage: `packages/agent-core-webcam/pyproject.toml` (from Task 0)

**Interfaces:**
- Produces: `cosine(a, b) -> float`; `decide(cosine, threshold) -> str` (`"jeff"`/`"unknown"` — takes the principal name); `match_embedding(embedding, template_embeddings, *, principal, threshold) -> tuple[str, float]`. Consumed by `embed_faces`/CLI (Task 3/4).

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-webcam/tests/presence/test_recognition_logic.py`:

```python
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
    verdict, score = match_embedding(
        np.array([1.0, 0.0]), [], principal="jeff", threshold=0.5
    )
    assert verdict == "unknown"
    assert score == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_recognition_logic.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: ...recognition`.

- [ ] **Step 3: Write the minimal implementation**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py`:

```python
"""Face recognition core for the Phase-2 proof.

Split so the decision logic (cosine, threshold, best-match) is pure and
CI-testable without the heavy model, while the model itself
(``insightface``) is imported lazily and only exercised in model-marked
tests and live use.

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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_recognition_logic.py --no-cov -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/pyproject.toml packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py packages/agent-core-webcam/tests/presence/test_recognition_logic.py
git commit -m "feat(recognition): pure cosine/decide/match core + recognition extra"
```

---

## Task 2: Template persistence + frame decode (no model)

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/enrollment.py`
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py` (add `decode_frame`)
- Test: `packages/agent-core-webcam/tests/presence/test_enrollment.py`

**Interfaces:**
- Produces: `Template` (frozen: `name: str`, `embeddings: list[Vector]`); `save_template(template, path)`; `load_template(path) -> Template`; `decode_frame(png_bytes) -> Vector` (BGR HxWx3 uint8). Consumed by CLI (Task 4) + `embed_faces` (Task 3).

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-webcam/tests/presence/test_enrollment.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_enrollment.py --no-cov -q`
Expected: FAIL — enrollment module / `decode_frame` missing.

- [ ] **Step 3: Add `decode_frame` to `recognition.py`**

Append to `packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py`:

```python
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
```

- [ ] **Step 4: Write `enrollment.py`**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/enrollment.py`:

```python
"""Enroll a principal: build + persist a face template.

SECURITY TODO: templates are biometric data. This Phase-2 spike stores them
PLAINTEXT, local-only. They MUST be encrypted at rest (OS keystore) before
Phase 4 / live-wire / any family enrollment. Do not ship this plaintext path
past the proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.floating]

DEFAULT_ENROLLMENT_DIR = Path.home() / ".agent-core" / "presence" / "enrollment"


@dataclass(frozen=True)
class Template:
    """A principal's enrolled face template: a name + a set of embeddings."""

    name: str
    embeddings: list[Vector]


def save_template(template: Template, path: Path) -> None:
    """Persist a template as plaintext JSON (embeddings as float lists).

    SECURITY TODO: encrypt at rest before this leaves the Phase-2 spike.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": template.name,
        "embeddings": [e.astype(float).tolist() for e in template.embeddings],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_template(path: Path) -> Template:
    """Load a template written by :func:`save_template`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    embeddings = [np.asarray(e, dtype=np.float32) for e in raw["embeddings"]]
    return Template(name=str(raw["name"]), embeddings=embeddings)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_enrollment.py --no-cov -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/enrollment.py packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py packages/agent-core-webcam/tests/presence/test_enrollment.py
git commit -m "feat(recognition): plaintext template persistence + PNG frame decode"
```

---

## Task 3: Model wrapper — lazy analyzer + embed_faces + build_template

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py`
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/presence/enrollment.py`
- Create: `packages/agent-core-webcam/tests/presence/test_recognition_model.py`
- Create: `packages/agent-core-webcam/tests/presence/fixtures/` (one face image + one non-face or a second, different face)

**Interfaces:**
- Produces: `load_analyzer() -> FaceAnalysis` (lazy insightface, CPU); `embed_faces(analyzer, frame) -> list[tuple[Vector, tuple[int,int,int,int], float]]` (normed_embedding, bbox, det_score per face); `build_template(analyzer, frames, name) -> Template`.

- [ ] **Step 1: Write the model-marked failing test**

Create `packages/agent-core-webcam/tests/presence/test_recognition_model.py`:

```python
"""Model-dependent recognition — self-skips when insightface is absent.

Never gates CI: if the `recognition` extra isn't installed, importorskip
skips the whole module. Fixtures are small face images checked into the repo.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("insightface")

from agent_core_webcam.presence.recognition import (  # noqa: E402
    cosine,
    embed_faces,
    load_analyzer,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def analyzer():  # type: ignore[no-untyped-def]
    return load_analyzer()


def _load_bgr(name: str) -> np.ndarray:
    import cv2

    img = cv2.imread(str(FIXTURES / name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    return img


def test_detects_a_face(analyzer) -> None:  # type: ignore[no-untyped-def]
    faces = embed_faces(analyzer, _load_bgr("face_a.png"))
    assert len(faces) >= 1


def test_same_face_scores_higher_than_a_stranger(analyzer) -> None:  # type: ignore[no-untyped-def]
    a1 = embed_faces(analyzer, _load_bgr("face_a.png"))[0][0]
    a2 = embed_faces(analyzer, _load_bgr("face_a2.png"))[0][0]  # same person, 2nd shot
    b = embed_faces(analyzer, _load_bgr("face_b.png"))[0][0]  # different person
    assert cosine(a1, a2) > cosine(a1, b)
```

Add fixture images to `packages/agent-core-webcam/tests/presence/fixtures/`: `face_a.png`, `face_a2.png` (same person, different shot), `face_b.png` (a different person). Use any freely-licensed face photos or two of Jeff's enrollment frames + one of another consenting person; keep them small. If suitable fixtures aren't readily available, mark the two comparison tests `@pytest.mark.skip(reason="fixtures pending")` with a note — the model wrapper is still validated live in Task 6.

- [ ] **Step 2: Run to verify it skips or fails correctly**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_recognition_model.py --no-cov -q`
Expected: with the extra installed (Task 0), FAIL on missing `embed_faces`/`load_analyzer` (or missing fixtures); without the extra, SKIP.

- [ ] **Step 3: Add the model wrapper to `recognition.py`**

Append to `recognition.py`:

```python
def load_analyzer(model_name: str = "buffalo_s") -> object:
    """Load a CPU InsightFace analyzer (SCRFD detect + ArcFace embed).

    Imported lazily so this module stays importable without the heavy stack.
    The model pack downloads to ``~/.insightface/models`` on first use.
    """
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name=model_name, providers=["CPUExecutionProvider"])
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
```

- [ ] **Step 4: Add `build_template` to `enrollment.py`**

Append to `enrollment.py`:

```python
def build_template(
    analyzer: object,
    frames: list[npt.NDArray[np.uint8]],
    *,
    name: str,
) -> Template:
    """Build a template from enrollment frames — the largest face per frame.

    Raises ValueError if no face is found in any frame.
    """
    from agent_core_webcam.presence.recognition import embed_faces

    embeddings: list[Vector] = []
    for frame in frames:
        faces = embed_faces(analyzer, frame)
        if not faces:
            continue
        # Largest detected face (bbox area) — the person being enrolled.
        emb, _bbox, _score = max(faces, key=lambda t: (t[1][2] - t[1][0]) * (t[1][3] - t[1][1]))
        embeddings.append(emb)
    if not embeddings:
        raise ValueError("no face detected in any enrollment frame")
    return Template(name=name, embeddings=embeddings)
```

- [ ] **Step 5: Run the model tests (with the extra installed)**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_recognition_model.py --no-cov -q`
Expected: PASS (or SKIP for comparison tests if fixtures were deferred; `test_detects_a_face` should pass with a real `face_a.png`).

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/recognition.py packages/agent-core-webcam/src/agent_core_webcam/presence/enrollment.py packages/agent-core-webcam/tests/presence/test_recognition_model.py packages/agent-core-webcam/tests/presence/fixtures
git commit -m "feat(recognition): lazy buffalo_s analyzer + embed_faces + build_template"
```

---

## Task 4: The `enroll` / `recognize` CLI

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/cli.py`
- Test: `packages/agent-core-webcam/tests/presence/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`; a `_grab_frame()` seam (returns BGR array) so the CLI is testable with a fake frame source. Run via `python -m agent_core_webcam.presence.cli enroll|recognize`.

- [ ] **Step 1: Write the failing test (CLI wiring, model-free via monkeypatch)**

Create `packages/agent-core-webcam/tests/presence/test_cli.py`:

```python
"""CLI wiring — recognize path exercised with monkeypatched model + frame."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from agent_core_webcam.presence import cli
from agent_core_webcam.presence.enrollment import Template, save_template


def test_recognize_prints_verdict(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    # A template whose only embedding matches our fake face embedding exactly.
    emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    tpath = tmp_path / "jeff.json"
    save_template(Template(name="jeff", embeddings=[emb]), tpath)

    monkeypatch.setattr(cli, "_grab_frame", lambda camera_index: np.zeros((2, 2, 3), np.uint8))
    monkeypatch.setattr(cli, "load_analyzer", lambda: object())
    monkeypatch.setattr(
        cli, "embed_faces", lambda analyzer, frame: [(emb, (1, 2, 3, 4), 0.99)]
    )

    rc = cli.main(["recognize", "--template", str(tpath), "--threshold", "0.5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "jeff" in out
    assert "cosine=1.0" in out or "cosine=1.00" in out


def test_recognize_no_template_errors(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = cli.main(["recognize", "--template", str(tmp_path / "missing.json")])
    assert rc != 0
    assert "enroll" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_cli.py --no-cov -q`
Expected: FAIL — `cli` module missing.

- [ ] **Step 3: Write `cli.py`**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/cli.py`:

```python
"""One-shot enroll / recognize CLI — the Phase-2 proof harness.

    python -m agent_core_webcam.presence.cli enroll  --name jeff --frames 5
    python -m agent_core_webcam.presence.cli recognize --threshold 0.5

No watcher, no state file: `recognize` grabs ONE frame, prints per-face
`verdict | cosine=.. | bbox=..`, and exits. The raw cosine is always printed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import numpy.typing as npt

from agent_core_webcam.presence.enrollment import (
    DEFAULT_ENROLLMENT_DIR,
    build_template,
    load_template,
    save_template,
)
from agent_core_webcam.presence.recognition import (
    decode_frame,
    embed_faces,
    load_analyzer,
    match_embedding,
)

_DEFAULT_THRESHOLD = 0.5


def _grab_frame(camera_index: int) -> npt.NDArray[np.uint8]:
    """Grab a single BGR frame from the webcam backend (seam for tests)."""
    from agent_core_webcam.opencv_backend import OpenCVCameraBackend

    png = OpenCVCameraBackend().capture(camera_index, (1280, 720))
    return decode_frame(png)


def _cmd_enroll(args: argparse.Namespace) -> int:
    analyzer = load_analyzer()
    frames: list[npt.NDArray[np.uint8]] = []
    print(f"Capturing {args.frames} frames — look at the camera, move a little between shots.")
    for i in range(args.frames):
        frames.append(_grab_frame(args.camera_index))
        print(f"  captured {i + 1}/{args.frames}")
        time.sleep(args.interval)
    template = build_template(analyzer, frames, name=args.name)
    out = Path(args.out) if args.out else DEFAULT_ENROLLMENT_DIR / f"{args.name}.json"
    save_template(template, out)  # SECURITY TODO: plaintext — encrypt before live
    print(f"Enrolled {args.name}: {len(template.embeddings)} embedding(s) -> {out}")
    return 0


def _cmd_recognize(args: argparse.Namespace) -> int:
    tpath = (
        Path(args.template)
        if args.template
        else DEFAULT_ENROLLMENT_DIR / f"{args.name}.json"
    )
    if not tpath.exists():
        print(f"error: no template at {tpath}. Run `enroll` first.", file=sys.stderr)
        return 2
    template = load_template(tpath)
    analyzer = load_analyzer()
    frame = _grab_frame(args.camera_index)
    faces = embed_faces(analyzer, frame)
    if not faces:
        print("no face detected")
        return 0
    for emb, bbox, det in faces:
        verdict, score = match_embedding(
            emb, template.embeddings, principal=template.name, threshold=args.threshold
        )
        print(f"{verdict} | cosine={score:.2f} | bbox={bbox} | det={det:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="presence")
    parser.add_argument("--camera-index", type=int, default=0)
    sub = parser.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enroll", help="capture frames and build a template")
    e.add_argument("--name", default="jeff")
    e.add_argument("--frames", type=int, default=5)
    e.add_argument("--interval", type=float, default=0.6)
    e.add_argument("--out", default=None)
    e.set_defaults(func=_cmd_enroll)

    r = sub.add_parser("recognize", help="recognize the face in one frame")
    r.add_argument("--name", default="jeff")
    r.add_argument("--template", default=None)
    r.add_argument("--threshold", type=float, default=_DEFAULT_THRESHOLD)
    r.set_defaults(func=_cmd_recognize)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_cli.py --no-cov -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/cli.py packages/agent-core-webcam/tests/presence/test_cli.py
git commit -m "feat(recognition): one-shot enroll/recognize CLI"
```

---

## Task 5: Canon gate green + import isolation + self-review

**Files:** none (verification + fixups).

- [ ] **Step 1: Lint + format the package explicitly**

```bash
uv run --no-sync ruff check packages/agent-core-webcam/src/agent_core_webcam/presence packages/agent-core-webcam/tests/presence
uv run --no-sync ruff format --check packages/agent-core-webcam/src/agent_core_webcam/presence packages/agent-core-webcam/tests/presence
```
Expected: clean. Fix with `--fix` / `format` and amend the relevant commit if needed.

- [ ] **Step 2: Type-check**

```bash
uv run --no-sync mypy
```
Expected: clean. The `analyzer: object` / `# type: ignore[attr-defined]` on `.get()` keeps mypy happy without stubbing insightface.

- [ ] **Step 3: Import isolation — the hook must not pull in insightface**

```bash
uv run --no-sync python -c "import sys; import agent_core_webcam.presence.injector; assert 'insightface' not in sys.modules and 'onnxruntime' not in sys.modules, sorted(m for m in sys.modules if m in {'insightface','onnxruntime'})"
uv run --no-sync python -c "import sys; import agent_core_webcam.presence.recognition; assert 'insightface' not in sys.modules, 'recognition.py must import insightface lazily'"
```
Expected: both print nothing / exit 0.

- [ ] **Step 4: Full gate**

```bash
just check
```
Expected: green. The model-marked tests either run (extra installed) or skip; only pure-logic tests gate coverage. If `just test`'s full-suite run trips the known Windows-local #535 flake (`test_push_notification_arrives_on_real_mcp_session` hang — passes in isolation), that is NOT this branch's failure; re-confirm the presence + recognition suites are green in isolation and note it.

- [ ] **Step 5: Adversarial self-review**

Read `git diff origin/main...HEAD` as a hostile reviewer:
- Import isolation holds (Step 3 proves it).
- Every template write site carries the `# SECURITY TODO` plaintext note.
- `cosine` handles zero-norm without dividing by zero; `match_embedding` handles the empty gallery.
- No `Co-Authored-By` in any commit (`git log origin/main..HEAD --format='%B' | grep -i co-authored` → nothing).
- The CLI's `_grab_frame` / `load_analyzer` / `embed_faces` seams are monkeypatchable (Task 4 proves the recognize path is testable model-free).

- [ ] **Step 6: Commit any fixups**

```bash
git add -A && git commit -m "chore(recognition): phase-2 gate fixups"
```
(Skip if clean.)

---

## Task 6: Live validation with Jeff (MANUAL — the actual proof, not pytest)

**Files:** none (a live session with Jeff).

This is the keystone. Recognition is **not** claimed working until this passes.

- [ ] **Step 1: Enroll Jeff**

```bash
uv run --no-sync python -m agent_core_webcam.presence.cli enroll --name jeff --frames 7
```
Jeff looks at the camera, shifting slightly between shots. Expect: `Enrolled jeff: 7 embedding(s) -> ...jeff.json`.

- [ ] **Step 2: Recognize Jeff (positive)**

```bash
uv run --no-sync python -m agent_core_webcam.presence.cli recognize --name jeff
```
Jeff sits in frame. Expect: `jeff | cosine=0.6x–0.8x | bbox=... | det=0.8x`. **Record the cosine.**

- [ ] **Step 3: Non-Jeff / empty (negative)**

Run `recognize` again with Jeff out of frame (empty chair) → expect `no face detected`. If another consenting person sits → expect `unknown | cosine=<low> | ...`. Hold up a photo of Jeff → note whether it passes (documents the v1 spoofability the umbrella spec flagged).

- [ ] **Step 4: Observe jitter (the evidence for Phase 3)**

Run `recognize` ~5–10 times while Jeff sits normally. Record the spread of cosines. A tight, comfortably-above-threshold cluster = per-frame recognition is stable, and **the Bayesian/tracking layer may not be needed** (Phase 3 stays deferred). A wide/threshold-straddling spread = Phase 3 is justified. **Report the numbers to Jeff — this is the decision input the whole re-scope was for.**

- [ ] **Step 5: Report + decide next**

Summarize to Jeff: does ArcFace recognize him (cosine vs threshold), how stable, spoofability note, and a recommendation on whether Phase 3 (tracking/Bayesian) is warranted. That recommendation drives the next phase.

---

## Definition of Done (Phase 2)

- `insightface`/`onnxruntime` install + run on this host (Task 0 go).
- Pure logic (cosine/decide/match, template round-trip, decode) green in CI; model tests self-skip cleanly without the extra.
- `enroll` builds a plaintext template (with the loud encrypt-TODO); `recognize` prints per-face `verdict | cosine | bbox` from one frame.
- Import isolation proven: the Phase-1 hook never pulls in insightface.
- **Live-validated with Jeff** — recognition demonstrably fires on him and not on an empty chair, with recorded cosines and a jitter observation that informs the Phase-3 go/no-go.
- **Not in scope:** watcher, `state.json`, tracking, Bayesian, motion gate, template encryption, live-wire — all deferred per the spec.
