# Phase 2.6 — End-to-end install validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec:** `docs/superpowers/specs/2026-05-23-phase26-end-to-end-install-validation-design.md` (commit `890dae5`).
>
> **Branch:** `feat/phase26-end-to-end-install-validation` (worktree at `.worktrees/phase26-end-to-end-install-validation/`).

**Goal:** Fix the two release-pipeline bugs Phase 3.5's test instance surfaced on its first install attempt against `v0.2.0` (missing CUDA index for torch+cu130 resolution; workspace-relative editable paths in the exported requirements.txt).

**Architecture:** Two surgical fixes in two files. (1) Add `[tool.uv.index]` + `[tool.uv.sources]` to `pyproject.toml` so the cu130 PyTorch index is configured workspace-wide — both `uv export` (in `release.yml`) and `uv pip install` (in `release.py`) pick it up automatically. (2) Add `--no-emit-workspace` to `release.yml`'s `uv export` step so the generated `requirements.txt` carries only third-party deps. `release.py` is unchanged in the expected path; one-line fallback if pyproject config doesn't propagate to install. Two unit tests on config/output shape; manual end-to-end validation via the Phase 3.5 test instance documented in the PR.

**Tech Stack:** Python 3.12, uv workspace, pytest, ruff, mypy, GitHub Actions.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `pyproject.toml` (root) | Add `[[tool.uv.index]]` + `[tool.uv.sources]` for cu130 PyTorch index | Modify |
| `packages/core/tests/test_pyproject_uv_index.py` | NEW — assert pyproject's `[tool.uv]` config has cu130 index properly bound | Create |
| `.github/workflows/release.yml` | Add `--no-emit-workspace` to the `uv export` step | Modify |
| `packages/core/tests/test_release_export.py` | NEW — assert `uv export --no-emit-workspace` against a fixture workspace produces no `-e ./packages/...` lines | Create |
| `packages/core/src/agent_core/daemon/release.py` | NO CHANGES (modulo fallback path for Fix 1 if pyproject config doesn't propagate to install) | Conditional modify |

---

## Phase 1 — Fix 1: cu130 index in pyproject.toml

### Task 1: Add `[[tool.uv.index]]` + `[tool.uv.sources]` config + test

**Files:**
- Modify: `pyproject.toml`
- Create: `packages/core/tests/test_pyproject_uv_index.py`

- [ ] **Step 0 (preflight): read current `pyproject.toml`'s uv config**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
grep -B1 -A20 "\[tool.uv\]\|\[tool.uv.workspace\]\|\[tool.uv.sources\]\|\[tool.uv.index\]\|\[\[tool.uv.index\]\]" pyproject.toml | head -40
uv --version
```

Note: the existing `[tool.uv.workspace]` section is intact; new additions slot in without disturbing it. Also note the uv version — the exact schema for `[tool.uv.sources]` + `[[tool.uv.index]]` evolved across uv versions. Confirm via `uv help` or recent uv docs (`https://docs.astral.sh/uv/concepts/projects/dependencies/`) that the schema below matches the installed uv version. If schema differs, adjust accordingly.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_pyproject_uv_index.py`:

```python
"""Tests for pyproject.toml's [tool.uv] configuration.

Phase 2.6: assert the cu130 PyTorch index is configured at the workspace
level so both `uv export` (in release.yml) and `uv pip install` (in
release.py) pick it up automatically. The bug Phase 3.5's test instance
surfaced: install couldn't find torch==2.12.0+cu130 because the cu130
index wasn't reachable from any config layer uv consults.
"""

from pathlib import Path

import tomllib


PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def test_pyproject_declares_pytorch_cu130_index():
    """[[tool.uv.index]] section must declare the pytorch-cu130 index by name."""
    data = tomllib.loads(PYPROJECT.read_text())
    indices = data.get("tool", {}).get("uv", {}).get("index", [])
    assert isinstance(indices, list), "expected [[tool.uv.index]] as an array of tables"
    by_name = {idx.get("name"): idx for idx in indices}
    assert "pytorch-cu130" in by_name, (
        f"expected an index named 'pytorch-cu130'; found {sorted(by_name)}"
    )
    cu130 = by_name["pytorch-cu130"]
    assert cu130.get("url") == "https://download.pytorch.org/whl/cu130"
    # `explicit = true` means uv won't search this index by default for
    # arbitrary packages — it'll only consult it when a source binding
    # explicitly references it. Prevents accidentally pulling other
    # torch variants from the cu130 index.
    assert cu130.get("explicit") is True, (
        "pytorch-cu130 index must be marked explicit=true so uv only consults it "
        "for packages with an explicit source binding"
    )


def test_pyproject_binds_torch_to_pytorch_cu130_under_cu130_extra():
    """[tool.uv.sources] must route torch to the pytorch-cu130 index when
    the cu130 extra is active. Without this binding, the index exists
    but torch resolution still hits PyPI."""
    data = tomllib.loads(PYPROJECT.read_text())
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    torch_source = sources.get("torch")
    assert torch_source is not None, (
        "expected [tool.uv.sources] to define a 'torch' binding"
    )
    # The binding may be a single mapping or a list-of-mappings depending
    # on uv schema version. Normalize.
    bindings = torch_source if isinstance(torch_source, list) else [torch_source]
    matched = [
        b for b in bindings
        if b.get("index") == "pytorch-cu130"
        and "extra == 'cu130'" in str(b.get("marker", ""))
    ]
    assert matched, (
        f"expected at least one torch binding referencing index='pytorch-cu130' "
        f"with marker == 'extra == \\'cu130\\''; found {bindings}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
uv run pytest packages/core/tests/test_pyproject_uv_index.py -v
```

Expected: both tests FAIL — the `[[tool.uv.index]]` section and the `[tool.uv.sources]` torch binding don't exist yet.

- [ ] **Step 3: Add the config to `pyproject.toml`**

Append to `pyproject.toml` (keep existing `[tool.uv.workspace]` etc. intact):

```toml
[[tool.uv.index]]
name = "pytorch-cu130"
url = "https://download.pytorch.org/whl/cu130"
explicit = true

[tool.uv.sources]
torch = [
  { index = "pytorch-cu130", marker = "extra == 'cu130'" },
]
```

If `[tool.uv.sources]` already exists, merge the `torch = [...]` binding into the existing block rather than creating a duplicate section.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
uv run pytest packages/core/tests/test_pyproject_uv_index.py -v
```

Expected: both tests PASS.

Then run uv lock to confirm the config is internally consistent (no resolution errors):

```bash
uv lock --check
```

Expected: clean (lockfile up-to-date OR cleanly regeneratable). If uv lock complains about a schema issue, the `[tool.uv.sources]` / `[[tool.uv.index]]` syntax needs adjustment for the installed uv version.

- [ ] **Step 5: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
git add pyproject.toml packages/core/tests/test_pyproject_uv_index.py
git commit -m "feat(release): add pytorch-cu130 uv index + torch source binding (Phase 2.6)"
```

If `uv lock` regenerated `uv.lock`, stage it too: `git add uv.lock`.

**FALLBACK (only if Step 4's `uv lock` shows the pyproject config doesn't propagate to `uv pip install` for any reason):** add `--extra-index-url=https://download.pytorch.org/whl/cu130` to `release.py:install_requirements`'s `cmd` list. In that case, swap the tests above for tests on the `install_requirements` command shape (mock subprocess.run, assert the captured command includes the flag). Report the fallback path in your status report so the controller knows the primary path didn't work.

---

## Phase 2 — Fix 2: --no-emit-workspace in release.yml

### Task 2: Add `--no-emit-workspace` flag + test

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `packages/core/tests/test_release_export.py`

- [ ] **Step 0 (preflight): find the uv export step + verify the flag name**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
grep -n "uv export" .github/workflows/release.yml
uv export --help 2>&1 | grep -A2 -i "workspace\|no-emit\|emit"
```

Confirm the actual flag name uv accepts. The expected name is `--no-emit-workspace`; recent uv may have it as `--no-emit-package` or similar. Use what `uv export --help` shows.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_release_export.py`:

```python
"""Test that `uv export` (as invoked from release.yml) produces a
requirements.txt that does NOT contain editable workspace-member entries.

Phase 2.6: the bug Phase 3.5's test instance surfaced (Bug 2) was that
the generated requirements.txt contained lines like `-e ./packages/...`
that don't resolve on the daemon side. The workspace packages ship via
wheels; requirements.txt should carry only third-party deps.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _make_fixture_workspace(root: Path) -> None:
    """Build a minimal uv workspace under `root` with one member package."""
    (root / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture-root"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = [\n'
        '  "fixture-member",\n'
        '  "annotated-types>=0.7.0",\n'  # one real third-party dep for control
        ']\n'
        '\n'
        '[tool.uv.workspace]\n'
        'members = ["packages/fixture-member"]\n'
        '\n'
        '[tool.uv.sources]\n'
        'fixture-member = { workspace = true }\n'
        '\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    )
    member = root / "packages" / "fixture-member"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture-member"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = []\n'
        '\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    )
    src = member / "src" / "fixture_member"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("__version__ = '0.0.0'\n")


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv binary not on PATH"
)
def test_uv_export_no_emit_workspace_excludes_member_packages(tmp_path):
    """`uv export --no-emit-workspace` against a fixture workspace must
    produce a requirements-txt that does NOT contain `-e ./packages/...`
    (or any other editable reference to workspace members)."""
    _make_fixture_workspace(tmp_path)

    result = subprocess.run(
        [
            "uv", "export",
            "--frozen", "--no-dev", "--no-hashes",
            "--no-emit-workspace",
            "--format", "requirements-txt",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    # If --frozen complains about a missing lockfile in a fresh fixture,
    # drop --frozen and re-run; this fixture doesn't ship with a lockfile.
    if result.returncode != 0 and "lock" in (result.stderr or "").lower():
        result = subprocess.run(
            [
                "uv", "export",
                "--no-dev", "--no-hashes",
                "--no-emit-workspace",
                "--format", "requirements-txt",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, (
        f"uv export failed: stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    requirements = result.stdout

    # Core assertion: no editable lines pointing at workspace members.
    bad = [line for line in requirements.splitlines() if line.startswith("-e ./packages/")]
    assert not bad, (
        f"requirements.txt contained workspace-member editable lines: {bad}\n"
        f"Full output:\n{requirements}"
    )

    # Sanity: the third-party dep we DID declare should still be present.
    assert "annotated-types" in requirements, (
        f"expected third-party dep 'annotated-types' in output; got:\n{requirements}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
uv run pytest packages/core/tests/test_release_export.py -v
```

Expected outcomes:
- If `--no-emit-workspace` is a valid uv flag in this version: test should PASS once `--no-emit-workspace` is in the command (the test itself enforces the right command). The "fail" state is "you wrote the test but `uv` doesn't accept the flag" — surfaces with a non-zero exit + "unrecognized argument" stderr.
- If the flag is actually called something else: test fails; adjust the flag name based on `uv export --help`.

If the flag doesn't exist in this uv version at all, escalate to the controller (per the spec's fallback ordering — pyproject-level workspace exclusion, or grep post-process as last resort).

- [ ] **Step 3: Add `--no-emit-workspace` to `.github/workflows/release.yml`**

Find the line that invokes `uv export` in the release workflow. Current shape (per the v0.2.0 release artifact's header):

```yaml
- name: Export requirements
  run: |
    uv export --frozen --no-dev --extra cu130 --no-hashes --format requirements-txt > dist/requirements.txt
```

Add `--no-emit-workspace`:

```yaml
- name: Export requirements
  run: |
    uv export --frozen --no-dev --extra cu130 --no-emit-workspace --no-hashes --format requirements-txt > dist/requirements.txt
```

If the actual flag name differs (per preflight Step 0), use that. If the actual step is structured differently in the file, adapt the patch to match.

- [ ] **Step 4: Re-run the test to confirm it passes**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
uv run pytest packages/core/tests/test_release_export.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
git add .github/workflows/release.yml packages/core/tests/test_release_export.py
git commit -m "feat(release): uv export --no-emit-workspace excludes workspace members (Phase 2.6)"
```

---

## Phase 3 — End-to-end validation + verification + PR

### Task 3: Local end-to-end install validation + ruff + mypy + full sweep + open PR

**Files:** No code changes — verification + PR open.

This task does the work the spec named as "the demand-validated surface where it earns its keep." It stands up the install path locally (without needing to cut a v0.3.0 release) and proves the fixes work end-to-end. Document the run in the PR description.

- [ ] **Step 1: Build wheels locally from this branch**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
rm -rf dist/
uv build --all-packages --wheel --out-dir dist/
ls -la dist/
```

Expected: ~10 `.whl` files for the workspace packages. Capture the file list for the PR description.

- [ ] **Step 2: Export the corrected requirements.txt**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
uv export --frozen --no-dev --extra cu130 --no-emit-workspace --no-hashes --format requirements-txt > dist/requirements.txt
echo "First 20 lines:"
head -20 dist/requirements.txt
echo "Editable workspace lines (should be empty):"
grep "^-e ./packages/" dist/requirements.txt || echo "  (none — good)"
echo "torch line:"
grep -i "^torch==" dist/requirements.txt | head -3
```

Expected: no `-e ./packages/...` lines; torch is pinned to a `+cu130` version. Capture the relevant lines for the PR description.

- [ ] **Step 3: Stand up a sandbox install**

```bash
SANDBOX=/tmp/agent-core-phase26-validation
rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/releases/vlocal"
cp dist/*.whl "$SANDBOX/releases/vlocal/"
cp dist/requirements.txt "$SANDBOX/releases/vlocal/"

# Create the venv
uv venv "$SANDBOX/.venv" --python 3.12

# Install third-party deps via the corrected requirements.txt — exercises Bug 1 fix
uv pip install \
  --python "$SANDBOX/.venv/Scripts/python.exe" \
  --requirement "$SANDBOX/releases/vlocal/requirements.txt"

# Install the agent_core wheels — same step release.py's install_wheels does
uv pip install \
  --python "$SANDBOX/.venv/Scripts/python.exe" \
  --force-reinstall --no-deps \
  "$SANDBOX/releases/vlocal"/*.whl
```

Expected: both `uv pip install` invocations succeed. The first one exercises Bug 1's fix (pyproject config → cu130 index reachable → torch resolution succeeds). The second one is the wheel-install step that release.py does last. Capture stdout/stderr counts (errors=0) for the PR description.

- [ ] **Step 4: Verify the sandbox install state**

```bash
SANDBOX=/tmp/agent-core-phase26-validation
echo "=== Installed packages ==="
uv pip list --python "$SANDBOX/.venv/Scripts/python.exe" | grep -E "agent.core|torch" | head -15
echo "=== Sanity: import agent_core ==="
"$SANDBOX/.venv/Scripts/python.exe" -c "import agent_core; print(f'agent_core import OK')"
echo "=== Sanity: torch version ==="
"$SANDBOX/.venv/Scripts/python.exe" -c "import torch; print(f'torch {torch.__version__}')"
```

Expected: all agent_core packages installed; `import agent_core` succeeds; torch reports a `+cu130` version. Capture this output for the PR description — it IS the evidence the fixes work.

- [ ] **Step 5: Tear down the sandbox**

```bash
rm -rf /tmp/agent-core-phase26-validation
```

Optional (leave it for the controller to inspect if anything surprised).

- [ ] **Step 6: ruff + mypy + full sweep**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
uv run ruff check packages/core/ pyproject.toml
uv run mypy packages/core/ 2>&1 | tail -5
uv run pytest -q 2>&1 | tail -10
```

Expected: ruff clean, mypy no new errors, full repo tests pass.

If ruff finds issues, fix and commit as `style(phase26): ruff cleanup`. If mypy finds new errors, fix and commit as `chore(phase26): mypy cleanup`.

- [ ] **Step 7: Push branch + open PR**

The PR description IS the celebration of Phase 3.5 working. Spend time on it — per controller-relayed Pepper guidance: "the story matters more than speed; the PR description IS the celebration of Phase 3.5 working. Don't rush the write-up."

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase26-end-to-end-install-validation
git push -u origin feat/phase26-end-to-end-install-validation

gh pr create \
  --base main \
  --head feat/phase26-end-to-end-install-validation \
  --title "feat(release): Phase 2.6 — end-to-end install validation" \
  --body "$(cat <<'EOF'
## Summary

Closes the two release-pipeline bugs Phase 3.5's test instance surfaced on its first install attempt against `v0.2.0`. Phase 2.6 is **the missing piece of Phase 2.5** — the validation step that proves the deploy path actually works end-to-end.

## The story

Phase 2.5 shipped a release-artifact deploy model: CI builds wheels + a requirements.txt and uploads them to a GH Release; the daemon's `refresh` command pulls those artifacts and installs them. The daemon-side install path was unit-tested with stubs but never exercised end-to-end on a real install.

Phase 3.5 shipped the test instance — a sandboxed daemon home that installs from release wheels via the SAME `release.py` code path as prod. Its first real install (against v0.2.0) failed at dependency resolution and surfaced TWO bugs that had been dormant in v0.2.0's artifacts. **Test instance worked exactly as designed: the bug got caught in a sandbox instead of in prod.**

Phase 2.6 fixes both bugs.

## What ships

**Fix 1: cu130 PyTorch index in `pyproject.toml`.** Added `[[tool.uv.index]]` for `pytorch-cu130` (URL `https://download.pytorch.org/whl/cu130`, `explicit=true`) plus `[tool.uv.sources]` binding torch to that index under the `cu130` extra. Single source of truth — both `uv export` (in release.yml) and `uv pip install` (in release.py) pick it up automatically. No `release.py` code change.

**Fix 2: `--no-emit-workspace` on `release.yml`'s uv export step.** Previously the generated requirements.txt included `-e ./packages/...` entries for workspace members — paths that don't resolve on the daemon side (where the workspace doesn't exist). With this flag, requirements.txt contains only third-party deps; the agent_core packages ship exclusively via the wheel-install step.

**Two unit tests:**
- `test_pyproject_uv_index.py` — asserts the cu130 index + source binding are well-formed in pyproject.toml.
- `test_release_export.py` — asserts `uv export --no-emit-workspace` against a fixture workspace produces no `-e ./packages/...` lines.

(Per spec: tests are about config / command shape, not resolution outcome. Resolution-success belongs to the end-to-end validation below.)

## End-to-end validation evidence

Built wheels locally from this branch, exported the corrected requirements.txt, stood up a sandbox install at `/tmp/agent-core-phase26-validation/`:

```
[Steps 1-4 stdout/stderr captured during the task — paste here, including
 the wheel count, the no-editable-lines confirmation, the torch+cu130
 version line, and the `import agent_core` success message.]
```

The install completed end-to-end. Both bugs are closed; the deploy path works.

## Out of scope (deferred follow-ups)

- **v0.2.0 artifact remediation.** Already-shipped release artifacts on GitHub are immutable. After this fix lands, `daemon install --release v0.2.0` will still fail; the fix only takes effect for v0.3.0+ releases.
- **`--from-local` CLI flag** for `daemon install --instance test` (Phase 3.5's deferred slot — still deferred; the validation above used uv directly).
- **Cross-adapter audit** for "intent diverged from implementation" docstring/code drift (would be rule-of-three; one instance is not enough to act on the class).

## Test plan

- [x] `uv run pytest packages/core/tests/test_pyproject_uv_index.py -v` — passes.
- [x] `uv run pytest packages/core/tests/test_release_export.py -v` — passes.
- [x] `uv run pytest -q` (full repo) — passes.
- [x] `uv run ruff check packages/core/ pyproject.toml` — clean.
- [x] `uv run mypy packages/core/` — no new errors.
- [x] End-to-end validation (see "End-to-end validation evidence" above) — install succeeds.

## Sequencing

Phase 2.6 should land AFTER PR #120 (Phase 3.5 — three-instance daemon), so post-merge `daemon install --instance test --release v0.3.0` becomes the standing automated demo of this fix in action.

PRs at the release gate after this lands: #119 (cliché detector), #110 (Phase 4 autostart), #120 (Phase 3.5), Phase 2.6. Awaiting Jeff's home-window for the deploy.
EOF
)"
```

The placeholder `[Steps 1-4 stdout/stderr captured during the task — paste here, ...]` is the ONE thing you need to fill in by hand from the actual outputs you captured in Steps 1–4. Replace with the real captured strings before opening the PR. Do not leave the placeholder in the published PR body.

- [ ] **Step 8: Status report**

Report `STATUS: DONE` with:
- All commit SHAs that landed.
- Final test count.
- ruff result, mypy result.
- PR URL.
- Whether the primary path (pyproject config) or the fallback (`--extra-index-url` in release.py) was taken — and why.
- Anything surprising about the end-to-end validation.

Controller (not this subagent) handles the Pepper ping post-PR.
