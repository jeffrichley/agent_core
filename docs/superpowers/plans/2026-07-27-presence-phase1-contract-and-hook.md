# Presence Awareness — Phase 1: Contract + Configurable Trust-Gating Hook

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the reader half of the presence system — a camera-free, safety-additive session hook that reads the presence-state file and injects per-being behavioral guidance (ambient → shoulder-surf → trust-gating), folded into the existing `agent-core-webcam` package.

**Architecture:** The already-written `agent-core-presence` code (`state.py`, `injector.py`, `motion.py` + tests) relocates verbatim into `agent-core-webcam/src/agent_core_webcam/presence/`, and the standalone package is deleted — per the approved spec, and because only inside webcam does this code come under the repo's mypy / coverage / testpaths gates. The injector is then extended from a single fixed line into a **level-driven, template-overridable** guidance selector: a pure `levels.py` policy maps `(presence reading, being's level)` → injected text, where every uncertain input lands on the cautious side and higher levels are strict supersets of lower ones. No camera, no CV, no watcher in this phase — the hook only ever *reads* a file the (future) watcher writes, and degrades every failure to "unknown → be cautious."

**Tech Stack:** Python 3.12, pydantic v2, pluggy entry-points, agent_core hook protocol (`execute(event, hook_input, params) -> ToolResult`), pytest. No new runtime dependencies.

## Phasing (this plan is Phase 1 of 4)

The approved spec (`docs/superpowers/specs/2026-07-27-presence-awareness-design.md`) is the umbrella design. It decomposes into four independently-shippable subsystems, each its own spec→plan→build:

1. **Phase 1 (THIS PLAN) — Presence contract + configurable hook.** Camera-free reader. Safety-additive on deploy: no watcher → always "unknown" → beings default cautious.
2. **Phase 2 — Detection pipeline + watcher (presence without recognition).** Motion gate → YuNet detect → tracking → writes `at_desk` + `unknown_count`. Enables level-2 shoulder-surf for real.
3. **Phase 3 — Recognition + Bayesian belief + enrollment.** ArcFace, encrypted templates, per-track belief filter. Makes `known=[jeff]` real. Requires live validation with Jeff.
4. **Phase 4 — Snapshot refactor + watcher CLI + live-wire.** Route Pepper's snapshot through the watcher (with standalone fallback), `agent-core-webcam watch` CLI, integration into the live uv-tool env + `agent_core.yaml`.

Phase 1 produces working, tested, deployable software on its own.

## Global Constraints

- **Gate:** `just check` must be green (from the worktree, after its own `uv sync --dev`): `lint` + `typecheck` (mypy) + `contracts` (import-linter) + `test` (full suite, `-n 0`, **85% whole-repo floor**) + `patch-cov` (**≥80% of changed lines**, vs `origin/main`).
- **`just lint` does NOT cover webcam.** Lint the touched package explicitly: `uv run --no-sync ruff check packages/agent-core-webcam`. Keep `ruff format` clean: `uv run --no-sync ruff format packages/agent-core-webcam`.
- **mypy** runs over `packages/agent-core-webcam/src` (already in `[tool.mypy].files`). agent-core-webcam is on the *lighter* flag set (NOT the discord `--strict` override): `check_untyped_defs`, `no_implicit_optional`, `warn_unused_ignores`. Still: fully type every new symbol.
- **Docstrings:** Google-style, matching the existing webcam/presence modules (every public module, class, function).
- **Commits:** conventional-commit lowercase subject (`feat:`, `refactor:`, `test:`, `chore:`). **NO `Co-Authored-By` trailer** (match agent_core convention — verify with `git log -3 --format='%B'`).
- **No new runtime deps.** The hook's import path (`injector` → `state` → `levels`) must never import `cv2`, `numpy`, `fastmcp`, or any camera code — it loads in every being's session on every turn and must stay instant. (`motion.py` imports numpy but nothing in the hook path imports `motion`.)
- **Safety invariant (load-bearing):** the injected text may only ever *add* caution. Every failure/uncertainty path resolves to "unknown → be cautious." No path makes a being less careful. A test must lock each degradation.

---

## File Structure

**Created (under `packages/agent-core-webcam/`):**
- `src/agent_core_webcam/presence/__init__.py` — subpackage marker + public exports.
- `src/agent_core_webcam/presence/state.py` — **moved verbatim** from `agent-core-presence` (the `PresenceState` contract + atomic `write_state`/`read_state`).
- `src/agent_core_webcam/presence/motion.py` — **moved verbatim** (Tier-0 motion gate; Phase-2 fuel, relocated now so the standalone package dies cleanly).
- `src/agent_core_webcam/presence/levels.py` — **NEW.** `PresenceReading`, `DEFAULT_TEMPLATES`, `classify()`, `render()` — the pure level→text policy.
- `src/agent_core_webcam/presence/injector.py` — **moved + extended** `PresenceInjector` (level + templates + principal + never-raise).
- `tests/presence/__init__.py`
- `tests/presence/test_state.py` — moved verbatim.
- `tests/presence/test_motion.py` — moved verbatim.
- `tests/presence/test_levels.py` — NEW.
- `tests/presence/test_injector.py` — moved + extended.

**Modified:**
- `src/agent_core_webcam/plugin.py` — add a `register_hook_tool_types` hookimpl exposing `builtin.presence_injector` (webcam's entry-point `webcam_aliases` already points at this module).
- `packages/agent-core-webcam/pyproject.toml` — no new deps needed (pydantic already present). Add nothing unless mypy/import-linter demands.

**Deleted:**
- `packages/agent-core-presence/` — the entire standalone package (its `pyproject.toml` entry-point `presence_aliases`, `src/`, `tests/`, `.venv-presence/`, `.presence-smoke/`).

---

## Task 0: Worktree setup + baseline green

**Files:** none (environment only).

The worktree currently has only an isolated `.venv-presence`; `just` needs a proper repo `.venv`.

- [ ] **Step 1: Sync the worktree's own environment**

```bash
cd E:/workspaces/ai/agents/agent_core/.worktrees/presence-injector
uv sync --dev
```
Expected: creates `.venv`, resolves the full workspace.

- [ ] **Step 2: Install this worktree's git hooks**

```bash
uv run --no-sync python -m agent_core.githooks
```
Expected: hooks installed (pre-push runs `just check`).

- [ ] **Step 3: Confirm the presence tests currently pass in their OLD home (baseline)**

```bash
uv run --no-sync pytest packages/agent-core-presence/tests --no-cov -q
```
Expected: PASS (13 tests: 4 state + 4 injector + 5 motion). This is the green we must preserve across the relocate.

- [ ] **Step 4: Confirm the webcam suite is green (we're about to add to it)**

```bash
uv run --no-sync pytest packages/agent-core-webcam/tests --no-cov -q
```
Expected: PASS.

No commit for this task.

---

## Task 1: Relocate `agent-core-presence` into `agent-core-webcam`

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/{__init__.py,state.py,motion.py,injector.py}`
- Create: `packages/agent-core-webcam/tests/presence/{__init__.py,test_state.py,test_motion.py,test_injector.py}`
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/plugin.py`
- Delete: `packages/agent-core-presence/` (whole directory)

**Interfaces:**
- Produces: `agent_core_webcam.presence.state.PresenceState` / `read_state` / `write_state`; `agent_core_webcam.presence.injector.PresenceInjector`; `agent_core_webcam.presence.motion.MotionGate`. Later tasks + phases import from these paths.
- The hookimpl `register_hook_tool_types() -> {"builtin.presence_injector": PresenceInjector}` on `agent_core_webcam.plugin`.

This task is mechanical but must end green: move files, rewrite the `agent_core_presence` import prefix to `agent_core_webcam.presence`, register the hook in webcam's plugin, delete the old package, prove the moved tests pass in their new home.

- [ ] **Step 1: Create the presence subpackage and move the three source modules**

```bash
cd E:/workspaces/ai/agents/agent_core/.worktrees/presence-injector
mkdir -p packages/agent-core-webcam/src/agent_core_webcam/presence
git mv packages/agent-core-presence/src/agent_core_presence/state.py   packages/agent-core-webcam/src/agent_core_webcam/presence/state.py
git mv packages/agent-core-presence/src/agent_core_presence/motion.py  packages/agent-core-webcam/src/agent_core_webcam/presence/motion.py
git mv packages/agent-core-presence/src/agent_core_presence/injector.py packages/agent-core-webcam/src/agent_core_webcam/presence/injector.py
```

- [ ] **Step 2: Write the subpackage `__init__.py`**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/__init__.py`:

```python
"""Presence framework — the reader half of the camera-derived presence signal.

``state`` is the on-disk contract (written by the future CV watcher, read by
the hook). ``injector`` is the in-session hook that turns a reading into
per-being behavioral guidance. ``motion`` is the Tier-0 motion gate (fuel for
the Phase-2 watcher). ``levels`` is the pure text-selection policy.

Nothing in the hook's import path (``injector`` -> ``state`` -> ``levels``)
imports ``cv2`` or ``numpy`` — the hook loads every turn and must stay instant.
"""

from __future__ import annotations

from agent_core_webcam.presence.injector import PresenceInjector
from agent_core_webcam.presence.state import PresenceState, read_state, write_state

__all__ = ["PresenceInjector", "PresenceState", "read_state", "write_state"]
```

- [ ] **Step 3: Fix the import prefix in `injector.py`**

In `packages/agent-core-webcam/src/agent_core_webcam/presence/injector.py`, change the state import:

```python
# from:
from agent_core_presence.state import PresenceState, read_state
# to:
from agent_core_webcam.presence.state import PresenceState, read_state
```
(Leave the rest of `injector.py` unchanged in this task — the level/template extension is Task 4. `state.py` and `motion.py` have no cross-package imports, so they move unedited.)

- [ ] **Step 4: Move the tests and fix their import prefixes**

```bash
mkdir -p packages/agent-core-webcam/tests/presence
git mv packages/agent-core-presence/tests/test_state.py    packages/agent-core-webcam/tests/presence/test_state.py
git mv packages/agent-core-presence/tests/test_motion.py   packages/agent-core-webcam/tests/presence/test_motion.py
git mv packages/agent-core-presence/tests/test_injector.py packages/agent-core-webcam/tests/presence/test_injector.py
```

Create `packages/agent-core-webcam/tests/presence/__init__.py` (empty file).

In all three moved test files, replace every `from agent_core_presence.` with `from agent_core_webcam.presence.` (e.g. `from agent_core_presence.motion import MotionGate` → `from agent_core_webcam.presence.motion import MotionGate`).

- [ ] **Step 5: Register the hook in webcam's plugin**

In `packages/agent-core-webcam/src/agent_core_webcam/plugin.py`, add this hookimpl (alongside the existing endpoint hookimpls; the module is already webcam's `agent_core` entry-point):

```python
@hookimpl
def register_hook_tool_types() -> dict[str, type[Any]]:
    """Register ``builtin.presence_injector`` as an agent_core hook-tool type."""
    from agent_core_webcam.presence.injector import PresenceInjector

    return {"builtin.presence_injector": PresenceInjector}
```
(`Any` and `hookimpl` are already imported in that file.)

- [ ] **Step 6: Delete the standalone package**

```bash
git rm -r packages/agent-core-presence/src packages/agent-core-presence/tests packages/agent-core-presence/pyproject.toml
rm -rf packages/agent-core-presence
```
(The `git rm` handles tracked files; the `rm -rf` clears the untracked `.venv-presence/` and `.presence-smoke/` scratch. Verify nothing tracked remains: `git status --porcelain packages/agent-core-presence`.)

- [ ] **Step 7: Re-sync so the removed package leaves the workspace and run the moved tests**

```bash
uv sync --dev
uv run --no-sync pytest packages/agent-core-webcam/tests/presence --no-cov -q
```
Expected: PASS (the same 13 tests, now under webcam). If `uv sync` errors on the removed workspace member, confirm `packages/agent-core-presence` is gone and re-run.

- [ ] **Step 8: Prove the hook type resolves via the entry-point**

```bash
uv run --no-sync python -c "from agent_core_webcam.plugin import register_hook_tool_types; d = register_hook_tool_types(); print(sorted(d)); assert 'builtin.presence_injector' in d"
```
Expected: prints a list containing `builtin.presence_injector`.

- [ ] **Step 9: Commit**

```bash
git add packages/agent-core-webcam packages/agent-core-presence
git commit -m "refactor(presence): fold agent-core-presence into agent-core-webcam"
```

---

## Task 2: `levels.py` — classify a reading into policy inputs

**Files:**
- Create: `packages/agent-core-webcam/src/agent_core_webcam/presence/levels.py`
- Test: `packages/agent-core-webcam/tests/presence/test_levels.py`

**Interfaces:**
- Produces: `PresenceReading` (frozen dataclass: `have_reading: bool`, `principal_present: bool`, `unknown_present: bool`) and `classify(state: PresenceState | None, *, principal: str) -> PresenceReading`. Consumed by `render()` (Task 3) and the injector (Task 4).

- [ ] **Step 1: Write the failing test**

Create `packages/agent-core-webcam/tests/presence/test_levels.py`:

```python
"""Tests for the pure presence->guidance policy (no camera, no I/O)."""

from __future__ import annotations

from agent_core_webcam.presence.levels import classify
from agent_core_webcam.presence.state import PresenceState


def _state(*, at_desk: bool, known: list[str], unknown_count: int) -> PresenceState:
    return PresenceState(
        updated_at=1000.0, at_desk=at_desk, known=known, unknown_count=unknown_count
    )


def test_none_state_is_maximally_uncertain() -> None:
    """A missing/stale reading (None) => no reading, principal absent, unknown present."""
    r = classify(None, principal="jeff")
    assert r.have_reading is False
    assert r.principal_present is False
    assert r.unknown_present is True  # cautious side: shoulder-surf still fires


def test_principal_present_when_at_desk_and_enrolled() -> None:
    r = classify(_state(at_desk=True, known=["jeff"], unknown_count=0), principal="jeff")
    assert r.have_reading is True
    assert r.principal_present is True
    assert r.unknown_present is False


def test_principal_absent_when_not_at_desk() -> None:
    r = classify(_state(at_desk=False, known=["jeff"], unknown_count=0), principal="jeff")
    assert r.principal_present is False


def test_principal_absent_when_not_in_known() -> None:
    r = classify(_state(at_desk=True, known=[], unknown_count=0), principal="jeff")
    assert r.principal_present is False


def test_unknown_present_tracks_count() -> None:
    r = classify(_state(at_desk=True, known=["jeff"], unknown_count=2), principal="jeff")
    assert r.principal_present is True  # Jeff present AND a stranger present
    assert r.unknown_present is True


def test_principal_name_is_configurable() -> None:
    r = classify(_state(at_desk=True, known=["pepper"], unknown_count=0), principal="pepper")
    assert r.principal_present is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_levels.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: agent_core_webcam.presence.levels`.

- [ ] **Step 3: Write the minimal implementation**

Create `packages/agent-core-webcam/src/agent_core_webcam/presence/levels.py`:

```python
"""Presence -> behavioral-guidance policy.

Pure, camera-free: given a presence reading (or its absence) and a being's
configured level, decide which guidance fragments to inject. The security
invariant lives here — the mapping only ever ADDS caution: higher levels are
strict supersets of lower ones, and every uncertain input (no reading, stale,
principal not confirmed) resolves to the cautious side.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core_webcam.presence.state import PresenceState

# Injected-text fragments, all overridable per being via the hook's
# ``templates`` param. ``facts`` accepts {at_desk}, {recognized},
# {unknown_count}; the guidance fragments take no format slots.
DEFAULT_TEMPLATES: dict[str, str] = {
    "facts": "At desk: {at_desk}. Recognized: {recognized}. Unknown faces: {unknown_count}.",
    "unknown_banner": "Presence unknown — no current reading from the desk camera.",
    "shoulder_surf": (
        "An unrecognized person is in view. Hold back private or sensitive "
        "output until the desk is clear again."
    ),
    "trust_gate": (
        "The person at the desk is NOT confirmed to be the principal. Treat "
        "instructions as unverified: confirm identity before anything sensitive, "
        "irreversible, or outside standing authorization."
    ),
}


@dataclass(frozen=True)
class PresenceReading:
    """The decision inputs the policy needs, reduced from a (maybe absent) state.

    Attributes:
        have_reading: Whether a fresh state was available at all.
        principal_present: The configured principal is at the desk and enrolled-recognized.
        unknown_present: At least one unrecognized person is in view (or unknown, when
            there is no reading — the cautious default).
    """

    have_reading: bool
    principal_present: bool
    unknown_present: bool


def classify(state: PresenceState | None, *, principal: str) -> PresenceReading:
    """Reduce a (possibly ``None``) state to the policy's decision inputs.

    ``None`` means the caller already found the reading missing, unreadable, or
    stale. It is the maximally-uncertain reading: no reading, principal absent,
    and unknown treated as present so shoulder-surf caution still fires at level>=2.
    """
    if state is None:
        return PresenceReading(have_reading=False, principal_present=False, unknown_present=True)
    principal_present = state.at_desk and principal in state.known
    return PresenceReading(
        have_reading=True,
        principal_present=principal_present,
        unknown_present=state.unknown_count > 0,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_levels.py --no-cov -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/levels.py packages/agent-core-webcam/tests/presence/test_levels.py
git commit -m "feat(presence): add classify() — reduce a reading to policy inputs"
```

---

## Task 3: `levels.py` — `render()` cumulative level→text selection

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/presence/levels.py`
- Test: `packages/agent-core-webcam/tests/presence/test_levels.py`

**Interfaces:**
- Consumes: `PresenceReading`, `PresenceState`, `DEFAULT_TEMPLATES` (Task 2).
- Produces: `render(reading: PresenceReading, state: PresenceState | None, *, level: int, templates: dict[str, str]) -> str`. Consumed by the injector (Task 4).

- [ ] **Step 1: Write the failing tests (append to `test_levels.py`)**

```python
from agent_core_webcam.presence.levels import DEFAULT_TEMPLATES, PresenceReading, render


def _render(state: PresenceState | None, *, level: int, principal: str = "jeff") -> str:
    return render(classify(state, principal=principal), state, level=level, templates=DEFAULT_TEMPLATES)


def test_level1_is_facts_only_even_with_unknown() -> None:
    """Ambient level never injects guidance, even when a stranger is present."""
    out = _render(_state(at_desk=True, known=["jeff"], unknown_count=3), level=1)
    assert out == "At desk: yes. Recognized: jeff. Unknown faces: 3."
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in out
    assert DEFAULT_TEMPLATES["trust_gate"] not in out


def test_level2_adds_shoulder_surf_only_when_unknown_present() -> None:
    clear = _render(_state(at_desk=True, known=["jeff"], unknown_count=0), level=2)
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in clear
    watched = _render(_state(at_desk=True, known=["jeff"], unknown_count=1), level=2)
    assert DEFAULT_TEMPLATES["shoulder_surf"] in watched
    # Level 2 never trust-gates, even when principal absent.
    assert DEFAULT_TEMPLATES["trust_gate"] not in _render(
        _state(at_desk=False, known=[], unknown_count=1), level=2
    )


def test_level3_trust_gates_when_principal_not_confirmed() -> None:
    absent = _render(_state(at_desk=False, known=[], unknown_count=1), level=3)
    assert DEFAULT_TEMPLATES["trust_gate"] in absent
    assert DEFAULT_TEMPLATES["shoulder_surf"] in absent  # cumulative
    # Principal confirmed and alone => no gating, no shoulder-surf.
    confirmed = _render(_state(at_desk=True, known=["jeff"], unknown_count=0), level=3)
    assert DEFAULT_TEMPLATES["trust_gate"] not in confirmed
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in confirmed


def test_no_reading_uses_unknown_banner_and_gates_at_level3() -> None:
    out = _render(None, level=3)
    assert DEFAULT_TEMPLATES["unknown_banner"] in out
    assert "At desk" not in out  # no facts line when there is no reading
    assert DEFAULT_TEMPLATES["trust_gate"] in out  # uncertainty => cautious


def test_templates_are_overridable() -> None:
    custom = {**DEFAULT_TEMPLATES, "trust_gate": "STRANGER — LOCK DOWN."}
    out = render(
        classify(_state(at_desk=False, known=[], unknown_count=1), principal="jeff"),
        _state(at_desk=False, known=[], unknown_count=1),
        level=3,
        templates=custom,
    )
    assert "STRANGER — LOCK DOWN." in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_levels.py --no-cov -q`
Expected: FAIL — `ImportError: cannot import name 'render'`.

- [ ] **Step 3: Implement `render()` (append to `levels.py`)**

```python
def render(
    reading: PresenceReading,
    state: PresenceState | None,
    *,
    level: int,
    templates: dict[str, str],
) -> str:
    """Select the injected guidance text for a reading at a being's level.

    Levels are cumulative: level 2 adds shoulder-surf caution when an unknown
    is present; level 3 additionally trust-gates whenever the principal is not
    confirmed present. Level comparisons use ``>=`` so any out-of-range high
    value simply yields maximum caution (safe) and any low value yields
    facts-only (ambient) — no clamping needed.
    """
    parts: list[str] = []
    if reading.have_reading and state is not None:
        parts.append(
            templates["facts"].format(
                at_desk="yes" if state.at_desk else "no",
                recognized=", ".join(state.known) if state.known else "nobody enrolled-recognized",
                unknown_count=state.unknown_count,
            )
        )
    else:
        parts.append(templates["unknown_banner"])
    if level >= 2 and reading.unknown_present:
        parts.append(templates["shoulder_surf"])
    if level >= 3 and not reading.principal_present:
        parts.append(templates["trust_gate"])
    return "\n".join(parts)
```

Add `render` to the module `__all__` if one exists; otherwise leave (no `__all__` currently).

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_levels.py --no-cov -q`
Expected: PASS (11 tests total in this file).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/levels.py packages/agent-core-webcam/tests/presence/test_levels.py
git commit -m "feat(presence): add render() — cumulative level->guidance selection"
```

---

## Task 4: Extend `PresenceInjector` — level, templates, principal, never-raise

**Files:**
- Modify: `packages/agent-core-webcam/src/agent_core_webcam/presence/injector.py`
- Test: `packages/agent-core-webcam/tests/presence/test_injector.py`

**Interfaces:**
- Consumes: `read_state` (state), `classify` + `render` + `DEFAULT_TEMPLATES` (levels).
- Produces: `PresenceInjector.execute(event, hook_input, params) -> ToolResult` honoring params `state_path`, `max_age_seconds`, `heading`, `level`, `principal`, `templates`. Registered as `builtin.presence_injector` (Task 1).

The moved `test_injector.py` already covers: fresh reading renders; missing file → unknown; stale → unknown; params override path/age. Those must keep passing (the fresh-render assertion may need updating to the new default `facts` wording — do so in Step 1). New tests add level behavior + never-raise.

- [ ] **Step 1: Update moved tests + write new failing tests**

First, in `packages/agent-core-webcam/tests/presence/test_injector.py`, update any assertion that pins the old fixed output string to the level-1 default (`"At desk: yes. Recognized: jeff. Unknown faces: 0."` is unchanged wording, so most should still pass — run them and adjust only what breaks).

Then append:

```python
import time as _time
from pathlib import Path

from agent_core_webcam.presence.injector import PresenceInjector
from agent_core_webcam.presence.levels import DEFAULT_TEMPLATES
from agent_core_webcam.presence.state import PresenceState, write_state


def _fresh(path: Path, *, at_desk: bool, known: list[str], unknown_count: int) -> None:
    write_state(
        PresenceState(
            updated_at=_time.time(), at_desk=at_desk, known=known, unknown_count=unknown_count
        ),
        path,
    )


def test_level3_injects_trust_gate_when_stranger_only(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=False, known=[], unknown_count=1)
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(p), "level": 3})
    assert DEFAULT_TEMPLATES["trust_gate"] in out.content


def test_level1_never_injects_guidance(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=False, known=[], unknown_count=2)
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(p), "level": 1})
    assert DEFAULT_TEMPLATES["trust_gate"] not in out.content
    assert DEFAULT_TEMPLATES["shoulder_surf"] not in out.content


def test_stale_reading_degrades_to_cautious_at_level3(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    write_state(
        PresenceState(updated_at=1.0, at_desk=True, known=["jeff"], unknown_count=0), p
    )  # ancient
    out = PresenceInjector().execute("SessionStart", {}, {"state_path": str(p), "level": 3})
    assert DEFAULT_TEMPLATES["unknown_banner"] in out.content
    assert DEFAULT_TEMPLATES["trust_gate"] in out.content


def test_custom_principal_and_templates(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=True, known=["pepper"], unknown_count=0)
    out = PresenceInjector().execute(
        "SessionStart",
        {},
        {"state_path": str(p), "level": 3, "principal": "pepper"},
    )
    # Pepper confirmed present => no trust gate.
    assert DEFAULT_TEMPLATES["trust_gate"] not in out.content


def test_execute_never_raises_on_garbage_params(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    _fresh(p, at_desk=True, known=["jeff"], unknown_count=0)
    # A non-numeric max_age would blow up float() — must be swallowed to "unknown".
    out = PresenceInjector().execute(
        "SessionStart", {}, {"state_path": str(p), "max_age_seconds": "not-a-number"}
    )
    assert DEFAULT_TEMPLATES["unknown_banner"] in out.content
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence/test_injector.py --no-cov -q`
Expected: the five new tests FAIL (injector doesn't yet read `level`/`principal`/`templates`, and doesn't yet swallow a bad `max_age_seconds`).

- [ ] **Step 3: Rewrite `injector.py`**

Replace the body of `packages/agent-core-webcam/src/agent_core_webcam/presence/injector.py` with:

```python
"""The ``PresenceInjector`` hook — reader half of the presence contract.

Runs in-session on each lifecycle event. It never computes presence itself
(no camera, no CV — the hook is invoked fresh every turn and must be instant);
it only reads the state file written out-of-band by the CV watcher, and turns
that reading into per-being guidance via the pure ``levels`` policy.

Safety-additive by construction: a missing, unreadable, stale, or malformed
reading — or any internal error — degrades to an explicit "unknown => be
cautious". The hook never blocks and never raises.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_core.models import ToolResult
from agent_core_webcam.presence.levels import DEFAULT_TEMPLATES, classify, render
from agent_core_webcam.presence.state import read_state

log = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path.home() / ".agent-core" / "presence" / "state.json"
_DEFAULT_MAX_AGE_SECONDS = 30.0
_DEFAULT_HEADING = "Presence"
_DEFAULT_LEVEL = 1
_DEFAULT_PRINCIPAL = "jeff"


class PresenceInjector:
    """Inject a staleness-guarded, level-appropriate presence tag into context.

    Params (from the ``params:`` block of the yaml registration):
        state_path (str): Path to the presence-state JSON. Default
            ``~/.agent-core/presence/state.json``.
        max_age_seconds (float): Readings older than this degrade to "unknown".
            Default ``30``.
        heading (str): Section heading for the injected context. Default ``"Presence"``.
        level (int): Behavioral level — 1 ambient, 2 +shoulder-surf, 3 +trust-gating.
            Default ``1``. Cumulative; out-of-range highs just mean max caution.
        principal (str): Enrolled identity that counts as "trusted present".
            Default ``"jeff"``.
        templates (dict): Per-being overrides for any of the ``levels`` text
            fragments (``facts``, ``unknown_banner``, ``shoulder_surf``, ``trust_gate``).
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Return the current presence guidance, degrading any failure to "unknown"."""
        del event, hook_input  # presence depends on neither the event nor the prompt
        heading = str(params.get("heading", _DEFAULT_HEADING))
        templates = {**DEFAULT_TEMPLATES, **(params.get("templates") or {})}
        try:
            state_path = Path(params.get("state_path", _DEFAULT_STATE_PATH))
            max_age = float(params.get("max_age_seconds", _DEFAULT_MAX_AGE_SECONDS))
            level = int(params.get("level", _DEFAULT_LEVEL))
            principal = str(params.get("principal", _DEFAULT_PRINCIPAL))

            state = read_state(state_path)
            if state is not None and (time.time() - state.updated_at) > max_age:
                state = None  # stale => treat as no reading (cautious)
            reading = classify(state, principal=principal)
            content = render(reading, state, level=level, templates=templates)
            return ToolResult(heading=heading, content=content)
        except Exception:  # never raise into the session — degrade to cautious
            log.exception("presence_injector failed; degrading to unknown")
            return ToolResult(heading=heading, content=templates["unknown_banner"])
```

- [ ] **Step 4: Run the full presence suite to verify pass**

Run: `uv run --no-sync pytest packages/agent-core-webcam/tests/presence --no-cov -q`
Expected: PASS (all presence tests: state + motion + levels + injector).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-webcam/src/agent_core_webcam/presence/injector.py packages/agent-core-webcam/tests/presence/test_injector.py
git commit -m "feat(presence): level + templates + principal + never-raise in injector"
```

---

## Task 5: Canon gate green + adversarial self-review

**Files:** none (verification + fixups only).

- [ ] **Step 1: Lint the touched package explicitly (just lint skips webcam)**

```bash
uv run --no-sync ruff check packages/agent-core-webcam
uv run --no-sync ruff format --check packages/agent-core-webcam
```
Expected: clean. Fix with `ruff check --fix` / `ruff format` and amend the relevant commit if needed.

- [ ] **Step 2: Type-check**

```bash
uv run --no-sync mypy
```
Expected: clean over the full `[tool.mypy].files` set (now includes the presence code under webcam). Fix any annotation gaps the fold-in surfaced.

- [ ] **Step 3: Architecture contracts**

```bash
uv run --no-sync lint-imports
```
Expected: clean. If a contract references the deleted `agent_core_presence` module or forbids `agent_core_webcam.presence` importing `agent_core.models`, update `[tool.importlinter]` in the root `pyproject.toml` to reflect the new home and re-run.

- [ ] **Step 4: Full suite + coverage floors (the real gate)**

```bash
just check
```
Expected: green — `lint` + `typecheck` + `contracts` + `test` (85% floor) + `patch-cov` (≥80% of changed lines). If patch-cov flags an uncovered line in `injector.py`'s `except` branch, the `test_execute_never_raises_on_garbage_params` test should already cover it; add a targeted test for any other uncovered changed line rather than lowering the bar.

- [ ] **Step 5: Adversarial self-review (hostile-reviewer pass before any PR)**

Read the whole diff (`git diff origin/main...HEAD`) as a hostile reviewer. Confirm:
- No path makes a being *less* cautious. Trace: no-reading, stale, malformed JSON, bad params, principal-absent, unknown-present — each lands on `unknown_banner` and/or the level-appropriate caution fragment.
- The hook import path (`injector`→`state`,`levels`) imports no `cv2`/`numpy`/`fastmcp`. Verify: `uv run --no-sync python -c "import sys; import agent_core_webcam.presence.injector; assert 'cv2' not in sys.modules and 'numpy' not in sys.modules, sorted(m for m in sys.modules if m in {'cv2','numpy'})"`.
- No dead references to `agent_core_presence` remain: `git grep -n agent_core_presence` returns nothing.
- Docstrings are Google-style on every new public symbol; no `Co-Authored-By` trailer in any commit (`git log origin/main..HEAD --format='%B' | grep -i co-authored` returns nothing).

- [ ] **Step 6: Final commit if any fixups**

```bash
git add -A
git commit -m "chore(presence): lint/type/contract fixups for phase-1 gate"
```
(Skip if Steps 1–4 were already clean.)

---

## Definition of Done (Phase 1)

- `just check` green from the worktree.
- `agent-core-presence` standalone package fully removed; all logic + tests live under `agent-core-webcam/presence/`.
- `builtin.presence_injector` resolves via webcam's entry-point.
- The hook injects level-appropriate guidance (1 ambient / 2 shoulder-surf / 3 trust-gating), per-being template-overridable, and degrades every failure to "unknown → be cautious" — locked by tests.
- **Not in scope (later phases):** the CV watcher, detection, recognition, enrollment, snapshot refactor, and the live-wire into the uv-tool env + `agent_core.yaml`. Phase 1 ships safe (always-cautious) until a watcher exists to write real readings.
