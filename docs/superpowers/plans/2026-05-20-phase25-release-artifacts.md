# Phase 2.5 — Release Artifacts + Bug Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the source-rebuild-at-deploy model with a release-artifact deploy pipeline (release-please + GH Release wheel attachments + `daemon install --release` flag), retire the Phase 2 towncrier setup, and fix two Phase 2 bugs (B1: dunamai bare-repo fallback; B2: false-positive "fallback" warning at `cli.py:164`).

**Architecture:** Conventional commits on `main` → release-please bot opens a release PR → merging it creates the tag + GitHub Release atomically → a `release: published` workflow builds 10 wheels on a real GH-provided checkout (B1 sidestepped) and uploads them as release assets → `agent-core daemon install --release vX.Y.Z` (or just `install` for latest) downloads from the release and runs `uv pip install --force-reinstall --no-deps` into the daemon venv. Source-based install path is deleted entirely.

**Tech Stack:** Python 3.12, uv 0.7.13 (workspace), hatchling + `uv-dynamic-versioning` (Phase 2), `googleapis/release-please-action` (v4), `amannn/action-semantic-pull-request` (v5), `gh` CLI in GH Actions.

**Spec:** `docs/superpowers/specs/2026-05-20-phase25-release-artifacts-design.md`

**Branch:** create `feat/phase25-release-artifacts` off `origin/main`. Land via PR through `phase1-main-gate` (squash-merge once that's the only enabled merge type — see Task 13).

---

## File structure

### New files
- `release-please-config.json` (repo root) — release-please's behavior config
- `.release-please-manifest.json` (repo root) — current version state
- `.github/workflows/release-please.yml` — bot runner
- `.github/workflows/release.yml` — wheel-build + upload workflow
- `.github/workflows/pr-title-lint.yml` — Conventional Commits PR title check
- `packages/core/src/agent_core/daemon/release.py` — pure functions for GH Release fetching + wheel install
- `packages/core/tests/test_daemon_release.py` — unit tests for release.py
- `packages/core/tests/test_daemon_status_b2.py` — B2 regression test

### Modified files
- `packages/core/src/agent_core/daemon/cli.py` — `--release` flag, source path removed, B2 fix
- `packages/core/src/agent_core/daemon/install.py` — stamp schema migrated, `run_install` removed
- `packages/core/tests/test_daemon_install.py` — drop source-path test cases
- `packages/core/tests/test_daemon_status_version.py` — minor update for new stamp shape
- `pyproject.toml` (root) — remove `[tool.towncrier]`, remove `towncrier` dev dep
- `uv.lock` — refreshed after dep removal
- `justfile` — remove `release VERSION` recipe
- `docs/setup/releases.md` — rewritten for new flow
- `.mcp.json` (workspace) — already uncommitted (empty `mcpServers: {}` from prior work today)

### Deleted files
- `changelog.d/` — entire directory (10 subdirs × `.gitkeep`)

---

## Task 1: Setup branch + create release-please config files

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`

- [ ] **Step 1: Create the worktree-based branch (bare repo topology)**

```bash
cd E:/workspaces/ai/agents/agent_core
git --work-tree=. fetch origin
git worktree add .worktrees/phase25-release-artifacts -b feat/phase25-release-artifacts origin/main
cd .worktrees/phase25-release-artifacts
just install-hooks    # pre-push hook for this fresh worktree
uv sync               # baseline workspace .venv for this worktree
```

- [ ] **Step 2: Run baseline `just check` to confirm clean starting state**

Run: `just check`
Expected: PASS (all checks green — this is the post-Phase-2 baseline)

- [ ] **Step 3: Create `release-please-config.json`**

Write to `release-please-config.json`:
```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "release-type": "simple",
  "include-v-in-tag": true,
  "bump-minor-pre-major": true,
  "bump-patch-for-minor-pre-major": false,
  "separate-pull-requests": false,
  "packages": {
    ".": {
      "release-type": "simple",
      "changelog-path": "CHANGELOG.md"
    }
  }
}
```

Why each key:
- `release-type: "simple"` — release-please will NOT modify any source files; it only updates `.release-please-manifest.json` and `CHANGELOG.md`. That's exactly what we want because Phase 2's `uv-dynamic-versioning` derives versions from git tags, not from any version field.
- `include-v-in-tag: true` — produces `vX.Y.Z` tags. (This is the default; explicit for clarity.)
- `bump-minor-pre-major: true` — while on 0.x, `feat:` commits bump the minor (0.1.0 → 0.2.0). Without it, on 0.x semver normally treats every commit as a patch.
- `separate-pull-requests: false` — one release PR for the whole monorepo, matching our lockstep versioning model.
- `packages: { "." : ... }` — single root component; one shared version across all 10 packages.

- [ ] **Step 4: Create `.release-please-manifest.json`**

Write to `.release-please-manifest.json`:
```json
{
  ".": "0.1.0"
}
```

This is the bootstrap state. release-please will update it on every release PR.

- [ ] **Step 5: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "feat(release): bootstrap release-please config + manifest at 0.1.0"
```

---

## Task 2: Create release-please workflow

**Files:**
- Create: `.github/workflows/release-please.yml`

- [ ] **Step 1: Resolve the current SHA for googleapis/release-please-action v4**

Run:
```bash
gh api repos/googleapis/release-please-action/git/refs/tags/v4 --jq .object.sha
```

Record the resulting 40-char SHA — we'll substitute it for `<SHA>` below.

- [ ] **Step 2: Write the workflow file**

Write to `.github/workflows/release-please.yml`:
```yaml
name: release-please

on:
  push:
    branches:
      - main

permissions:
  contents: write
  pull-requests: write

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@<SHA>  # v4 — resolved via gh api in Step 1
        id: release
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

Replace `<SHA>` with the value from Step 1. Keep the `# v4 — resolved via gh api in Step 1` comment as the human-readable equivalent — it's standard convention for SHA-pinned actions.

- [ ] **Step 3: Validate yaml syntax**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release-please.yml'))"
```
Expected: no output (silent success), exit 0.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release-please.yml
git commit -m "feat(release): release-please bot workflow"
```

---

## Task 3: Create PR title lint workflow

**Files:**
- Create: `.github/workflows/pr-title-lint.yml`

- [ ] **Step 1: Resolve the current SHA for amannn/action-semantic-pull-request v5**

Run:
```bash
gh api repos/amannn/action-semantic-pull-request/git/refs/tags/v5 --jq .object.sha
```

Record the 40-char SHA.

- [ ] **Step 2: Verify parameter names against the action's README**

Open `https://github.com/amannn/action-semantic-pull-request` and confirm that the input parameter names used in Step 3 (`types`, `requireScope`, `subjectPattern`) match the current README. If anything differs, adjust before writing.

- [ ] **Step 3: Write the workflow file**

Write to `.github/workflows/pr-title-lint.yml`:
```yaml
name: pr-title-lint

on:
  pull_request_target:
    types:
      - opened
      - edited
      - synchronize

permissions:
  pull-requests: read

jobs:
  validate:
    name: Validate PR title
    runs-on: ubuntu-latest
    steps:
      - uses: amannn/action-semantic-pull-request@<SHA>  # v5 — resolved via gh api in Step 1
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          types: |
            feat
            fix
            chore
            docs
            refactor
            test
            style
            build
            ci
            perf
            revert
          requireScope: false
          subjectPattern: ^(?![A-Z]).+$
          subjectPatternError: |
            The subject "{subject}" found in the pull request title "{title}"
            didn't match the configured pattern. Please ensure that the subject
            doesn't start with an uppercase character.
```

Replace `<SHA>` with the value from Step 1.

Why `pull_request_target` (not `pull_request`): `pull_request_target` runs in the base-repo context and can use the default `GITHUB_TOKEN` to read PR metadata from forks safely. With `permissions: pull-requests: read`, this is safe even on fork PRs (read-only scope, no checkout of untrusted code).

- [ ] **Step 4: Validate yaml syntax**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/pr-title-lint.yml'))"
```
Expected: silent success, exit 0.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/pr-title-lint.yml
git commit -m "feat(ci): PR title lint workflow (Conventional Commits enforcement)"
```

---

## Task 4: Create wheel-build + upload workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Resolve SHAs for actions we'll reuse**

Run:
```bash
gh api repos/actions/checkout/git/refs/tags/v4 --jq .object.sha
gh api repos/astral-sh/setup-uv/git/refs/tags/v8 --jq .object.sha
```

(These are the same families used in the existing Phase 1 `ci.yml` — match those SHAs if they're already known-good and current.)

- [ ] **Step 2: Write the workflow file**

Write to `.github/workflows/release.yml`:
```yaml
name: release

on:
  release:
    types: [published]

permissions:
  contents: write

jobs:
  build-and-upload:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@<CHECKOUT_SHA>  # v4
        with:
          ref: ${{ github.event.release.tag_name }}
          fetch-depth: 0
          fetch-tags: true

      - uses: astral-sh/setup-uv@<SETUP_UV_SHA>  # v8
        with:
          python-version: "3.12"

      - name: Build wheels
        run: uv build --all-packages --wheel --out-dir dist/

      - name: Export pinned requirements (cu130 extra)
        run: |
          uv export --frozen --no-dev --extra cu130 \
                    --no-hashes --format requirements-txt \
                    > dist/requirements.txt

      - name: List built artifacts
        run: ls -la dist/

      - name: Upload artifacts to GH Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release upload ${{ github.event.release.tag_name }} dist/*.whl dist/requirements.txt
```

Notes:
- `ref: ${{ github.event.release.tag_name }}` checks out the exact tagged commit (release-please tagged it on the release-PR merge commit). Combined with `fetch-tags: true`, `uv-dynamic-versioning` will see the tag and bake the correct version into every wheel filename + metadata.
- `uv build --all-packages --wheel --out-dir dist/` produces 10 wheels.
- `uv export --extra cu130 --no-hashes --format requirements-txt` produces a pinned `requirements.txt` that **embeds the PyTorch cu130 index URL** (`--extra-index-url https://download.pytorch.org/whl/cu130`) so `daemon install --release` can resolve cu130-tagged torch wheels (`torch==2.12.0+cu130`) which don't exist on PyPI. Without this file, fresh installs on new boxes would fail to find cu130 torch.
- `gh release upload <tag> dist/*.whl dist/requirements.txt` attaches all artifacts to the release that release-please just published.
- `permissions: contents: write` is the only scope needed — verified via the release-please-action README's own example for this exact pattern.

- [ ] **Step 3: Validate yaml syntax**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: silent success, exit 0.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat(release): wheel-build workflow on release: published"
```

---

## Task 5: Create `daemon/release.py` module (TDD)

**Files:**
- Create: `packages/core/src/agent_core/daemon/release.py`
- Test: `packages/core/tests/test_daemon_release.py`

- [ ] **Step 1: Write the failing tests (test file first)**

Write to `packages/core/tests/test_daemon_release.py`:
```python
"""Unit tests for daemon/release.py — pure functions over a fake fetcher."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from agent_core.daemon.release import (
    NoReleasesError,
    WheelAsset,
    download_wheels,
    list_release_wheels,
    resolve_version,
)


# ---- resolve_version --------------------------------------------------------

def _fake_fetcher(responses: dict[str, bytes]) -> Callable[[str], bytes]:
    def f(url: str) -> bytes:
        if url not in responses:
            raise RuntimeError(f"unexpected URL: {url}")
        return responses[url]
    return f


def test_resolve_version_explicit_passes_through() -> None:
    # Explicit version — fetcher must not be called.
    f = _fake_fetcher({})  # any call would raise
    assert resolve_version("v0.1.0", repo="x/y", fetcher=f) == "v0.1.0"


def test_resolve_version_latest_resolves_from_api() -> None:
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/latest": json.dumps(
            {"tag_name": "v0.2.0"}
        ).encode("utf-8")
    })
    assert resolve_version(None, repo="x/y", fetcher=f) == "v0.2.0"


def test_resolve_version_no_releases_raises() -> None:
    # GitHub API returns 404 → fetcher raises; we wrap it.
    def f(url: str) -> bytes:
        raise RuntimeError("404 Not Found")

    with pytest.raises(NoReleasesError):
        resolve_version(None, repo="x/y", fetcher=f)


# ---- list_release_wheels ----------------------------------------------------

_RELEASE_JSON = json.dumps({
    "tag_name": "v0.1.0",
    "assets": [
        {"name": "agent_core-0.1.0-py3-none-any.whl",
         "browser_download_url": "https://example/agent_core-0.1.0-py3-none-any.whl"},
        {"name": "agent_core_busproxy-0.1.0-py3-none-any.whl",
         "browser_download_url": "https://example/agent_core_busproxy-0.1.0-py3-none-any.whl"},
        {"name": "checksums.txt",
         "browser_download_url": "https://example/checksums.txt"},
        {"name": "Source code (zip)",
         "browser_download_url": "https://example/source.zip"},
    ],
}).encode("utf-8")


def test_list_release_wheels_filters_to_whl_only() -> None:
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/tags/v0.1.0": _RELEASE_JSON
    })
    wheels = list_release_wheels("v0.1.0", repo="x/y", fetcher=f)
    assert [w.name for w in wheels] == [
        "agent_core-0.1.0-py3-none-any.whl",
        "agent_core_busproxy-0.1.0-py3-none-any.whl",
    ]
    assert all(isinstance(w, WheelAsset) for w in wheels)


# ---- download_wheels --------------------------------------------------------

def test_download_wheels_writes_files(tmp_path: Path) -> None:
    assets = [
        WheelAsset(
            name="a.whl",
            download_url="https://example/a.whl",
        ),
        WheelAsset(
            name="b.whl",
            download_url="https://example/b.whl",
        ),
    ]
    f = _fake_fetcher({
        "https://example/a.whl": b"WHEEL_BYTES_A",
        "https://example/b.whl": b"WHEEL_BYTES_B",
    })

    paths = download_wheels(assets, dest=tmp_path, fetcher=f)

    assert sorted(p.name for p in paths) == ["a.whl", "b.whl"]
    assert (tmp_path / "a.whl").read_bytes() == b"WHEEL_BYTES_A"
    assert (tmp_path / "b.whl").read_bytes() == b"WHEEL_BYTES_B"


def test_download_wheels_skips_existing_with_same_size(tmp_path: Path) -> None:
    # Pre-populate one wheel.
    (tmp_path / "a.whl").write_bytes(b"WHEEL_BYTES_A")

    assets = [WheelAsset(name="a.whl", download_url="https://example/a.whl")]

    call_count = {"n": 0}

    def f(url: str) -> bytes:
        call_count["n"] += 1
        return b"WHEEL_BYTES_A"

    download_wheels(assets, dest=tmp_path, fetcher=f)
    assert call_count["n"] == 0, "fetcher should not be called for an already-present file"


# ---- download_requirements --------------------------------------------------

_RELEASE_JSON_WITH_REQS = json.dumps({
    "tag_name": "v0.1.0",
    "assets": [
        {"name": "agent_core-0.1.0-py3-none-any.whl",
         "browser_download_url": "https://example/agent_core-0.1.0-py3-none-any.whl"},
        {"name": "requirements.txt",
         "browser_download_url": "https://example/requirements.txt"},
    ],
}).encode("utf-8")


def test_download_requirements_writes_file(tmp_path: Path) -> None:
    from agent_core.daemon.release import download_requirements
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/tags/v0.1.0": _RELEASE_JSON_WITH_REQS,
        "https://example/requirements.txt": b"# pinned deps\ntorch==2.12.0+cu130\n",
    })
    path = download_requirements("v0.1.0", repo="x/y", dest=tmp_path, fetcher=f)
    assert path == tmp_path / "requirements.txt"
    assert (tmp_path / "requirements.txt").read_text() == "# pinned deps\ntorch==2.12.0+cu130\n"


def test_download_requirements_missing_raises(tmp_path: Path) -> None:
    from agent_core.daemon.release import download_requirements
    # Release exists but has no requirements.txt attached
    release_json = json.dumps({
        "tag_name": "v0.0.9",
        "assets": [
            {"name": "agent_core-0.0.9-py3-none-any.whl",
             "browser_download_url": "https://example/x.whl"},
        ],
    }).encode("utf-8")
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/tags/v0.0.9": release_json,
    })
    with pytest.raises(FileNotFoundError, match="requirements.txt"):
        download_requirements("v0.0.9", repo="x/y", dest=tmp_path, fetcher=f)


def test_download_requirements_skips_existing(tmp_path: Path) -> None:
    from agent_core.daemon.release import download_requirements
    # Pre-populate
    (tmp_path / "requirements.txt").write_text("cached content")

    calls = {"n": 0}
    def f(url: str) -> bytes:
        calls["n"] += 1
        return b"new content"

    path = download_requirements("v0.1.0", repo="x/y", dest=tmp_path, fetcher=f)
    assert calls["n"] == 0
    assert path.read_text() == "cached content"


# ---- ensure_venv ------------------------------------------------------------

def test_ensure_venv_no_op_when_python_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.daemon.release import ensure_venv
    import sys
    # Pre-create the venv python
    if sys.platform == "win32":
        py = tmp_path / "Scripts" / "python.exe"
    else:
        py = tmp_path / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")

    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        class _R: returncode = 0
        return _R()
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_run)

    ensure_venv(tmp_path)
    assert calls == [], "uv venv should not be invoked when python already exists"


def test_ensure_venv_creates_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.daemon.release import ensure_venv
    venv = tmp_path / "newvenv"
    # venv dir doesn't exist yet
    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        class _R: returncode = 0
        return _R()
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_run)

    ensure_venv(venv, python_version="3.12")
    assert len(calls) == 1
    assert calls[0][:3] == ["uv", "venv", str(venv)]
    assert "--python" in calls[0]
    assert "3.12" in calls[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_release.py -v`
Expected: `ImportError` (module not yet defined) → all tests fail to collect.

- [ ] **Step 3: Implement `daemon/release.py`**

Write to `packages/core/src/agent_core/daemon/release.py`:
```python
"""Release-artifact fetching + installation for the daemon.

Pure-ish functions: HTTP I/O is injected via a `fetcher` callable so unit
tests can stub it. The defaults use `urllib.request` (stdlib) so the
daemon venv carries no extra dependency.

Used by `agent-core daemon install --release` and `daemon refresh
--release` in `daemon/cli.py`.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class NoReleasesError(Exception):
    """Raised when `latest` is requested but no releases exist for the repo."""


@dataclass(frozen=True)
class WheelAsset:
    """A `.whl` asset on a GitHub Release."""
    name: str
    download_url: str


# Default fetcher: urllib over the stdlib, returns raw bytes.
def _default_fetcher(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-core-daemon"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


Fetcher = Callable[[str], bytes]


def resolve_version(
    version: str | None,
    *,
    repo: str,
    fetcher: Fetcher = _default_fetcher,
) -> str:
    """Resolve a user-supplied version to a concrete `vX.Y.Z` tag.

    - `version="vX.Y.Z"` → returned unchanged.
    - `version=None` → query GitHub's `/releases/latest` endpoint, return its
      `tag_name`. Raises NoReleasesError if no releases exist.
    """
    if version is not None:
        return version

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        body = fetcher(url)
    except Exception as exc:  # noqa: BLE001 — wrap any fetch failure
        raise NoReleasesError(
            f"could not resolve latest release for {repo} (no releases yet?): {exc}"
        ) from exc

    data = json.loads(body)
    return data["tag_name"]


def list_release_wheels(
    version: str,
    *,
    repo: str,
    fetcher: Fetcher = _default_fetcher,
) -> list[WheelAsset]:
    """Return the `.whl` assets attached to release `version` (e.g. `v0.1.0`)."""
    url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"
    body = fetcher(url)
    data = json.loads(body)
    wheels: list[WheelAsset] = []
    for asset in data.get("assets", []):
        if asset["name"].endswith(".whl"):
            wheels.append(
                WheelAsset(
                    name=asset["name"],
                    download_url=asset["browser_download_url"],
                )
            )
    return wheels


def download_wheels(
    assets: list[WheelAsset],
    *,
    dest: Path,
    fetcher: Fetcher = _default_fetcher,
) -> list[Path]:
    """Download each asset into `dest/`, skipping if a file of the same name exists.

    Skipping by name (not size/hash) is deliberate: the local cache at
    `~/.agent-core/releases/vX.Y.Z/` is keyed by exact release tag, so a
    matching filename implies a matching wheel. If you need to force a
    redownload, delete the cache dir.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for asset in assets:
        path = dest / asset.name
        if not path.exists():
            body = fetcher(asset.download_url)
            path.write_bytes(body)
        out.append(path)
    return out


def download_requirements(
    version: str,
    *,
    repo: str,
    dest: Path,
    fetcher: Fetcher = _default_fetcher,
) -> Path:
    """Download `requirements.txt` from the release `version` into `dest/`.

    Same skip-if-present cache logic as `download_wheels`. Returns the
    local path to the downloaded requirements file. Raises if no
    `requirements.txt` is attached to the release.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / "requirements.txt"
    if out_path.exists():
        return out_path

    # Resolve the release to find the requirements.txt asset.
    url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"
    body = fetcher(url)
    data = json.loads(body)
    for asset in data.get("assets", []):
        if asset["name"] == "requirements.txt":
            out_path.write_bytes(fetcher(asset["browser_download_url"]))
            return out_path
    raise FileNotFoundError(
        f"release {version} has no requirements.txt asset; "
        f"cannot resolve dependencies (was the release built by Phase 2.5+?)"
    )


def ensure_venv(venv: Path, *, python_version: str = "3.12") -> None:
    """Create the daemon venv at `venv` if it doesn't already exist.

    Idempotent: no-op if `venv/Scripts/python.exe` (or `bin/python`) is
    present. Uses `uv venv` (the only reliable way to provision a Python
    on Windows + Linux without local Python install assumptions)."""
    import sys
    if sys.platform == "win32":
        existing = venv / "Scripts" / "python.exe"
    else:
        existing = venv / "bin" / "python"
    if existing.exists():
        return
    subprocess.run(
        ["uv", "venv", str(venv), "--python", python_version],
        check=True,
    )


def install_requirements(req_path: Path, *, venv_python: Path) -> None:
    """Install pinned dependencies from a requirements.txt into the daemon venv.

    Resolves the PyTorch cu130 index URL embedded in the requirements file."""
    cmd = [
        "uv", "pip", "install",
        "--python", str(venv_python),
        "--requirement", str(req_path),
    ]
    subprocess.run(cmd, check=True)


def install_wheels(wheel_paths: list[Path], *, venv_python: Path) -> None:
    """Replace the installed agent_core* packages in `venv_python`'s env with
    the contents of `wheel_paths`. Surgical — does not touch dependencies.

    Call AFTER `install_requirements` so deps are present."""
    cmd = [
        "uv", "pip", "install",
        "--python", str(venv_python),
        "--force-reinstall",
        "--no-deps",
        *[str(p) for p in wheel_paths],
    ]
    subprocess.run(cmd, check=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_release.py -v`
Expected: 5 tests pass.

- [ ] **Step 5: Run lint + typecheck**

Run: `just lint && just typecheck`
Expected: PASS for the new file. (If `daemon/release.py` triggers ruff/mypy warnings, fix them.)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/release.py packages/core/tests/test_daemon_release.py
git commit -m "feat(daemon): release.py module — fetch + install wheels from GH Release"
```

---

## Task 6: Migrate install stamp schema

**Files:**
- Modify: `packages/core/src/agent_core/daemon/install.py:50-91` (the `InstallStamp` dataclass + `read_stamp` + `write_stamp`)
- Modify: `packages/core/tests/test_daemon_status_version.py` (existing tests)

- [ ] **Step 1: Read existing test_daemon_status_version.py to understand its expectations**

Run: `cat packages/core/tests/test_daemon_status_version.py | head -50`

Make a note of which stamp fields the existing tests check.

- [ ] **Step 2: Update the InstallStamp dataclass + read_stamp / write_stamp**

In `packages/core/src/agent_core/daemon/install.py`, replace the `InstallStamp` dataclass + `read_stamp` function:
```python
@dataclass(frozen=True)
class InstallStamp:
    """Captures what was installed into the daemon venv, when, and from where."""

    installed_at: str            # ISO 8601 UTC
    installed_sha: str           # git rev-parse HEAD of the source at install time
    installed_version: str       # human-readable version (e.g. "0.1.0")
    python_version: str          # e.g. "3.12.5"
    extra: str | None            # uv extra name, or None
    release_tag: str | None      # provenance: GH release tag, or None for non-release installs


def write_stamp(home: Path, stamp: InstallStamp) -> None:
    """Write the install stamp to <home>/.daemon-install-stamp.json."""
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP_FILENAME).write_text(
        json.dumps(asdict(stamp), indent=2) + "\n",
        encoding="utf-8",
    )


def read_stamp(home: Path) -> InstallStamp | None:
    """Read the install stamp. None on missing/corrupt/schema-incomplete.

    Forward-compatible: unknown fields are silently dropped. Missing new
    fields default to None / "unknown" so older stamps from Phase 2
    continue to read (the next install/refresh writes the new schema)."""
    path = home / STAMP_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return InstallStamp(
            installed_at=data["installed_at"],
            installed_sha=data["installed_sha"],
            installed_version=data.get("installed_version", "unknown"),
            python_version=data["python_version"],
            extra=data.get("extra"),
            release_tag=data.get("release_tag"),
        )
    except (KeyError, TypeError):
        return None
```

Required-field set is `installed_at`, `installed_sha`, `python_version` (the three from Phase 0/2 that older stamps all have). New fields `installed_version` and `release_tag` are optional on read; `uv_lock_hash` is silently dropped if present in older stamps.

- [ ] **Step 3: Update test_daemon_status_version.py to match the new shape**

Whatever fields the existing tests reference, update them to match the new dataclass. If a test asserts the presence of `uv_lock_hash`, delete that assertion (lock-drift is gone). Add an assertion that the new fields are read correctly:

In `packages/core/tests/test_daemon_status_version.py`, add a new test:
```python
def test_read_stamp_old_schema_back_compat(tmp_path: Path) -> None:
    """Stamps written by Phase 2 lacked installed_version/release_tag and
    carried uv_lock_hash. read_stamp must accept them and default the new
    fields, dropping the obsolete one."""
    stamp_path = tmp_path / ".daemon-install-stamp.json"
    stamp_path.write_text(json.dumps({
        "installed_at": "2026-05-20T10:00:00Z",
        "installed_sha": "abc1234",
        "python_version": "3.12",
        "extra": "cu130",
        "uv_lock_hash": "sha256:legacy",
    }) + "\n", encoding="utf-8")

    stamp = read_stamp(tmp_path)
    assert stamp is not None
    assert stamp.installed_at == "2026-05-20T10:00:00Z"
    assert stamp.installed_sha == "abc1234"
    assert stamp.installed_version == "unknown"   # new field defaulted
    assert stamp.release_tag is None              # new field defaulted
    # uv_lock_hash silently dropped; no attribute on the dataclass.
```

- [ ] **Step 4: Run the tests**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_status_version.py -v`
Expected: existing tests pass after field updates; new back-compat test passes.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/daemon/install.py packages/core/tests/test_daemon_status_version.py
git commit -m "refactor(daemon): stamp schema — installed_version + release_tag, drop uv_lock_hash"
```

---

## Task 7: Add `--release` flag to `daemon install` and `daemon refresh`

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py` (the `install` command + `refresh` command)
- Test: cover behavior in `packages/core/tests/test_daemon_release.py` (extend it)

- [ ] **Step 1: Add a high-level orchestration test**

Append to `packages/core/tests/test_daemon_release.py`:
```python
# ---- end-to-end orchestration via CLI ----

from typer.testing import CliRunner

from agent_core.daemon.cli import app as daemon_app


def test_install_release_invokes_full_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent-core daemon install --release v0.2.0` resolves, downloads,
    installs, and writes a stamp — through fake injection points."""
    # Arrange a fake home + fake fetcher + fake uv pip install.
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")  # fake daemon python

    fake_release_json = json.dumps({
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "agent_core-0.2.0-py3-none-any.whl",
             "browser_download_url": "https://example/agent_core-0.2.0.whl"},
            {"name": "requirements.txt",
             "browser_download_url": "https://example/requirements.txt"},
        ],
    }).encode("utf-8")

    calls: list[list[str]] = []

    def fake_fetcher(url: str) -> bytes:
        if url.endswith("/releases/tags/v0.2.0"):
            return fake_release_json
        if url.endswith(".whl"):
            return b"FAKE_WHEEL_BYTES"
        if url.endswith("/requirements.txt"):
            return b"# pinned\ntorch==2.12.0+cu130\n"
        raise RuntimeError(f"unexpected URL: {url}")

    def fake_subprocess_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr("agent_core.daemon.release._default_fetcher", fake_fetcher)
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_subprocess_run)

    runner = CliRunner()
    result = runner.invoke(daemon_app, ["install", "--release", "v0.2.0"])

    # Assert: the install ran the expected uv pip commands in order:
    #   1. uv pip install --requirement (deps)
    #   2. uv pip install --force-reinstall --no-deps (wheels)
    # ensure_venv's uv venv may also be called (idempotent — skipped if .venv exists).
    assert result.exit_code == 0, result.output
    pip_calls = [c for c in calls if "pip" in c and "install" in c]
    assert len(pip_calls) >= 2, f"expected at least 2 uv pip install calls, got: {pip_calls}"
    assert any("--requirement" in c for c in pip_calls), "requirements.txt install missing"
    assert any("--no-deps" in c for c in pip_calls), "wheel surgical install missing"
    # Assert: stamp written with the right version + tag
    stamp_text = (tmp_path / ".daemon-install-stamp.json").read_text()
    stamp = json.loads(stamp_text)
    assert stamp["installed_version"] == "0.2.0"
    assert stamp["release_tag"] == "v0.2.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_release.py::test_install_release_invokes_full_chain -v`
Expected: FAIL (the `--release` flag doesn't exist yet)

- [ ] **Step 3: Update `daemon/cli.py` `install` command**

In `packages/core/src/agent_core/daemon/cli.py`:

Add imports at the top (with existing imports):
```python
from agent_core.daemon.release import (
    NoReleasesError,
    download_requirements,
    download_wheels,
    ensure_venv,
    install_requirements,
    install_wheels,
    list_release_wheels,
    resolve_version,
)
```

Define a constant near the top of the file:
```python
RELEASE_REPO = "jeffrichley/agent_core"
```

Replace the existing `install` command body with:
```python
@app.command()
def install(
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (e.g. v0.1.0). Default: latest release.",
    ),
) -> None:
    """Populate ~/.agent-core/.venv/ from a GitHub Release artifact."""
    pid_file = _pid_path()
    existing = read_pid(pid_file)
    if existing is not None and is_alive(existing):
        console.print(
            f"[red]daemon is currently running (PID {existing}).[/red]\n"
            "   • Run [bold]agent-core daemon stop[/bold] and re-run install, or\n"
            "   • Run [bold]agent-core daemon refresh[/bold] to stop/install/start in one step."
        )
        raise typer.Exit(code=1)

    home = _home()
    home.mkdir(parents=True, exist_ok=True)

    # Resolve version (None → latest).
    try:
        tag = resolve_version(release, repo=RELEASE_REPO)
    except NoReleasesError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Fetch + download artifacts.
    cache_dir = home / "releases" / tag
    assets = list_release_wheels(tag, repo=RELEASE_REPO)
    if not assets:
        console.print(f"[red]release {tag} has no .whl assets attached[/red]")
        raise typer.Exit(code=1)
    wheel_paths = download_wheels(assets, dest=cache_dir)
    try:
        req_path = download_requirements(tag, repo=RELEASE_REPO, dest=cache_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Ensure daemon venv exists (creates if first install on this box).
    venv = home / ".venv"
    try:
        ensure_venv(venv, python_version="3.12")
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]uv venv failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(code=1) from exc

    venv_python = _daemon_python_path()

    # Install pinned dependencies (no-op-fast on upgrades when deps unchanged).
    try:
        install_requirements(req_path, venv_python=venv_python)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]dep install failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(code=1) from exc

    # Surgically replace the agent_core* packages.
    try:
        install_wheels(wheel_paths, venv_python=venv_python)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]wheel install failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(code=1) from exc

    # Stamp it.
    version = tag.removeprefix("v")
    stamp = InstallStamp(
        installed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        installed_sha=_git_sha_of_tag(tag),  # best-effort, falls back to "unknown"
        installed_version=version,
        python_version="3.12",  # daemon Python is pinned
        extra=None,
        release_tag=tag,
    )
    write_stamp(home, stamp)

    console.print(f"[green]daemon updated to {tag}[/green]")
```

Add a private helper near `_daemon_python`:
```python
def _daemon_python_path() -> Path:
    """The fixed path to the daemon venv's python, regardless of existence."""
    if sys.platform == "win32":
        return _home() / ".venv" / "Scripts" / "python.exe"
    return _home() / ".venv" / "bin" / "python"


def _git_sha_of_tag(tag: str) -> str:
    """Best-effort: resolve a tag to a git short sha. Returns 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", tag],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"
```

Update the existing `install` import list to no longer import `find_workspace_root`, `compute_lock_hash`, `run_install` (those are gone — handled in Task 8).

Update the `refresh` command:
```python
@app.command()
def refresh(
    release: str | None = typer.Option(
        None,
        "--release",
        help="Release tag to install (e.g. v0.1.0). Default: latest release.",
    ),
) -> None:
    """Stop daemon → install release artifacts → start daemon."""
    stop()
    install(release=release)
    start()
```

(`extra` and `python_version` options on `refresh` are gone — they were source-install-era options.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_release.py::test_install_release_invokes_full_chain -v`
Expected: PASS.

- [ ] **Step 5: Run the broader daemon CLI tests**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_install.py packages/core/tests/test_daemon_status_version.py -v`
Expected: Some failures expected (Task 8 deletes the source-install code paths these test). Note which fail — they'll be fixed in Task 8.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_release.py
git commit -m "feat(daemon): --release flag for install + refresh (artifact-based)"
```

---

## Task 8: Remove source-based install code

**Files:**
- Modify: `packages/core/src/agent_core/daemon/install.py` — delete `run_install`, `build_uv_sync_command`, `find_workspace_root`, `compute_lock_hash`, related exceptions
- Modify: `packages/core/tests/test_daemon_install.py` — remove obsolete tests
- Modify: `packages/core/src/agent_core/daemon/cli.py` — remove now-orphaned imports

- [ ] **Step 1: Read the current install.py to identify what to delete**

Run: `grep -n "^def\|^class" packages/core/src/agent_core/daemon/install.py`

This lists every function/class. Mark for deletion:
- `WorkspaceNotFoundError` (class)
- `find_workspace_root` (function)
- `build_uv_sync_command` (function)
- `UvNotFoundError` (class)
- `_git_head_sha` (function — used by run_install only)
- `compute_lock_hash` (function)
- `run_install` (function)

Keep:
- `STAMP_FILENAME` constant
- `InstallStamp` dataclass
- `write_stamp` / `read_stamp` (cli.py uses these)

- [ ] **Step 2: Delete the source-install code in install.py**

After this step, `packages/core/src/agent_core/daemon/install.py` should look like:
```python
"""Install stamp read/write — single-responsibility, no I/O beyond filesystem.

`cli.py` writes a stamp after each successful `daemon install --release`.
The stamp tells subsequent `daemon status` invocations what version is
currently deployed.

Source-based install was removed in Phase 2.5: the daemon is now updated
exclusively from GitHub Release artifacts (see daemon/release.py).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


STAMP_FILENAME = ".daemon-install-stamp.json"


@dataclass(frozen=True)
class InstallStamp:
    """Captures what was installed into the daemon venv, when, and from where."""

    installed_at: str            # ISO 8601 UTC
    installed_sha: str           # git rev-parse HEAD / tag sha at install time
    installed_version: str       # human-readable version
    python_version: str          # e.g. "3.12.5"
    extra: str | None            # uv extra name, or None
    release_tag: str | None      # GH release tag (provenance)


def write_stamp(home: Path, stamp: InstallStamp) -> None:
    """Write the install stamp to <home>/.daemon-install-stamp.json."""
    home.mkdir(parents=True, exist_ok=True)
    (home / STAMP_FILENAME).write_text(
        json.dumps(asdict(stamp), indent=2) + "\n",
        encoding="utf-8",
    )


def read_stamp(home: Path) -> InstallStamp | None:
    """Read the install stamp. Forward-compatible: unknown fields dropped,
    missing new fields default to None / 'unknown'."""
    path = home / STAMP_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return InstallStamp(
            installed_at=data["installed_at"],
            installed_sha=data["installed_sha"],
            installed_version=data.get("installed_version", "unknown"),
            python_version=data["python_version"],
            extra=data.get("extra"),
            release_tag=data.get("release_tag"),
        )
    except (KeyError, TypeError):
        return None
```

(All other functions/classes deleted.)

- [ ] **Step 3: Clean up orphaned imports in cli.py**

In `packages/core/src/agent_core/daemon/cli.py`, the import block at the top currently is:
```python
from agent_core.daemon.install import (
    UvNotFoundError,
    WorkspaceNotFoundError,
    compute_lock_hash,
    find_workspace_root,
    read_stamp,
    run_install,
)
```

Replace with:
```python
from agent_core.daemon.install import (
    InstallStamp,
    read_stamp,
    write_stamp,
)
```

Search the rest of `cli.py` for any remaining usages of the deleted names (`UvNotFoundError`, `WorkspaceNotFoundError`, `compute_lock_hash`, `find_workspace_root`, `run_install`) and remove the blocks that reference them. Particular spots:
- The `install` command's exception handlers for `UvNotFoundError` / `WorkspaceNotFoundError` — removed when Task 7 rewrote the install body.
- The `status` command's lock-drift check (a `try/except WorkspaceNotFoundError` block).

- [ ] **Step 4: Delete obsolete tests in test_daemon_install.py**

The file `packages/core/tests/test_daemon_install.py` likely contains tests for `find_workspace_root`, `build_uv_sync_command`, `run_install`, `compute_lock_hash`. Delete every test function that references any of those.

Keep any tests for `write_stamp` / `read_stamp`.

If after deletion the file has zero tests, delete the file:
```bash
git rm packages/core/tests/test_daemon_install.py
```

If some tests remain, leave the file.

- [ ] **Step 5: Run the full test suite to confirm green**

Run: `just check`
Expected: PASS. Lint, typecheck, contracts, tests all green.

If lint complains about unused imports — clean them up.
If mypy complains about now-unreferenced types — clean them up.
If a contract test (import-linter) fails because a forbidden import is now reachable, investigate and fix.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/install.py \
        packages/core/src/agent_core/daemon/cli.py \
        packages/core/tests/test_daemon_install.py
git commit -m "refactor(daemon): remove source-based install path (replaced by --release)"
```

(If `test_daemon_install.py` was deleted entirely, use `git rm` instead of `git add` for it.)

---

## Task 9: Fix B2 (false-positive "fallback" warning)

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py:160-170` (the `status` command's fallback warning)
- Test: `packages/core/tests/test_daemon_status_b2.py`

- [ ] **Step 1: Write the regression test (failing)**

Write to `packages/core/tests/test_daemon_status_b2.py`:
```python
"""B2 regression: `daemon status` must not print the 'fallback —
vulnerable to uv sync' warning when invoked from inside the daemon venv
(or anywhere else), as long as the daemon venv exists.

See Phase 2.5 design §3.7."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.daemon.cli import app as daemon_app


def test_status_no_false_positive_fallback_when_daemon_venv_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ~/.agent-core/.venv/Scripts/python.exe (or bin/python) exists,
    `status` must NOT include the word 'fallback' in its output —
    regardless of which python is invoking the CLI."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    # Create the daemon venv's python on disk
    import sys
    if sys.platform == "win32":
        py = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        py = tmp_path / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")

    runner = CliRunner()
    result = runner.invoke(daemon_app, ["status"])

    # Daemon isn't actually running, so we expect an early-return path,
    # but the bug is specifically about the "running from" line's
    # decoration — make sure 'fallback' never appears.
    assert "fallback" not in result.output, (
        "B2 regression: 'fallback' should not appear in status output "
        "when daemon venv exists. Output:\n" + result.output
    )


def test_status_fallback_warning_only_when_no_daemon_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Conversely: if no daemon venv exists, the warning SHOULD appear
    (this is the legitimate case the warning was designed for)."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Note: tmp_path / .venv does not exist — that's the point.

    # We need to run status against a "live" daemon for the "running from:"
    # line to be reached. Easier: assert that the helper _daemon_venv_exists
    # returns False here.
    from agent_core.daemon.cli import _daemon_venv_exists
    assert _daemon_venv_exists() is False
```

- [ ] **Step 2: Run to verify the first test fails (the regression test)**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_status_b2.py::test_status_no_false_positive_fallback_when_daemon_venv_exists -v`
Expected: FAIL — the current `cli.py:164` check incorrectly fires.

(The second test, `test_status_fallback_warning_only_when_no_daemon_venv`, will fail at import because `_daemon_venv_exists` doesn't exist yet — that's expected.)

- [ ] **Step 3: Add the helper + fix the check**

In `packages/core/src/agent_core/daemon/cli.py`, add the helper near `_daemon_python`:
```python
def _daemon_venv_exists() -> bool:
    """True iff the daemon venv's python interpreter exists on disk.

    Used by `daemon status` to decide whether to warn the user that the
    daemon is running from sys.executable as a fallback. Replaces the
    old `daemon_py == sys.executable` check which produced false positives
    when the CLI itself was invoked from inside the daemon venv (B2,
    Phase 2.5 design §3.7).
    """
    if sys.platform == "win32":
        return (_home() / ".venv" / "Scripts" / "python.exe").exists()
    return (_home() / ".venv" / "bin" / "python").exists()
```

Then in the `status` command, find the block:
```python
    daemon_py = _daemon_python()
    suffix = ""
    if daemon_py == sys.executable:
        suffix = (
            " [dim red](fallback — vulnerable to uv sync; "
            "run `agent-core daemon install`)[/dim red]"
        )
    console.print(f"running from: {daemon_py}{suffix}")
```

Replace with:
```python
    daemon_py = _daemon_python()
    suffix = ""
    if not _daemon_venv_exists():
        suffix = (
            " [dim red](fallback — no daemon venv; "
            "run `agent-core daemon install`)[/dim red]"
        )
    console.print(f"running from: {daemon_py}{suffix}")
```

(Also updated the warning text: "vulnerable to uv sync" was Phase 2 language that no longer applies — the daemon venv is decoupled from `uv sync` now.)

- [ ] **Step 4: Run both tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_status_b2.py -v`
Expected: 2 tests pass.

- [ ] **Step 5: Run the broader status tests**

Run: `uv run --no-sync pytest packages/core/tests/test_daemon_status_version.py packages/core/tests/test_daemon_status_b2.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_status_b2.py
git commit -m "fix(daemon): B2 — status no longer warns 'fallback' when venv exists"
```

---

## Task 10: Retire towncrier

**Files:**
- Modify: `pyproject.toml` (root)
- Delete: `changelog.d/` (entire directory)
- Modify: `justfile`
- Modify: `uv.lock` (auto-refreshed)

- [ ] **Step 1: Verify changelog.d/ has no unconsumed fragments**

Run: `find changelog.d -type f -name '*.md' | grep -v '\.gitkeep'`
Expected: empty output (Phase 2's v0.1.0 release consumed all fragments). If anything is listed, STOP and surface — those fragments would be lost.

- [ ] **Step 2: Remove `[tool.towncrier]` from root pyproject.toml**

In `pyproject.toml`, find the `[tool.towncrier]` block (with `directory`, `filename`, `start_string`, `underlines`, `title_format`, `issue_format`, the `[[tool.towncrier.type]]` entries, and the `[[tool.towncrier.section]]` entries). Delete the entire block — everything from `[tool.towncrier]` up to (but not including) whatever section follows.

- [ ] **Step 3: Remove towncrier dev dependency**

In `pyproject.toml`, find the dev dependency list. It will look something like:
```toml
[dependency-groups]
dev = [
    ...,
    "towncrier>=...",
    ...,
]
```

Remove the `"towncrier>=..."` line.

- [ ] **Step 4: Delete the changelog.d/ directory**

```bash
git rm -r changelog.d/
```

- [ ] **Step 5: Refresh uv.lock**

Run: `uv lock`
Expected: lockfile updates (towncrier + its transitive deps removed).

- [ ] **Step 6: Remove the `release` recipe from justfile**

In `justfile`, find:
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

Delete the entire recipe.

- [ ] **Step 7: Verify `just check` still passes**

Run: `just check`
Expected: PASS.

If there are leftover towncrier-related test files in `packages/core/tests/` (e.g., `test_towncrier_config.py`), delete them too:
```bash
git rm packages/core/tests/test_towncrier_config.py
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock justfile
# git rm already staged the deletions
git commit -m "chore(release): retire towncrier (replaced by release-please)"
```

---

## Task 11: Update docs/setup/releases.md

**Files:**
- Modify: `docs/setup/releases.md`

- [ ] **Step 1: Replace the file's contents**

Write to `docs/setup/releases.md`:
````markdown
# Releasing

Versions are **VCS-derived** (`uv-dynamic-versioning`): a build on a
`vX.Y.Z`-tagged commit is exactly `X.Y.Z`. Phase 2.5 introduced the
**release-artifact deploy model** — releases are built in CI and
distributed as wheel attachments on the GitHub Release.

## The release flow

```
PR opened with conventional title (feat:/fix:/chore:/etc.)
  → pr-title-lint validates
  → CI (check + integration) green
  → squash-merge via GH UI (the only allowed merge type)
  → release-please bot opens or updates a release PR labeled
    "chore(release): X.Y.Z" automatically
  → multiple PRs may merge before you ship; bot keeps the release PR fresh
  → when ready to ship: merge the release PR
  → release-please tags vX.Y.Z on the merge commit + creates GH Release
  → release.yml workflow builds wheels, attaches them to the GH Release
  → on the daemon box: `agent-core daemon refresh` (no args = latest)
```

## One-time repo configuration (already done)

These were set up during the Phase 2.5 land:
- Repo settings: **only squash-merge enabled** (merge commits + rebase disabled)
- `phase1-main-gate` ruleset: `pr-title-lint` is a required check

## Writing PRs

PR title must match Conventional Commits:
```
feat(daemon): support custom port
fix(busproxy): handle reconnect after daemon restart
chore(deps): bump uv to 0.7.14
docs(setup): clarify deployment steps
```

Supported types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`,
`style`, `build`, `ci`, `perf`, `revert`.

Scopes (the `(...)` part) are optional. Lowercase subjects (the part
after the colon) are enforced.

The branch's individual commit messages don't matter — squash-merge
collapses them into one commit whose message becomes the PR title.

## Cutting a release

You don't run any command to cut a release. Just merge the bot's release PR.

If you want to inspect or override the version bump release-please
proposes, edit the release PR's contents (the bot honors manual edits
on the next push).

## Deploying

```
agent-core daemon refresh                # latest release
agent-core daemon refresh --release v0.1.0   # specific version (rollback)
```

`refresh` does: `stop` → fetch wheels from GH Release → install with
`uv pip install --force-reinstall --no-deps` → `start`.

Verify:
```
agent-core daemon status
# expect: installed version: X.Y.Z
```

## Rollback

```
agent-core daemon refresh --release v0.1.0
```

Local cache at `~/.agent-core/releases/<tag>/` means re-installing a
previously-used version is offline-fast (no fetch).
````

- [ ] **Step 2: Commit**

```bash
git add docs/setup/releases.md
git commit -m "docs(setup): rewrite releases.md for release-please + artifact deploy"
```

---

## Task 12: Bundle the .mcp.json cleanup

**Files:**
- Stage: `.mcp.json` (workspace) — already modified in working tree from prior session

- [ ] **Step 1: Verify the current state of .mcp.json matches the expected cleanup**

Run: `cat .mcp.json`
Expected:
```json
{
  "mcpServers": {}
}
```

If anything else, STOP and surface — the file's content is not what's expected.

- [ ] **Step 2: Stage and commit**

```bash
git add .mcp.json
git commit -m "chore(mcp): clear workspace project-scope MCPs (moved to per-agent configs)"
```

---

## Task 13: Final whole-PR validation

**Files:** none — this is a verification task

- [ ] **Step 1: Run the full check gate locally**

Run: `just check`
Expected: PASS — all lint, typecheck, contracts, and tests green.

- [ ] **Step 2: Inspect the commit history before pushing**

Run: `git --work-tree=. log origin/main..HEAD --oneline`
Expected: 12 commits, one per Task 1-12. All with conventional commit messages.

- [ ] **Step 3: Push the branch + open the PR**

```bash
git push -u origin feat/phase25-release-artifacts
```

(The pre-push hook runs `just check`; should be green from Step 1.)

Then open the PR via:
```bash
gh pr create --title "feat(release): Phase 2.5 — release-artifact deploy + bug cleanup" \
  --body-file - <<'EOF'
## Summary
- Adopt release-please + squash-merge for version/changelog management
- New `release.yml` workflow builds wheels in CI on `release: published`, uploads to GH Release attachments
- New `daemon install --release` flag fetches + installs from GH Release; default `install` installs latest
- Source-based daemon install path removed entirely (eliminates B1 and the Windows file-lock thrash class of problems)
- Fix B2: `daemon status` no longer prints false-positive "fallback" warning when CLI is invoked from inside the daemon venv
- Retire Phase 2 towncrier setup (replaced by release-please)
- Clear workspace `.mcp.json` (notify moved to per-agent configs)

Spec: `docs/superpowers/specs/2026-05-20-phase25-release-artifacts-design.md`
Plan: `docs/superpowers/plans/2026-05-20-phase25-release-artifacts.md`

## Test plan
- [ ] All Phase 1 CI checks green (ubuntu + windows + integration)
- [ ] pr-title-lint passes (this PR title is conventional)
- [ ] After merge: one-time GH UI actions (squash-only merge in repo settings; add `pr-title-lint` to phase1-main-gate ruleset)
- [ ] After merge: release-please bot opens a release PR within ~1 minute of merge
- [ ] After merging the release PR: `release.yml` builds 10 wheels and attaches them
- [ ] On the daemon box: `agent-core daemon refresh` → `daemon status` shows the new `installed version`
EOF
```

- [ ] **Step 4: Wait for CI; resolve any failures**

If CI fails, fix the issue (likely a missed SHA pin, yaml syntax, or test that didn't get migrated correctly). Recommit and let CI re-run. Do NOT bypass the gate.

- [ ] **Step 5: After CI green: squash-merge via GH UI**

(Once Task 13 lands, GH repo settings should be flipped to squash-only — but for THIS PR, since the setting hasn't been flipped yet, choose squash-and-merge manually.)

- [ ] **Step 6: Post-merge one-time GH UI actions**

These must be done by the human (require GH UI access):
1. **Repo Settings → General → Pull Requests:** uncheck "Allow merge commits" and "Allow rebase merging." Leave only "Allow squash merging."
2. **Repo Settings → Branches → phase1-main-gate ruleset → Required status checks:** add `validate` (the job name from `pr-title-lint.yml`). Existing required checks (`check (ubuntu-latest)`, `check (windows-latest)`, `integration`) stay.

- [ ] **Step 7: Verify release-please opens a release PR**

After step 6, the next push to main (which is the Phase 2.5 merge itself) should cause release-please to open a release PR titled `chore(release): 0.2.0` (or similar, depending on the conventional commits in this PR).

If no PR appears within ~2 minutes, check the `release-please` workflow run in the GH Actions tab; the most common failure is a permissions issue or a config typo.

- [ ] **Step 8: Cut the inaugural v0.2.0 release**

Merge the release PR via GH UI. Confirm:
- A `v0.2.0` tag is created on the merge commit.
- A GH Release page exists at `https://github.com/jeffrichley/agent_core/releases/tag/v0.2.0`.
- The `release.yml` workflow runs and uploads 10 `.whl` files.

- [ ] **Step 9: Deploy v0.2.0 to the daemon box**

On the daemon box:
```bash
agent-core daemon refresh
# Expected output: "daemon updated to v0.2.0", then "daemon started (PID: ...)"

agent-core daemon status
# Expected to include:
#   installed version: 0.2.0
#   installed sha: <merge-commit-sha>
#   release_tag: v0.2.0 (if you've added stamp.release_tag to status output — optional polish)
```

Confirm Pepper and Wren remain connected (4 ESTABLISHED connections to 8789).

---

## Self-review check

After plan creation, spec coverage was verified:

- §1 Goal — covered by overall task structure (CI-built artifacts replace source rebuild)
- §2 Architecture — Tasks 1-4 (CI infrastructure), Task 5+7 (client side)
- §3.1-3.3 release-please bot — Tasks 1, 2
- §3.4 release.yml — Task 4
- §3.5 pr-title-lint.yml — Task 3
- §3.6 daemon/release.py — Task 5
- §3.7 daemon/cli.py --release + B2 — Tasks 7 (flag), 8 (source removal), 9 (B2)
- §3.8 stamp schema — Task 6
- §3.9 justfile cleanup — Task 10
- §3.10 towncrier retirement — Task 10
- §3.11 .mcp.json cleanup — Task 12
- §3.12 ruleset extension — Task 13 step 6 (one-time GH UI action)
- §3.13 squash-only repo setting — Task 13 step 6 (one-time GH UI action)
- §6 Risks — handled inline (the docs/setup/releases.md rewrite captures the human-facing mitigations)
- §8 Uncertainties — U1-U3 verified via context7 and baked into Tasks 1-4. U4 SHA resolved at Task 3 Step 1 via `gh api`. U5 (already empirically verified today) underpins Task 7.

No spec section is uncovered.
