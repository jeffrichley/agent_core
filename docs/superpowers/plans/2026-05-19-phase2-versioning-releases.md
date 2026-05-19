# Phase 2 — Versioning & Releases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every build a real git-derived PEP 440 version (surfaced by `daemon status`) and a single aggregated towncrier `CHANGELOG.md`, under one lockstep `vX.Y.Z` tag series.

**Architecture:** Each of the 10 workspace members switches to `uv-dynamic-versioning` (hatchling version source) with `dynamic = ["version"]`; the Phase-0 `[tool.uv] cache-keys` gains `tags = true` so a new tag invalidates the build cache. One root `[tool.towncrier]` config (sections model) aggregates per-package fragments under `changelog.d/<pkg>/` into one root `CHANGELOG.md`. A `just release` recipe builds the changelog + a local annotated tag. The inaugural `v0.1.0` is cut **post-merge on `main`** (documented, not in PR scope).

**Tech Stack:** `uv-dynamic-versioning` (`/ninoseki/uv-dynamic-versioning`), `towncrier` (`/twisted/towncrier`, dev-dep `>=23.11`), `hatchling`, `uv`, `just`, Typer.

**Spec:** `docs/superpowers/specs/2026-05-19-phase2-versioning-releases-design.md` (refines §Phase 2 of the maturity spec).

**Branch / base:** worktree `.claude/worktrees/feat+phase2-versioning-releases`, branch `worktree-feat+phase2-versioning-releases` @ `5c92e3a` (merged Phase 0+1; Phase 1 ruleset `phase1-main-gate` active — the Phase 2 PR must pass it). Do NOT implement on `main`.

**Spec reconciliation (resolved here):** Spec §3.3 implies fragment-consumption happens in the PR; §7 puts `just release` post-merge. **Authoritative decision: the PR does NOT consume fragments.** The PR ships: the versioning switch, the towncrier config, the `changelog.d/<pkg>/` reorg (fragments moved, not consumed), a header-only `CHANGELOG.md`, the `just release` recipe, `daemon status`, tests, and the one-time `uv lock`. The inaugural `towncrier build` + `v0.1.0` annotated tag is the **post-merge** step (tag must sit on the `main` merge commit so `uv-dynamic-versioning`'s `git describe` math is correct). Task 7 reconciles the Phase 2 spec doc to say this unambiguously.

**Repo facts (verified):**
- 10 members, uniform pyproject shape: `[project]` line 2 `name`, line 3 `version = "0.1.0"`; `[build-system] requires = ["hatchling"]` + `build-backend = "hatchling.build"`; `[tool.hatch.build.targets.wheel] packages = ["src/<pkg>"]`; `[tool.uv] cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]`. Member dirs: `core, credentials, notify, agent-core-briefs, agent-core-busproxy, agent-core-channel, agent-core-discord, agent-core-hatchery, agent-core-voice, agent-core-webcam`. **Special:** `agent-core-hatchery` also has `[tool.uv.sources]`; `agent-core-voice` also has `[tool.uv] conflicts`, `[tool.uv.sources]`, `[[tool.uv.index]]` blocks — preserve those; only modify the `cache-keys` line and append the new `[tool.hatch.version]` / `[tool.uv-dynamic-versioning]` tables.
- Phase 0 guard: `packages/core/tests/test_member_cache_keys_guard.py`, `EXPECTED_CACHE_KEYS = [{"file": "pyproject.toml"}, {"git": {"commit": True}}]` (asserts exact equality for every member — MUST update in lockstep).
- Existing fragments (13): `packages/core/changelog.d/{3.changed,4.changed,6.added,7.added,37.added,54.fixed,67.added,69.fixed,70.added}.md` + a `.gitkeep`; `packages/agent-core-discord/changelog.d/{8.added,22.added}.md`; `packages/credentials/changelog.d/5.added.md`; `packages/notify/changelog.d/4.added.md`. Only types `added/changed/fixed` in use.
- Root `pyproject.toml`: virtual workspace root, no `[build-system]`, no `[tool.towncrier]`; `towncrier>=23.11` in `[dependency-groups] dev`. Sections end at `[tool.pytest.ini_options]` (line 80).
- `just` recipes end with `install-hooks`. `daemon status()` is `packages/core/src/agent_core/daemon/cli.py:125-174`; prints `installed sha:` at line 156 inside `if stamp is not None:`; `_daemon_python()` (line 143) returns the daemon venv interpreter.
- Repo: `github.com/jeffrichley/agent_core`. Only 2 git tags exist, both `pepper-cutover-*` (no `v` prefix) → uv-dynamic-versioning's **default** v-prefixed tag pattern already ignores them; we deliberately set NO custom `pattern` (a hand-written Dunamai regex is error-prone) and guard the requirement with an empirical test (Task 3).

---

### Task 1: Add `tags = true` to every member cache-key (lockstep with guard)

**Why first:** the Phase 0 guard test asserts the exact cache-keys list for all 10 members; versioning-from-tags requires `tags = true` (a new tag changes build output, so it must invalidate uv's build cache — context7 `/ninoseki/uv-dynamic-versioning` explicitly recommends `git = { commit = true, tags = true }`).

**Files:** Modify `packages/core/tests/test_member_cache_keys_guard.py`; Modify all 10 `packages/*/pyproject.toml` (the `[tool.uv] cache-keys` line only).

- [ ] **Step 1: Update the guard test's expectation (RED first)**

In `packages/core/tests/test_member_cache_keys_guard.py` change:
```python
EXPECTED_CACHE_KEYS = [
    {"file": "pyproject.toml"},
    {"git": {"commit": True}},
]
```
to:
```python
EXPECTED_CACHE_KEYS = [
    {"file": "pyproject.toml"},
    {"git": {"commit": True, "tags": True}},
]
```

- [ ] **Step 2: Run guard → expect FAIL**

Run: `uv run --no-sync pytest packages/core/tests/test_member_cache_keys_guard.py -v`
Expected: FAIL — every member listed as offender (pyprojects still have the old key).

- [ ] **Step 3: Update all 10 members' cache-keys line**

In each of the 10 `packages/*/pyproject.toml`, replace the single line:
```toml
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
```
with:
```toml
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true, tags = true } }]
```
(Members: core, credentials, notify, agent-core-briefs, agent-core-busproxy, agent-core-channel, agent-core-discord, agent-core-hatchery, agent-core-voice, agent-core-webcam. For `agent-core-voice` the `[tool.uv]` table also has `conflicts = [...]` and there are `[tool.uv.sources]`/`[[tool.uv.index]]` tables, and `agent-core-hatchery` has `[tool.uv.sources]` — change ONLY the `cache-keys` line, leave those intact.)

- [ ] **Step 4: Run guard → expect PASS**

Run: `uv run --no-sync pytest packages/core/tests/test_member_cache_keys_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Fast gate**

Run: `just check`
Expected: green (ruff/mypy/import-linter + fast suite).

- [ ] **Step 6: Commit**

```bash
git add packages/core/tests/test_member_cache_keys_guard.py packages/core/pyproject.toml packages/credentials/pyproject.toml packages/notify/pyproject.toml packages/agent-core-briefs/pyproject.toml packages/agent-core-busproxy/pyproject.toml packages/agent-core-channel/pyproject.toml packages/agent-core-discord/pyproject.toml packages/agent-core-hatchery/pyproject.toml packages/agent-core-voice/pyproject.toml packages/agent-core-webcam/pyproject.toml
git commit -m "feat(versioning): add tags=true to member cache-keys (tag changes build output)"
```

---

### Task 2: Switch all 10 members to `uv-dynamic-versioning`

**Files:** Modify all 10 `packages/*/pyproject.toml`; then run `uv lock`; Test `packages/core/tests/test_dynamic_versioning.py` (new).

- [ ] **Step 1: Write the failing version test**

Create `packages/core/tests/test_dynamic_versioning.py`:

```python
"""Slow: real `uv build` proves git-derived versions. Skipped by default."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _uv() -> bool:
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _make_member(repo: Path) -> Path:
    """A minimal hatchling+uv-dynamic-versioning package inside a git repo."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    pkg = repo / "src" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[build-system]\n'
        'requires = ["hatchling", "uv-dynamic-versioning"]\n'
        'build-backend = "hatchling.build"\n\n'
        '[project]\n'
        'name = "demo"\n'
        'dynamic = ["version"]\n'
        'requires-python = ">=3.12"\n\n'
        '[tool.hatch.version]\n'
        'source = "uv-dynamic-versioning"\n\n'
        '[tool.uv-dynamic-versioning]\n'
        'fallback-version = "0.0.0"\n\n'
        '[tool.hatch.build.targets.wheel]\n'
        'packages = ["src/demo"]\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _built_version(repo: Path, tmp: Path) -> str:
    out = tmp / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    wheel = next(out.glob("demo-*.whl"))
    # demo-<version>-py3-none-any.whl
    return wheel.name.split("-")[1]


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_tagged_commit_yields_exact_version(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    _git(repo, "tag", "-a", "v1.2.3", "-m", "r")
    assert _built_version(repo, tmp_path) == "1.2.3"


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_untagged_commit_has_dev_version_with_sha(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    _git(repo, "tag", "-a", "v1.2.3", "-m", "r")
    (repo / "src" / "demo" / "x.py").write_text("y = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "next")
    v = _built_version(repo, tmp_path)
    assert v != "1.2.3" and "1.2.3" in v and "g" in v  # PEP440 dev/post + sha


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_pepper_cutover_tags_are_ignored(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    _git(repo, "tag", "-a", "pepper-cutover-ready-2026-05-06", "-m", "p")
    # No v* tag → must fall back, NOT derive from the pepper tag.
    v = _built_version(repo, tmp_path)
    assert "pepper" not in v and "2026" not in v


@pytest.mark.skipif(not _uv(), reason="uv not on PATH")
def test_no_git_uses_fallback(tmp_path: Path) -> None:
    repo = _make_member(tmp_path / "r")
    import shutil
    shutil.rmtree(repo / ".git")
    assert _built_version(repo, tmp_path) == "0.0.0"
```

- [ ] **Step 2: Run → expect FAIL/ERROR**

Run: `uv run --no-sync pytest packages/core/tests/test_dynamic_versioning.py -v -m slow`
Expected: FAIL (uv-dynamic-versioning not yet a build dep / behavior unproven). Capture output.

- [ ] **Step 3: Edit all 10 members' pyproject.toml**

For EACH of the 10 `packages/*/pyproject.toml` apply these 4 edits (example shown for `core`; the shape is identical for every member — only the `name`/`packages` differ and are left as-is):

1. `[build-system]` requires:
   - from `requires = ["hatchling"]`
   - to `requires = ["hatchling", "uv-dynamic-versioning"]`
2. `[project]`: delete the line `version = "0.1.0"` and add `dynamic = ["version"]` in its place (same position, line 3).
3. Append two new tables. Place them AFTER the existing `[tool.hatch.build.targets.wheel]` table and BEFORE `[tool.uv]` (for `agent-core-voice`/`agent-core-hatchery`, place them so they do not split the existing `[tool.uv]`/`[tool.uv.sources]`/`[[tool.uv.index]]` tables — appending at end-of-file is acceptable as long as every `[tool.*]` table stays contiguous):
   ```toml
   [tool.hatch.version]
   source = "uv-dynamic-versioning"

   [tool.uv-dynamic-versioning]
   fallback-version = "0.0.0"
   ```
   Do NOT set a custom `pattern` — the tool's default matches `v`-prefixed semver and already ignores the repo's `pepper-cutover-*` tags (Task 2 Step-1 test `test_pepper_cutover_tags_are_ignored` guards this).

- [ ] **Step 4: One-time `uv lock`**

Run: `uv lock`
Then confirm dynamic members carry no `version` in the lock:
Run: `uv run --no-sync python -c "import re,sys; t=open('uv.lock',encoding='utf-8').read(); import tomllib; d=tomllib.loads(t); pk=[p for p in d['package'] if p['name']=='agent-core']; print(pk[0].get('version'))"`
Expected: `None` (the workspace member has no pinned version → no lock thrash).

- [ ] **Step 5: Run the version test → expect PASS**

Run: `uv run --no-sync pytest packages/core/tests/test_dynamic_versioning.py -v -m slow`
Expected: PASS (4 tests). If `uv build` exceeds 5s it is correctly `slow`-marked and runs in the Phase 1 `integration` job, not the fast gate.

- [ ] **Step 6: Fast gate**

Run: `just check`
Expected: green. (The new test is `slow` → deselected here; runs in CI `integration`.)

- [ ] **Step 7: Commit**

```bash
git add packages/core/pyproject.toml packages/credentials/pyproject.toml packages/notify/pyproject.toml packages/agent-core-briefs/pyproject.toml packages/agent-core-busproxy/pyproject.toml packages/agent-core-channel/pyproject.toml packages/agent-core-discord/pyproject.toml packages/agent-core-hatchery/pyproject.toml packages/agent-core-voice/pyproject.toml packages/agent-core-webcam/pyproject.toml uv.lock packages/core/tests/test_dynamic_versioning.py
git commit -m "feat(versioning): VCS-derived versions via uv-dynamic-versioning (all 10 members)"
```

---

### Task 3: `uv.lock` no-thrash regression test

**Files:** Test `packages/core/tests/test_lock_no_thrash.py` (new).

- [ ] **Step 1: Write the test**

Create `packages/core/tests/test_lock_no_thrash.py`:

```python
"""Guard: dynamic-version members must not carry a pinned version in uv.lock,
so source-only commits don't thrash the lockfile (the daemon's --frozen path).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root

WORKSPACE_MEMBERS = {
    "agent-core", "agent-core-credentials", "agent-core-notify",
    "agent-core-briefs", "agent-core-busproxy", "agent-core-channel",
    "agent-core-discord", "agent-core-hatchery", "agent-core-voice",
    "agent-core-webcam",
}


def test_workspace_members_have_no_pinned_version_in_lock() -> None:
    root = find_workspace_root(Path(__file__))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    offenders = [
        p["name"]
        for p in lock["package"]
        if p["name"] in WORKSPACE_MEMBERS and "version" in p
    ]
    assert not offenders, (
        "dynamic-version members must omit `version` in uv.lock "
        f"(lock thrash risk): {offenders}"
    )
```

- [ ] **Step 2: Run → expect PASS** (Task 2 already produced a correct lock)

Run: `uv run --no-sync pytest packages/core/tests/test_lock_no_thrash.py -v`
Expected: PASS. (If it FAILS, a member still pins a version — revisit Task 2 Step 3/4 for that member before continuing.)

- [ ] **Step 3: Fast gate + commit**

Run: `just check` → green.
```bash
git add packages/core/tests/test_lock_no_thrash.py
git commit -m "test(versioning): guard uv.lock has no pinned member versions (no thrash)"
```

---

### Task 4: Root towncrier config + `changelog.d/<pkg>/` reorg + guard

**Files:** Modify root `pyproject.toml`; Create `CHANGELOG.md`; create `changelog.d/<pkg>/.gitkeep` ×10 and `git mv` the 13 existing fragments; delete the 4 old `packages/*/changelog.d/` dirs; Test `packages/core/tests/test_towncrier_config.py` (new).

- [ ] **Step 1: Append towncrier config to root `pyproject.toml`**

Append to the end of root `pyproject.toml`:

```toml
[tool.towncrier]
directory = "changelog.d"
filename = "CHANGELOG.md"
start_string = "<!-- towncrier release notes start -->\n"
underlines = ["", "", ""]
title_format = "## [{version}](https://github.com/jeffrichley/agent_core/tree/{version}) - {project_date}"
issue_format = "[#{issue}](https://github.com/jeffrichley/agent_core/issues/{issue})"

[[tool.towncrier.type]]
name = "Security"

[[tool.towncrier.type]]
name = "Removed"

[[tool.towncrier.type]]
name = "Deprecated"

[[tool.towncrier.type]]
name = "Added"

[[tool.towncrier.type]]
name = "Changed"

[[tool.towncrier.type]]
name = "Fixed"

[[tool.towncrier.section]]
name = ""
path = ""

[[tool.towncrier.section]]
name = "core"
path = "core"

[[tool.towncrier.section]]
name = "credentials"
path = "credentials"

[[tool.towncrier.section]]
name = "notify"
path = "notify"

[[tool.towncrier.section]]
name = "agent-core-briefs"
path = "agent-core-briefs"

[[tool.towncrier.section]]
name = "agent-core-busproxy"
path = "agent-core-busproxy"

[[tool.towncrier.section]]
name = "agent-core-channel"
path = "agent-core-channel"

[[tool.towncrier.section]]
name = "agent-core-discord"
path = "agent-core-discord"

[[tool.towncrier.section]]
name = "agent-core-hatchery"
path = "agent-core-hatchery"

[[tool.towncrier.section]]
name = "agent-core-voice"
path = "agent-core-voice"

[[tool.towncrier.section]]
name = "agent-core-webcam"
path = "agent-core-webcam"
```

(The first `name = "" / path = ""` section is towncrier's required default/root bucket. Per `/twisted/towncrier` docs only `name` is needed on each `[[tool.towncrier.type]]`; the fragment dir is the lowercased name, matching the existing `added/changed/fixed` files.)

- [ ] **Step 2: Create the header-only `CHANGELOG.md`**

Create `CHANGELOG.md` (NO release sections — fragments are NOT consumed in the PR; the inaugural build is post-merge, see plan header):

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). Versions are VCS-derived (`uv-dynamic-versioning`); releases are cut with `just release <X.Y.Z>` (see `docs/setup/releases.md`).

This project uses [*towncrier*](https://towncrier.readthedocs.io/); unreleased changes live in per-package `changelog.d/<package>/` fragments.

<!-- towncrier release notes start -->
```

- [ ] **Step 3: Reorg fragments into `changelog.d/<pkg>/`**

```bash
# Create all 10 per-package fragment dirs with a .gitkeep (persists the dir
# across releases — towncrier deletes fragments but the section dirs must remain)
for p in core credentials notify agent-core-briefs agent-core-busproxy agent-core-channel agent-core-discord agent-core-hatchery agent-core-voice agent-core-webcam; do
  mkdir -p "changelog.d/$p"
  : > "changelog.d/$p/.gitkeep"
done
# Move the 13 existing fragments (git mv preserves history)
git mv packages/core/changelog.d/3.changed.md   changelog.d/core/3.changed.md
git mv packages/core/changelog.d/4.changed.md   changelog.d/core/4.changed.md
git mv packages/core/changelog.d/6.added.md     changelog.d/core/6.added.md
git mv packages/core/changelog.d/7.added.md     changelog.d/core/7.added.md
git mv packages/core/changelog.d/37.added.md    changelog.d/core/37.added.md
git mv packages/core/changelog.d/54.fixed.md    changelog.d/core/54.fixed.md
git mv packages/core/changelog.d/67.added.md    changelog.d/core/67.added.md
git mv packages/core/changelog.d/69.fixed.md    changelog.d/core/69.fixed.md
git mv packages/core/changelog.d/70.added.md    changelog.d/core/70.added.md
git mv packages/agent-core-discord/changelog.d/8.added.md   changelog.d/agent-core-discord/8.added.md
git mv packages/agent-core-discord/changelog.d/22.added.md  changelog.d/agent-core-discord/22.added.md
git mv packages/credentials/changelog.d/5.added.md  changelog.d/credentials/5.added.md
git mv packages/notify/changelog.d/4.added.md       changelog.d/notify/4.added.md
# Drop the now-stale per-package changelog.d (incl. core's old .gitkeep)
git rm -q packages/core/changelog.d/.gitkeep
rm -rf packages/core/changelog.d packages/agent-core-discord/changelog.d packages/credentials/changelog.d packages/notify/changelog.d
git add changelog.d
```

- [ ] **Step 4: Verify towncrier renders (draft, no writes)**

Run: `uv run --no-sync towncrier build --draft --version 0.1.0`
Expected: non-error output containing a `## [0.1.0]...` title, per-package section headings (e.g. `core`, `agent-core-discord`), and `### Added/Changed/Fixed` subsections listing the moved fragments. If towncrier errors on the empty `name=""/path=""` section or on all-non-empty paths, adjust per `/twisted/towncrier` docs (e.g. keep the single `name=""/path=""` default section as written) and re-run until the draft renders. **No files are modified by `--draft`.**

- [ ] **Step 5: Write the towncrier guard test**

Create `packages/core/tests/test_towncrier_config.py`:

```python
"""Guard: towncrier is configured and every workspace member has a
changelog.d/<pkg>/ section, so a new member can't silently drop out of
the aggregated CHANGELOG.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root


def test_every_member_has_a_changelog_section_and_dir() -> None:
    root = find_workspace_root(Path(__file__))
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    tc = cfg["tool"]["towncrier"]
    assert tc["directory"] == "changelog.d"
    assert tc["filename"] == "CHANGELOG.md"
    section_paths = {s["path"] for s in tc["section"]}
    members = sorted(p.name for p in (root / "packages").iterdir() if p.is_dir())
    missing_section = [m for m in members if m not in section_paths]
    missing_dir = [
        m for m in members if not (root / "changelog.d" / m).is_dir()
    ]
    assert not missing_section, f"members with no towncrier section: {missing_section}"
    assert not missing_dir, f"members with no changelog.d/<pkg>/ dir: {missing_dir}"
```

- [ ] **Step 6: Run guard → PASS; fast gate; commit**

Run: `uv run --no-sync pytest packages/core/tests/test_towncrier_config.py -v` → PASS.
Run: `just check` → green.
```bash
git add pyproject.toml CHANGELOG.md changelog.d packages/core/tests/test_towncrier_config.py
git commit -m "feat(releases): root towncrier config + aggregated changelog.d/<pkg>/ layout"
```

---

### Task 5: `just release` recipe

**Files:** Modify `justfile` (append after the `install-hooks` recipe).

- [ ] **Step 1: Append the recipe**

Append to `justfile`:

```just
# Cut a release: build the aggregated CHANGELOG from fragments + a local
# annotated tag. Does NOT push — push the tag explicitly when ready.
release VERSION:
    uv run --no-sync towncrier build --yes --version {{VERSION}}
    git add CHANGELOG.md changelog.d
    git commit -m "docs(changelog): release v{{VERSION}}"
    git tag -a "v{{VERSION}}" -m "Release v{{VERSION}}"
    @echo "Tagged v{{VERSION}} locally (changelog committed). Push when ready: git push origin v{{VERSION}}"
```

- [ ] **Step 2: Verify it lists**

Run: `just --list`
Expected: `release` appears with its comment.

- [ ] **Step 3: Dry verification (no real release in the PR)**

Do NOT run `just release` here (it would consume fragments + tag — that is the post-merge step). Instead re-confirm the underlying build is valid:
Run: `uv run --no-sync towncrier build --draft --version 0.1.0`
Expected: same successful draft as Task 4 Step 4 (fragments still present, unconsumed).

- [ ] **Step 4: Commit**

```bash
git add justfile
git commit -m "feat(releases): just release recipe (towncrier build + local annotated tag)"
```

---

### Task 6: `daemon status` surfaces the installed version

**Files:** Modify `packages/core/src/agent_core/daemon/cli.py`; Test `packages/core/tests/test_daemon_status_version.py` (new).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_daemon_status_version.py`:

```python
"""daemon status prints an `installed version:` line (best-effort)."""

from __future__ import annotations

from agent_core.daemon import cli


def test_installed_version_helper_returns_unknown_when_unresolvable(tmp_path):
    # A bogus interpreter path → helper must not raise, returns "unknown".
    assert cli._installed_version(str(tmp_path / "nope" / "python")) == "unknown"


def test_installed_version_helper_reads_metadata() -> None:
    # The current interpreter has agent-core installed (editable dev env).
    import sys

    v = cli._installed_version(sys.executable)
    assert v != "unknown" and v.strip()
```

- [ ] **Step 2: Run → expect FAIL** (`_installed_version` undefined)

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_status_version.py -v`
Expected: FAIL — `AttributeError: module 'agent_core.daemon.cli' has no attribute '_installed_version'`.

- [ ] **Step 3: Add the helper + status line**

In `packages/core/src/agent_core/daemon/cli.py`, add this helper (place it next to `_daemon_python`, near line 70):

```python
def _installed_version(python: str) -> str:
    """Best-effort: the agent-core version installed in the daemon venv.

    Read from the wheel metadata (the true 'what's running' signal — the
    version is VCS-derived at build time, Phase 2). Never raises.
    """
    try:
        result = subprocess.run(
            [python, "-c",
             "import importlib.metadata as m; print(m.version('agent-core'))"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"
```

Then in `status()`, immediately AFTER the line:
```python
        console.print(f"installed sha: {stamp.installed_sha}")
```
add:
```python
        console.print(f"installed version: {_installed_version(daemon_py)}")
```
(`daemon_py` is already defined at line 143 in `status()`.)

- [ ] **Step 4: Run → expect PASS**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_status_version.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Fast gate (mypy covers this file)**

Run: `just check`
Expected: green — `_installed_version` must be mypy-clean (`packages/core/src` is in `[tool.mypy] files`).

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_status_version.py
git commit -m "feat(daemon): status surfaces installed version next to installed sha"
```

---

### Task 7: Docs — release procedure + spec reconciliation

**Files:** Create `docs/setup/releases.md`; Modify `docs/setup/ci.md` (one xref line); Modify `docs/superpowers/specs/2026-05-19-phase2-versioning-releases-design.md`.

- [ ] **Step 1: Write `docs/setup/releases.md`**

Create `docs/setup/releases.md`:

```markdown
# Releasing

Versions are **VCS-derived** (`uv-dynamic-versioning`): a build on a
`vX.Y.Z`-tagged commit is exactly `X.Y.Z`; between tags it is a PEP 440
dev/post version with the git sha embedded. There is **no version field**
to bump.

## Adding a news fragment

Add a Markdown fragment under the package you changed:
`changelog.d/<package>/<issue>.<type>.md` where `<type>` is one of
`added | changed | deprecated | removed | fixed | security`.

## Cutting a release

Releases are cut **on `main`, after** the change has merged through the
`phase1-main-gate` ruleset (the tag must sit on the merged commit so the
version math is correct):

```
just release 0.1.0
git push origin v0.1.0      # explicit — confirm before pushing
```

`just release X.Y.Z` runs `towncrier build` (folds the fragments into the
single root `CHANGELOG.md`, deletes them), commits the changelog, and
creates a **local** annotated `vX.Y.Z` tag. It does **not** push.

Verify after deploy: `agent-core daemon status` shows
`installed version: X.Y.Z`.
```

- [ ] **Step 2: Cross-reference from `docs/setup/ci.md`**

In `docs/setup/ci.md`, in its `## Related` (or equivalent) section, add:
```markdown
- `docs/setup/releases.md` — VCS-derived versioning and how to cut a release.
```
(If no such section exists, append a `## Related` section with that bullet.)

- [ ] **Step 3: Reconcile the Phase 2 spec doc**

In `docs/superpowers/specs/2026-05-19-phase2-versioning-releases-design.md`:
- §3.1: change "fragments stay per-package in `packages/*/changelog.d/`" to the **as-built** model: a single root `changelog.d/` with one subdir per package (`changelog.d/<pkg>/`), towncrier *sections* model — native; one-time move of the existing fragments was performed. Add one sentence noting native towncrier reads exactly one `directory`, so per-package-scattered dirs were not viable.
- §3.3 / §7: make unambiguous that the **PR does not consume fragments** (ships config + reorg + header-only `CHANGELOG.md`); the inaugural `towncrier build` + `v0.1.0` tag is the **post-merge** step. Remove the contradictory "consumed fragments' deletions are part of the PR" wording.
- §2.2: note the **as-built** cache-keys are `{ git = { commit = true, tags = true } }` (the `tags = true` is required so a new tag invalidates uv's build cache) and the Phase 0 guard test was updated in lockstep; and that NO custom `pattern` is set (default v-prefix ignores `pepper-cutover-*`, guarded by a test).

- [ ] **Step 4: Fast gate (docs only — confirm nothing broke) + commit**

Run: `just check` → green.
```bash
git add docs/setup/releases.md docs/setup/ci.md docs/superpowers/specs/2026-05-19-phase2-versioning-releases-design.md
git commit -m "docs(releases): release procedure + reconcile Phase 2 spec with implementation"
```

---

### Task 8: PR + CI acceptance; record the post-merge inaugural release

**Files:** none.

> Pushing the branch and opening the PR are shared-state actions — the
> executor MUST get explicit user confirmation immediately before Step 1
> (consistent with the project's "respect the gate / confirm shared-state"
> rule). Pushing is via the feature branch only; never local-push `main`.

- [ ] **Step 1: Push + open PR (after explicit user confirmation)**

```bash
git push origin worktree-feat+phase2-versioning-releases:feat/phase2-versioning-releases
gh pr create --base main --head feat/phase2-versioning-releases \
  --title "Phase 2: VCS-derived versioning + towncrier releases" \
  --body "Implements docs/superpowers/specs/2026-05-19-phase2-versioning-releases-design.md (see plan docs/superpowers/plans/2026-05-19-phase2-versioning-releases.md). NOTE: the inaugural v0.1.0 tag is cut post-merge on main, not in this PR."
```
The push runs the Phase 1 `pre-push` hook (`just check`). If it blocks, fix the failure — do not `--no-verify`.

- [ ] **Step 2: Watch the gate**

Run: `gh pr checks <N> --watch` (capture `gh`'s own exit code, not a pipe's).
Expected: `check (ubuntu-latest)`, `check (windows-latest)`, `integration` all green; `gh pr view <N> --json mergeStateStatus` → `CLEAN`. The `integration` job runs the new `slow` version tests (`test_dynamic_versioning.py`).

- [ ] **Step 3: Merge through the gate (after explicit user confirmation)**

`gh pr merge <N> --merge --delete-branch` (server-side, through the ruleset — never a local push to `main`).

- [ ] **Step 4: Record the post-merge inaugural release (do NOT perform here)**

The inaugural release is a **separate, explicitly-confirmed** post-merge step, performed on a fresh worktree/checkout of the `main` merge commit (not part of this plan's automated execution):
```bash
# on main @ the Phase 2 merge commit:
just release 0.1.0          # towncrier build + changelog commit + local tag v0.1.0
git push origin v0.1.0      # EXPLICIT confirmation required (shared-state)
# then deploy + verify:
agent-core daemon refresh && agent-core daemon status   # expect: installed version: 0.1.0
```
Report this runbook to the user; do not execute it automatically.

- [ ] **Step 5: Report** — branch pushed, PR #, 3 checks green, ruleset gating, merged; then the post-merge `just release 0.1.0` runbook. Hand to `superpowers:finishing-a-development-branch`.

---

## Self-Review

**Spec coverage:**
- §2.1 per-member pyproject switch → Task 2. §2.2 tool/pattern/cache-keys → Task 1 (`tags=true`) + Task 2 (dynamic version, default pattern + Task 2 pepper test). §2.3 `uv.lock` no-thrash → Task 2 Step 4 + Task 3. ✔
- §3.1 single aggregated changelog (sections model, as reconciled) → Task 4. §3.2 `just release` (build + local tag, no push) → Task 5. §3.3 inaugural `v0.1.0` post-merge, no fragment-consumption in PR → plan header + Task 7 Step 3 + Task 8 Step 4. ✔
- §4 `daemon status` version → Task 6. ✔
- §5 tests: version-format/fallback → Task 2; pepper-ignored → Task 2; uv.lock no-thrash → Task 3; daemon-status → Task 6; towncrier guard → Task 4; PR-is-acceptance → Task 8. ✔
- §6 risks: forgot `uv lock` → Task 2 Step 4 + CI `--locked`; tag-on-feature-branch → Task 8 Step 4 (post-merge only); pepper tags → Task 2 test; tool keys → context7-resolved in this plan; missing member section → Task 4 guard; backlog absorption → post-merge, confirmed; tag push shared-state → Task 8 explicit-confirm. ✔
- §7 rollout / §8 one-time setup → Task 2 (`uv lock`), Task 8 Step 4 (post-merge `just release 0.1.0` + tag push). ✔

**Placeholder scan:** none. `<N>` (PR number) is produced by Task 8 Step 1's `gh pr create`. Task 4 Step 4 "adjust per docs if towncrier errors on the default section" is an explicit verify-and-adjust with the concrete fallback stated (keep the `name=""/path=""` section), not a vague TODO. Exact TOML/code given in every step.

**Type/name consistency:** `_installed_version(python: str) -> str` defined and used identically (Task 6). `EXPECTED_CACHE_KEYS` shape consistent between Task 1 and the guard. `find_workspace_root(Path(__file__))` reused from the Phase 0 guard pattern in Tasks 3 & 4. Member dir list identical across Task 1/2/3/4. towncrier `directory="changelog.d"`, `filename="CHANGELOG.md"`, section `path`==member-dir consistent across Task 4 config, the guard test, and `docs/setup/releases.md`.
