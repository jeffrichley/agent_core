# Phase 0 — Defect-A `tool.uv.cache-keys` Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agent-core daemon refresh` always install the current
committed code by adding a git-commit cache key to every workspace member,
so the daemon can never silently run stale code (Defect A).

**Architecture:** uv's build cache for a *local/workspace path*
dependency is keyed by `pyproject.toml` mtime, **not** the version string
(verified empirically on uv 0.7.13; uv issue #15224). A source-only edit
therefore does not invalidate the cached wheel, so `uv sync --frozen
--no-editable` (what `run_install` runs) serves stale code. The fix is a
per-member `[tool.uv] cache-keys = [{ file = "pyproject.toml" }, { git =
{ commit = true } }]` block: the `git commit` entry folds the current
HEAD commit into the cache key, forcing a rebuild on every commit. A fast
guard test prevents any member from regressing; a slow integration test
proves the mechanism end-to-end through the real `run_install`.

**Tech Stack:** Python 3.12, uv 0.7.13 (workspace monorepo, `hatchling`),
pytest (`-m 'not slow'` default; `slow` marker for subprocess
integration), `tomllib` (stdlib), git.

**Scope:** This is Phase 0 of the maturity spec
(`docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md`),
shipped as its own standalone PR before Phases 1–4. It touches only:
the 10 member `pyproject.toml` files, two test files, and
`docs/setup/daemon.md`. It does **not** add VCS versioning, CI, or
instance-parameterization (later phases).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `packages/core/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core` |
| `packages/notify/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-notify` |
| `packages/credentials/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-credentials` |
| `packages/agent-core-discord/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-discord` |
| `packages/agent-core-briefs/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-briefs` |
| `packages/agent-core-webcam/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-webcam` |
| `packages/agent-core-channel/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-channel` |
| `packages/agent-core-busproxy/pyproject.toml` | modify (append `[tool.uv]`) | cache-key for `agent-core-busproxy` |
| `packages/agent-core-hatchery/pyproject.toml` | modify (insert `[tool.uv]` before `[tool.uv.sources]`) | cache-key for `agent-core-hatchery` (already has `[tool.uv.sources]`) |
| `packages/agent-core-voice/pyproject.toml` | modify (add key into existing `[tool.uv]`) | cache-key for `agent-core-voice` (already has a `[tool.uv]` table with `conflicts`) |
| `packages/core/tests/test_member_cache_keys_guard.py` | create | fast unit guard: every `packages/*/pyproject.toml` carries the exact cache-keys list |
| `packages/core/tests/test_daemon_install_integration.py` | modify (add one test) | slow regression: source-only change + commit + `run_install` ⇒ fresh code with the key, stale without |
| `docs/setup/daemon.md` | modify | document the fix; state manual `uv cache clean` is no longer required |
| `uv.lock` | conditionally modify | only if `uv lock` rewrites it after the pyproject edits |

The canonical cache-keys block (identical for all 10 members):

```toml
[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
```

---

## Task 1: Guard test + apply cache-keys to all 10 members

**Files:**
- Create: `packages/core/tests/test_member_cache_keys_guard.py`
- Modify: all 10 `packages/*/pyproject.toml` (see File Structure table)
- Modify (conditional): `uv.lock`

- [ ] **Step 1: Write the failing guard test**

Create `packages/core/tests/test_member_cache_keys_guard.py` with exactly:

```python
"""Guard: every workspace member must carry the Defect-A cache key.

Fast unit test (no subprocess). If this fails, a member's
`pyproject.toml` is missing the git-commit cache key and
`daemon refresh` could silently ship stale code for that package.
See docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root

EXPECTED_CACHE_KEYS = [
    {"file": "pyproject.toml"},
    {"git": {"commit": True}},
]


def _member_pyprojects() -> list[Path]:
    root = find_workspace_root(Path(__file__))
    members = sorted((root / "packages").glob("*/pyproject.toml"))
    assert members, f"no member pyproject.toml found under {root / 'packages'}"
    return members


def test_every_member_has_the_defect_a_cache_key() -> None:
    offenders: list[str] = []
    for pp in _member_pyprojects():
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
        cache_keys = data.get("tool", {}).get("uv", {}).get("cache-keys")
        if cache_keys != EXPECTED_CACHE_KEYS:
            offenders.append(f"{pp}: cache-keys={cache_keys!r}")
    assert not offenders, (
        "members missing/incorrect [tool.uv] cache-keys "
        f"(expected {EXPECTED_CACHE_KEYS!r}):\n" + "\n".join(offenders)
    )
```

- [ ] **Step 2: Run the guard test to verify it fails**

Run: `uv run --no-sync pytest packages/core/tests/test_member_cache_keys_guard.py -q`
Expected: FAIL — assertion lists all 10 members with `cache-keys=None`.

- [ ] **Step 3: Append the cache-keys block to the 8 simple members**

For **each** of these 8 files, append exactly one blank line followed by
the canonical block to the **end of the file** (none of them currently
has a `[tool.uv]` table; each currently ends with its
`[tool.hatch.build.targets.wheel]` block):

- `packages/core/pyproject.toml`
- `packages/notify/pyproject.toml`
- `packages/credentials/pyproject.toml`
- `packages/agent-core-discord/pyproject.toml`
- `packages/agent-core-briefs/pyproject.toml`
- `packages/agent-core-webcam/pyproject.toml`
- `packages/agent-core-channel/pyproject.toml`
- `packages/agent-core-busproxy/pyproject.toml`

Block to append (preceded by exactly one blank line):

```toml

[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
```

Concretely, for each file the last two lines are
`[tool.hatch.build.targets.wheel]` then `packages = ["src/<pkg>"]`;
make the edit by replacing that `packages = ["src/<pkg>"]` line with
itself plus the appended block. Example for `packages/core/pyproject.toml`
(old → new):

old:
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/agent_core"]
```
new:
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/agent_core"]

[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
```

Apply the analogous edit to the other 7 files, substituting their
respective `packages = ["src/..."]` line (`src/agent_core_notify`,
`src/agent_core_credentials`, `src/agent_core_discord`,
`src/agent_core_briefs`, `src/agent_core_webcam`,
`src/agent_core_channel`, `src/agent_core_busproxy`).

- [ ] **Step 4: Insert the block into `agent-core-hatchery` (has `[tool.uv.sources]`)**

`packages/agent-core-hatchery/pyproject.toml` has no bare `[tool.uv]`
table but does have `[tool.uv.sources]`. Insert the `[tool.uv]` table
immediately **before** `[tool.uv.sources]` so the parent table precedes
its subtable.

old:
```toml
[tool.uv.sources]
agent-core = { workspace = true }
```
new:
```toml
[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]

[tool.uv.sources]
agent-core = { workspace = true }
```

- [ ] **Step 5: Add the key into `agent-core-voice`'s existing `[tool.uv]` table**

`packages/agent-core-voice/pyproject.toml` already has a `[tool.uv]`
table containing `conflicts`. Add `cache-keys` as the first key inside
that existing table (do **not** create a second `[tool.uv]` header).

old:
```toml
[tool.uv]
conflicts = [
    [{ extra = "cpu" }, { extra = "cu130" }],
]
```
new:
```toml
[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
conflicts = [
    [{ extra = "cpu" }, { extra = "cu130" }],
]
```

- [ ] **Step 6: Run the guard test to verify it passes**

Run: `uv run --no-sync pytest packages/core/tests/test_member_cache_keys_guard.py -q`
Expected: PASS (1 passed).

- [ ] **Step 7: Reconcile `uv.lock`**

`cache-keys` is build-cache config, not a resolution input, so `uv.lock`
should not change — but verify deterministically:

Run: `uv lock`
Then run: `git status --porcelain uv.lock`
- If output is empty → `uv.lock` unchanged; do not stage it.
- If output is non-empty → the edits touched the lock; stage `uv.lock`
  in Step 9's commit alongside the pyprojects.

- [ ] **Step 8: Run the full fast suite to confirm no regression**

Run: `just check`
Expected: PASS (ruff → mypy → lint-imports → `pytest -m 'not slow'` all
green; the new guard test is collected and passes).

- [ ] **Step 9: Commit**

```bash
git add packages/core/tests/test_member_cache_keys_guard.py \
  packages/core/pyproject.toml \
  packages/notify/pyproject.toml \
  packages/credentials/pyproject.toml \
  packages/agent-core-discord/pyproject.toml \
  packages/agent-core-briefs/pyproject.toml \
  packages/agent-core-webcam/pyproject.toml \
  packages/agent-core-channel/pyproject.toml \
  packages/agent-core-busproxy/pyproject.toml \
  packages/agent-core-hatchery/pyproject.toml \
  packages/agent-core-voice/pyproject.toml
# Also `git add uv.lock` ONLY if Step 7 reported it changed.
git commit -m "fix(daemon): add tool.uv.cache-keys git entry to all members (Defect A)

uv keys the local/workspace build cache by pyproject.toml mtime, not the
version string, so a source-only change was not rebuilt by
uv sync --frozen --no-editable and the daemon shipped stale code.
The git-commit cache key forces a rebuild on every commit. Guard test
fails CI if any member regresses."
```

## Task 2: Defect-A regression integration test (slow)

**Files:**
- Modify: `packages/core/tests/test_daemon_install_integration.py`
  (add one parametrized test; reuse the existing module's imports and
  `_uv_available` helper)

- [ ] **Step 1: Add the failing-by-construction regression test**

Append this test to `packages/core/tests/test_daemon_install_integration.py`
(the module already has `from __future__ import annotations`,
`import shutil/subprocess/sys`, `from pathlib import Path`,
`import pytest`, `from agent_core.daemon.install import read_stamp,
run_install`, `pytestmark = pytest.mark.slow`, and a module-level
`_uv_available()`):

```python
def _git(args: list[str], cwd: Path) -> None:
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"


def _write_workspace(ws: Path, *, with_cache_key: bool, sentinel: str) -> None:
    cache_block = (
        '\n[tool.uv]\n'
        'cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]\n'
        if with_cache_key
        else ""
    )
    (ws / "pyproject.toml").write_text(
        f"""
[project]
name = "stale-probe"
version = "0.0.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/stale_probe"]
{cache_block}""",
        encoding="utf-8",
    )
    pkg = ws / "src" / "stale_probe"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        f'SENTINEL = "{sentinel}"\n', encoding="utf-8"
    )


def _installed_sentinel(home: Path) -> str:
    py = (
        home / ".venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else home / ".venv" / "bin" / "python"
    )
    out = subprocess.run(
        [str(py), "-c", "import stale_probe; print(stale_probe.SENTINEL)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.skipif(not _uv_available(), reason="uv not on PATH")
@pytest.mark.parametrize(
    ("with_cache_key", "expected_after_refresh"),
    [
        pytest.param(True, "V2", id="with-cache-key-picks-up-new-code"),
        pytest.param(False, "V1", id="without-cache-key-reproduces-defect-a"),
    ],
)
def test_defect_a_source_only_change_is_picked_up(
    tmp_path: Path, with_cache_key: bool, expected_after_refresh: str
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_workspace(ws, with_cache_key=with_cache_key, sentinel="V1")
    _git(["init"], ws)
    _git(["config", "user.email", "test@example.com"], ws)
    _git(["config", "user.name", "Test"], ws)
    _git(["add", "-A"], ws)
    _git(["commit", "-m", "initial"], ws)

    lock = subprocess.run(
        ["uv", "lock"], cwd=ws, capture_output=True, text=True, check=False
    )
    assert lock.returncode == 0, lock.stderr

    home = tmp_path / "home"
    home.mkdir()
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"

    run_install(home=home, workspace=ws, extra=None, python_version=pyver)
    assert _installed_sentinel(home) == "V1"

    # Source-only change (pyproject.toml NOT touched), then commit.
    (ws / "src" / "stale_probe" / "__init__.py").write_text(
        'SENTINEL = "V2"\n', encoding="utf-8"
    )
    _git(["add", "-A"], ws)
    _git(["commit", "-m", "change sentinel to V2"], ws)

    # The "daemon refresh" re-install.
    run_install(home=home, workspace=ws, extra=None, python_version=pyver)

    assert _installed_sentinel(home) == expected_after_refresh
```

- [ ] **Step 2: Run the regression test (both parameters)**

Run: `uv run --no-sync pytest "packages/core/tests/test_daemon_install_integration.py::test_defect_a_source_only_change_is_picked_up" -v -m slow`
Expected: PASS for **both** params —
`with-cache-key-picks-up-new-code` (proves the fix: refresh serves V2)
and `without-cache-key-reproduces-defect-a` (documents the hazard:
without the key the refresh still serves stale V1 on uv 0.7.13).

> If the `without` case ever fails (asserts V2), uv changed its
> local-cache behavior upstream — that is a signal to revisit whether
> the cache-keys block is still required, not a test bug. Note it and
> escalate; do not delete the assertion silently.

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_daemon_install_integration.py
git commit -m "test(daemon): slow regression for Defect A cache-key fix

Parametrized: with the git cache-key a source-only change + commit is
picked up by run_install (fresh code); without it the stale wheel is
reproduced on uv 0.7.13. Faithful end-to-end reproduction through the
real run_install path."
```

## Task 3: Documentation — retire the manual cache-clean

**Files:**
- Modify: `docs/setup/daemon.md`

- [ ] **Step 1: Annotate the refresh step**

In `docs/setup/daemon.md`, in the "Daily flow" numbered list, replace
the step-2 bullet:

old:
```markdown
2. `daemon install` — re-runs `uv sync --frozen --no-editable --no-dev` against
   the workspace. Uses the extra you specified at install time (stamped in
   `~/.agent-core/.daemon-install-stamp.json`).
```
new:
```markdown
2. `daemon install` — re-runs `uv sync --frozen --no-editable --no-dev` against
   the workspace. Uses the extra you specified at install time (stamped in
   `~/.agent-core/.daemon-install-stamp.json`). Every workspace member
   carries `[tool.uv] cache-keys` with a git-commit entry, so a
   source-only change is rebuilt as long as it is committed — no manual
   `uv cache clean` is needed (see "Why members carry cache-keys").
```

- [ ] **Step 2: Add the explanatory subsection**

In `docs/setup/daemon.md`, immediately after the "## Why this exists"
section (i.e., directly before "## Disk cost"), insert:

```markdown
## Why members carry `cache-keys` (Defect A)

uv's build cache for a *local/workspace path* dependency is keyed by the
`pyproject.toml` mtime, **not** the package version string (the
`(name, version)` key applies only to *published* PyPI wheels; verified
empirically on uv 0.7.13, cf. uv issue #15224). Before the fix, a change
to only `src/*.py` did not invalidate the cached wheel, so
`uv sync --frozen --no-editable` reused stale build output and the daemon
ran old code while truthfully stamping the new git sha. The historical
workaround was a manual surgical `uv cache clean <pkgs>` before every
`daemon refresh`.

The fix: every member `pyproject.toml` carries

    [tool.uv]
    cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]

The `git commit` entry folds the current HEAD commit into the cache key,
forcing a rebuild on every commit. `cache-keys` *replaces* uv's defaults,
so the `pyproject.toml` file entry is kept to preserve
dependency/metadata invalidation. Consequence: **the manual
`uv cache clean` procedure is retired** — `daemon refresh` always
installs the current committed code. (It keys on the *committed* HEAD;
a refresh of uncommitted edits is not seen — the daemon only ever
deploys committed state.) `tests/test_member_cache_keys_guard.py` fails
CI if any member loses the key.
```

- [ ] **Step 3: Update the "Related" list**

In `docs/setup/daemon.md`, replace the first "Related" bullet:

old:
```markdown
- [#79](https://github.com/jeffrichley/agent_core/issues/79) — the issue that motivated this.
```
new:
```markdown
- [#79](https://github.com/jeffrichley/agent_core/issues/79) — the issue that motivated the venv isolation.
- [#93](https://github.com/jeffrichley/agent_core/issues/93) — Defect A (stale daemon install); fixed by the per-member `tool.uv.cache-keys` git entry.
- `docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md` — the maturity spec (Phase 0 = this fix).
```

- [ ] **Step 4: Commit**

```bash
git add docs/setup/daemon.md
git commit -m "docs(daemon): document the Defect-A cache-keys fix; retire manual cache clean"
```

---

## Post-merge (orchestrator actions — NOT subagent plan tasks)

These happen on Jeff's machine after the PR merges; they are not
executable repo tasks and must not be done by a plan-executing subagent:

1. Deploy: on the box, `agent-core daemon refresh`, then **empirically
   verify one last time** per the historical procedure (check the
   installed file timestamp/symbols in
   `~/.agent-core/.venv/Lib/site-packages/...`) to confirm the fix works
   live before trusting it.
2. The orchestrator (not a subagent) updates the private memory
   `project_daemon_refresh_stale_cache_hazard` to mark the manual
   surgical cache-clean **retired as of Phase 0 shipping**.

---

## Self-Review

**1. Spec coverage** (against
`docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md` §4
Phase 0):
- "Add cache-keys to all 10 members" → Task 1 Steps 3–5 (8 + hatchery +
  voice = 10; voice's pre-existing `[tool.uv]` and hatchery's
  `[tool.uv.sources]` handled explicitly).
- "`cache-keys` must include the `pyproject.toml` file entry" → the
  canonical block includes `{ file = "pyproject.toml" }`; guard test
  asserts the *full* expected list, not just the git key.
- "One-time `uv lock`" → Task 1 Step 7 (deterministic reconcile).
- "Guard test parses every member, asserts full list" → Task 1 Step 1.
- "Slow regression: commit → refresh → installed file reflects change" →
  Task 2 (parametrized; also reproduces the bug without the key).
- "Update docs; retire manual procedure" → Task 3.
- "Post-deploy manual verify + memory update" → Post-merge section
  (correctly scoped out of subagent execution).
No gaps.

**2. Placeholder scan:** No TBD/TODO/"handle errors"/"similar to". Every
code block is complete and runnable; every command has an expected
result; the 8 identical edits are fully specified by exact old→new with
the per-file substitution enumerated.

**3. Type/identifier consistency:** `find_workspace_root(Path)` matches
its real signature in `agent_core.daemon.install`. `run_install(home=,
workspace=, extra=, python_version=)` matches the existing integration
test's usage. `EXPECTED_CACHE_KEYS` shape
`[{"file": "pyproject.toml"}, {"git": {"commit": True}}]` is exactly what
`tomllib` produces from the TOML block used in every edit and in the
regression fixture. Helper names (`_git`, `_write_workspace`,
`_installed_sentinel`) are unique within the test module and do not
collide with the existing `_uv_available`.

---

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-05-18-phase0-defect-a-cache-keys.md`.
