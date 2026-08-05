# Presence State Loop (Watcher) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hand-started watcher that reads the camera on a cadence, recognizes each frame's faces, maps them to the presence-state contract, and atomically writes `state.json` — feeding the Phase-1 hook so Wren/Pepper know when it isn't just Jeff.

**Architecture:** A pure `aggregate(faces) -> PresenceState` (the locked largest-face/presence mapping), a `run_watch(...)` loop that owns a `CameraSession` (Phase-2) and composes `embed_faces` + `match_embedding` + `aggregate` + `write_state` with per-cycle error isolation, and a `watch` CLI subcommand. All in `agent-core-webcam/presence/`, reusing Phase-1 (`state.py`) and Phase-2 (`recognition.py`, `enrollment.py`, `camera_session.py`).

**Tech Stack:** Python 3.12, numpy, the existing presence modules. No new dependencies.

## Global Constraints

- **Gate:** `just check` green from the worktree (already synced). `lint` skips webcam → lint it explicitly: `uv run --no-sync ruff check packages/agent-core-webcam` + keep `ruff format` clean on touched files. `mypy` covers webcam/src. `test` (full suite) + `patch-cov` (≥80% of changed lines vs `origin/main`).
- **Import isolation:** the watcher/aggregate/CLI may import numpy + the recognition stack (they're not the hook). The Phase-1 hook path (`injector`→`state`,`levels`) must STILL import no `cv2`/`insightface` — unaffected here, but re-verify.
- **Docstrings:** Google-style. **Commits:** conventional-commit lowercase subject, **NO `Co-Authored-By`**.
- **Safety invariant:** no path makes a being less cautious. Every error/uncertainty resolves to "unknown → cautious" (via skipped writes + the Phase-1 staleness guard). Lock it with tests.

---

## File Structure

**Created (under `packages/agent-core-webcam/`):**
- `src/agent_core_webcam/presence/aggregate.py` — `bbox_area`, `aggregate`.
- `src/agent_core_webcam/presence/watcher.py` — `run_watch`.
- `tests/presence/test_aggregate.py` — pure mapping tests.
- `tests/presence/test_watcher.py` — loop tests with fakes.

**Modified:**
- `src/agent_core_webcam/presence/cli.py` — add the `watch` subcommand.
- `tests/presence/test_cli.py` — a `watch` wiring test.

---

## Task 1: The pure aggregator

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/aggregate.py`
- Test: `packages/agent-core-webcam/tests/presence/test_aggregate.py`

**Interfaces:**
- Produces: `bbox_area(bbox) -> int`; `aggregate(faces, *, principal, source, now) -> PresenceState` where `faces: list[tuple[str, tuple[int,int,int,int]]]` is `(verdict, bbox)` per detected face (verdict already threshold-resolved by Phase-2 `match_embedding`). Consumed by the watcher (Task 2).

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-webcam/tests/presence/test_aggregate.py`:

```python
"""Pure faces->state mapping — no camera, no model, no I/O."""

from __future__ import annotations

from agent_core_webcam.presence.aggregate import aggregate, bbox_area

_BIG = (100, 100, 300, 400)  # area 200*300 = 60000
_SMALL = (10, 10, 40, 50)  # area 30*40 = 1200


def test_bbox_area() -> None:
    assert bbox_area(_BIG) == 200 * 300
    assert bbox_area(_SMALL) == 30 * 40


def test_no_faces_is_empty_scene() -> None:
    s = aggregate([], principal="jeff", source="desk-cam", now=1000.0)
    assert s.at_desk is False and s.known == [] and s.unknown_count == 0
    assert s.updated_at == 1000.0 and s.source == "desk-cam"


def test_jeff_alone_at_desk() -> None:
    s = aggregate([("jeff", _BIG)], principal="jeff", source="desk-cam", now=1.0)
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 0


def test_jeff_at_desk_plus_stranger_behind() -> None:
    s = aggregate(
        [("jeff", _BIG), ("unknown", _SMALL)], principal="jeff", source="d", now=1.0
    )
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 1


def test_stranger_at_desk_jeff_away() -> None:
    s = aggregate([("unknown", _BIG)], principal="jeff", source="d", now=1.0)
    assert s.at_desk is False and s.known == [] and s.unknown_count == 1


def test_stranger_at_desk_jeff_small_in_background() -> None:
    # Largest face is the stranger -> not at desk, but Jeff IS seen -> known.
    s = aggregate(
        [("unknown", _BIG), ("jeff", _SMALL)], principal="jeff", source="d", now=1.0
    )
    assert s.at_desk is False  # Jeff isn't the one driving the desk
    assert s.known == ["jeff"]  # but he's present
    assert s.unknown_count == 1


def test_two_strangers() -> None:
    s = aggregate(
        [("unknown", _BIG), ("unknown", _SMALL)], principal="jeff", source="d", now=1.0
    )
    assert s.at_desk is False and s.known == [] and s.unknown_count == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_aggregate.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: ...aggregate`.

- [ ] **Step 3: Write the implementation**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/aggregate.py`:

```python
"""Pure faces->state mapping for the presence watcher.

Turns one frame's per-face recognition results into a :class:`PresenceState`,
per the locked design: the LARGEST face (closest to the desk cam) decides
``at_desk``; the principal counts as ``known`` if recognized anywhere; everyone
else (strangers + low-confidence) is counted in ``unknown_count``. Pure and
fully unit-testable — no camera, no model, no clock (``now`` is passed in).
"""

from __future__ import annotations

from collections.abc import Sequence

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

    ``verdict`` is already threshold-resolved (``principal`` or ``"unknown"``) by
    the caller's :func:`match_embedding`. ``at_desk`` is whether the largest face
    is the principal; ``known`` lists the principal iff seen anywhere; every
    non-principal face increments ``unknown_count``.
    """
    if not faces:
        return PresenceState(
            updated_at=now, at_desk=False, known=[], unknown_count=0, source=source
        )
    largest_verdict, _ = max(faces, key=lambda vb: bbox_area(vb[1]))
    at_desk = largest_verdict == principal
    jeff_seen = any(verdict == principal for verdict, _ in faces)
    known = [principal] if jeff_seen else []
    unknown_count = sum(1 for verdict, _ in faces if verdict != principal)
    return PresenceState(
        updated_at=now,
        at_desk=at_desk,
        known=known,
        unknown_count=unknown_count,
        source=source,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_aggregate.py --no-cov -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/aggregate.py packages/agent-core-webcam/tests/presence/test_aggregate.py
git commit -m "feat(presence): pure faces->state aggregator (largest-face desk mapping)"
```

---

## Task 2: The watcher loop

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/watcher.py`
- Test: `packages/agent-core-webcam/tests/presence/test_watcher.py`

**Interfaces:**
- Consumes: `CameraSession` (via an injectable `session_factory`), `embed_faces` + `match_embedding` (recognition), `aggregate` (Task 1), `write_state` (Phase 1), `Template` (enrollment).
- Produces: `run_watch(*, template, state_path, principal, threshold, interval, source, camera_index, iterations, session_factory, analyzer_factory, embed_faces_fn, sleep_fn, clock) -> None`. The injectable seams (all defaulted to the real implementations) make it testable with no camera/model; `iterations=None` runs forever, an int runs exactly N cycles.

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-webcam/tests/presence/test_watcher.py`:

```python
"""Watcher loop — driven by fakes, no camera and no model."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from agent_core_webcam.presence.enrollment import Template
from agent_core_webcam.presence.state import read_state
from agent_core_webcam.presence.watcher import run_watch

_JEFF = np.array([1.0, 0.0, 0.0], dtype=np.float32)
_STRANGER = np.array([0.0, 1.0, 0.0], dtype=np.float32)
_BIG = (100, 100, 300, 400)
_SMALL = (10, 10, 40, 50)


class _FakeSession:
    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def warmup(self, n: int = 3) -> None:
        return None

    def read_bgr(self) -> np.ndarray:
        return np.zeros((2, 2, 3), np.uint8)


def _template() -> Template:
    return Template(name="jeff", embeddings=[_JEFF])


def _run(tmp_path: Path, frames: list, iterations: int) -> Path:
    """Run the watcher `iterations` cycles; `frames[i]` is embed_faces output for cycle i."""
    state_path = tmp_path / "state.json"
    calls = {"i": 0}

    def fake_embed(_analyzer, _frame):
        out = frames[min(calls["i"], len(frames) - 1)]
        calls["i"] += 1
        return out

    run_watch(
        template=_template(),
        state_path=state_path,
        principal="jeff",
        threshold=0.5,
        interval=0.0,
        source="test-cam",
        camera_index=0,
        iterations=iterations,
        session_factory=lambda _idx: _FakeSession(),
        analyzer_factory=lambda: object(),
        embed_faces_fn=fake_embed,
        sleep_fn=lambda _s: None,
        clock=lambda: 1234.0,
    )
    return state_path


def test_jeff_alone_writes_trusted_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # one detected face: Jeff (matches template exactly), large bbox
    path = _run(tmp_path, frames=[[(_JEFF, _BIG, 0.9)]], iterations=1)
    s = read_state(path)
    assert s is not None
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 0
    assert s.updated_at == 1234.0 and s.source == "test-cam"


def test_stranger_present_raises_unknown_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = _run(tmp_path, frames=[[(_JEFF, _BIG, 0.9), (_STRANGER, _SMALL, 0.9)]], iterations=1)
    s = read_state(path)
    assert s is not None
    assert s.at_desk is True and s.known == ["jeff"] and s.unknown_count == 1


def test_empty_frame_is_nobody(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = _run(tmp_path, frames=[[]], iterations=1)
    s = read_state(path)
    assert s is not None
    assert s.at_desk is False and s.known == [] and s.unknown_count == 0


def test_cycle_error_skips_write_and_continues(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # cycle 0 writes a good Jeff state; cycle 1's embed raises -> write skipped,
    # loop survives; the last good state remains readable.
    state_path = tmp_path / "state.json"
    calls = {"i": 0}

    def flaky_embed(_analyzer, _frame):
        i = calls["i"]
        calls["i"] += 1
        if i == 1:
            raise RuntimeError("transient frame failure")
        return [(_JEFF, _BIG, 0.9)]

    run_watch(
        template=_template(),
        state_path=state_path,
        principal="jeff",
        threshold=0.5,
        interval=0.0,
        source="test-cam",
        camera_index=0,
        iterations=2,
        session_factory=lambda _idx: _FakeSession(),
        analyzer_factory=lambda: object(),
        embed_faces_fn=flaky_embed,
        sleep_fn=lambda _s: None,
        clock=lambda: 1234.0,
    )
    s = read_state(state_path)
    assert s is not None and s.at_desk is True  # cycle-0 state survived cycle-1 error
    assert calls["i"] == 2  # loop ran both cycles (didn't crash on the error)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_watcher.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: ...watcher`.

- [ ] **Step 3: Write the implementation**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/watcher.py`:

```python
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
    embed_faces as _real_embed_faces,
)
from agent_core_webcam.presence.recognition import (
    load_analyzer as _real_load_analyzer,
)
from agent_core_webcam.presence.recognition import (
    match_embedding,
)
from agent_core_webcam.presence.state import write_state

log = logging.getLogger(__name__)

_EmbedFn = Callable[[object, npt.NDArray[Any]], list[tuple[Any, Bbox, float]]]


def run_watch(
    *,
    template: Template,
    state_path: Path,
    principal: str = "jeff",
    threshold: float = 0.5,
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

    ``iterations=None`` loops until interrupted; an int runs exactly that many
    cycles (used by tests). All heavy collaborators are injectable seams.
    """
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
                    verdict, _score = match_embedding(
                        emb, template.embeddings, principal=principal, threshold=threshold
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_watcher.py --no-cov -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/watcher.py packages/agent-core-webcam/tests/presence/test_watcher.py
git commit -m "feat(presence): watcher loop — recognize->aggregate->write state.json"
```

---

## Task 3: The `watch` CLI subcommand

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/presence/cli.py`
- Test: `packages/agent-core-webcam/tests/presence/test_cli.py`

**Interfaces:**
- Consumes: `run_watch` (Task 2), `load_template` (enrollment).
- Produces: a `watch` subcommand routing to a `_cmd_watch` that loads the template and calls `run_watch`.

- [ ] **Step 1: Write the failing test (append to `test_cli.py`)**

```python
def test_watch_loads_template_and_runs(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    emb = np.array([1.0, 0.0], dtype=np.float32)
    tpath = tmp_path / "jeff.json"
    save_template(Template(name="jeff", embeddings=[emb]), tpath)
    spath = tmp_path / "state.json"

    captured = {}

    def fake_run_watch(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "run_watch", fake_run_watch)
    rc = cli.main(
        [
            "watch",
            "--template", str(tpath),
            "--state-path", str(spath),
            "--interval", "5",
            "--threshold", "0.6",
        ]
    )
    assert rc == 0
    assert captured["template"].name == "jeff"
    assert captured["state_path"] == spath
    assert captured["interval"] == 5.0
    assert captured["threshold"] == 0.6


def test_watch_no_template_errors(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = cli.main(["watch", "--template", str(tmp_path / "missing.json")])
    assert rc != 0
    assert "enroll" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_cli.py -k watch --no-cov -q`
Expected: FAIL — no `watch` subcommand / `run_watch` not imported into `cli`.

- [ ] **Step 3: Wire the subcommand in `cli.py`**

Add the import (top of `cli.py`, with the other presence imports):

```python
from agent_core_webcam.presence.watcher import run_watch
```

Add the command handler (near `_cmd_recognize`):

```python
def _cmd_watch(args: argparse.Namespace) -> int:
    tpath = (
        Path(args.template)
        if args.template
        else DEFAULT_ENROLLMENT_DIR / f"{args.name}.json"
    )
    if not tpath.exists():
        print(f"error: no template at {tpath}. Run `enroll` first.", file=sys.stderr)
        return 2
    template = load_template(tpath)
    state_path = (
        Path(args.state_path)
        if args.state_path
        else Path.home() / ".agent-core" / "presence" / "state.json"
    )
    print(
        f"Watching camera {args.camera_index} every {args.interval}s -> {state_path}\n"
        f"(Ctrl-C to stop.)"
    )
    run_watch(
        template=template,
        state_path=state_path,
        principal=template.name,
        threshold=args.threshold,
        interval=args.interval,
        camera_index=args.camera_index,
    )
    return 0
```

Register the subparser inside `main()` (after the `recognize` subparser):

```python
    w = sub.add_parser("watch", help="continuously write presence state.json")
    w.add_argument("--name", default="jeff")
    w.add_argument("--template", default=None)
    w.add_argument("--state-path", default=None)
    w.add_argument("--threshold", type=float, default=_DEFAULT_THRESHOLD)
    w.add_argument("--interval", type=float, default=2.0)
    w.set_defaults(func=_cmd_watch)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_cli.py --no-cov -q`
Expected: PASS (all cli tests, incl. the 2 new watch tests).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/cli.py packages/agent-core-webcam/tests/presence/test_cli.py
git commit -m "feat(presence): watch CLI subcommand (start the state loop by hand)"
```

---

## Task 4: Gate + import isolation + self-review

**Files:** none (verification + fixups).

- [ ] **Step 1: Lint + format the touched files**

```bash
uv run --no-sync ruff check packages/agent-core-webcam/src/agent_core_webcam/presence packages/agent-core-webcam/tests/presence
uv run --no-sync ruff format --check packages/agent-core-webcam/src/agent_core_webcam/presence packages/agent-core-webcam/tests/presence
```
Expected: clean. Fix with `--fix` / `format` and amend the relevant commit if needed.

- [ ] **Step 2: Type-check**

```bash
uv run --no-sync mypy
```
Expected: clean.

- [ ] **Step 3: Import isolation — the hook still imports no cv2/insightface**

```bash
uv run --no-sync python -c "import sys; import agent_core_webcam.presence.injector; bad=sorted(m for m in sys.modules if m in {'cv2','insightface','onnxruntime'}); print('LEAK:',bad) if bad else print('hook CLEAN')"
```
Expected: `hook CLEAN`.

- [ ] **Step 4: Full gate**

```bash
just check
```
Expected: green. If the run trips the known Windows-local #535 flake (`test_push_notification_arrives_on_real_mcp_session` hang — passes in isolation), that is NOT this branch's failure; confirm the presence suites are green in isolation and note it.

- [ ] **Step 5: Adversarial self-review**

Read `git diff origin/main...HEAD` (this phase's commits) as a hostile reviewer:
- The aggregator matches the spec's mapping table row-for-row (re-check each row).
- The watcher's `except` truly can't crash the loop, and a persistent failure degrades to stale→unknown (no write on error).
- `at_desk` can only be True when the largest face's verdict is the principal (never on a low-confidence/unknown largest face).
- No `Co-Authored-By` in any commit (`git log origin/main..HEAD --format='%B' | grep -i co-authored` → nothing).
- The `watch` CLI's `run_watch` seam is monkeypatchable (Task 3 proves it).

- [ ] **Step 6: Commit any fixups**

```bash
git add -A && git commit -m "chore(presence): state-loop gate fixups"
```
(Skip if clean.)

---

## Task 5: Live validation with Jeff (MANUAL — not pytest)

**Files:** none (a live session with Jeff).

- [ ] **Step 1: Start the watcher (in one terminal)**

```bash
uv run --no-sync python -m agent_core_webcam.presence.cli watch --name jeff --interval 2
```
Expect: `Watching camera 0 every 2.0s -> ...state.json`.

- [ ] **Step 2: Watch the state track reality (in another terminal)**

```bash
# repeatedly print the state file as Jeff sits / leaves / a 2nd person enters
uv run --no-sync python -c "import json,pathlib; print(pathlib.Path.home().joinpath('.agent-core/presence/state.json').read_text())"
```
Confirm: Jeff sitting → `at_desk: true, known: ["jeff"], unknown_count: 0`; Jeff leaves → `at_desk: false`; a second person enters → `unknown_count: 1`. Record what you see.

- [ ] **Step 3: Confirm the hook renders it**

Point the Phase-1 hook at that state file and confirm it injects the level-appropriate guidance for each scene (Jeff present / not-just-Jeff / empty). Report to Jeff.

---

## Definition of Done

- Pure aggregator + watcher loop + `watch` CLI, all green under `just check`; import isolation intact (hook still cv2/insightface-free).
- The watcher writes `state.json` from real recognition; every error/uncertainty path degrades to "unknown → cautious" (locked by tests).
- **Live-validated with Jeff** — the state file tracks who's actually present, and the Phase-1 hook renders the right per-being guidance.
- **Not in scope:** tracking/Bayesian, motion gate, auto-spin/lock/service, template encryption, snapshot refactor, live-wire — all deferred.
