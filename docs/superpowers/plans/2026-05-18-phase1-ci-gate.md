# Phase 1 — CI Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a greenfield CI gate (GitHub Actions + version-controlled pre-push hook + `main` branch ruleset) so `main` is never red and CI failures stay rare-but-loud.

**Architecture:** A `just install-hooks` recipe wraps a unit-tested pure function (`agent_core.githooks.install_git_hooks`) that points the clone's `core.hooksPath` at a version-controlled `.githooks/pre-push` which runs `just check`. A `.github/workflows/ci.yml` runs a fast `check` matrix (`ubuntu`+`windows`) and a Windows-only `integration` job that runs the self-contained slow suite (`pytest packages/core/tests -m slow`) with a Torch-free sync. A `main` branch ruleset (created via `gh api`) requires all three contexts green. All third-party actions are SHA-pinned.

**Tech Stack:** GitHub Actions, `uv`, `just`, `git` hooks, Typer/Python (core package), `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-05-18-phase1-ci-gate-design.md` (refines §Phase 1 of `2026-05-18-agent-core-maturity-design.md`).

**Branch:** all work on `feat/phase1-ci-gate` off `main`. Do NOT implement on `main`.

**Resolved action pins (authoritative, fetched 2026-05-18 via `gh api`):**
- `actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd` # v6.0.2
- `astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b` # v8.1.0
- `extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3` # v4

**Repo facts the implementer needs:**
- `owner/repo` = `jeffrichley/agent_core`; default branch `main`.
- `justfile` already defines `check: lint typecheck contracts test`; `pyproject.toml` `addopts = "--import-mode=importlib -m 'not slow'"`. A CLI `-m slow` overrides the addopts `-m 'not slow'` (later `-m` wins) — this is how the slow suite is selected.
- The ONLY `slow`-marked module is `packages/core/tests/test_daemon_install_integration.py` (`pytestmark = pytest.mark.slow`); it is self-contained (builds its own temp workspace, calls real `uv`/`git`). No supervised daemon, no `agent_core.yaml`, no port. Scoping pytest to `packages/core/tests` avoids collecting Torch-heavy `packages/agent-core-voice/tests`.
- `torch`/`torchaudio` are **extra-only** (`agent-core-voice` `[project.optional-dependencies] cpu`/`cu130`); never base deps. The vendored `qwen-tts` (`vendor/Qwen3-TTS`, `editable=false`) may pull Torch as its own base dep — hence the integration job must sync narrowly (Task 5).
- `set windows-shell := ["cmd.exe", "/c"]` in the justfile: recipe lines run under `cmd.exe` on Windows, so recipe commands must be cmd-safe (`uv run ... python -m ...` is).
- The core CLI is Typer (`packages/core/src/agent_core/cli.py`), pattern: pure logic module + thin wrapper (e.g. `daemon/install.py` vs `daemon/cli.py`). `agent_core.githooks` follows the same split.

---

### Task 1: `install_git_hooks` pure function + unit tests

**Files:**
- Create: `packages/core/src/agent_core/githooks.py`
- Test: `packages/core/tests/test_githooks.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_githooks.py`:

```python
"""Unit tests for agent_core.githooks.install_git_hooks (temp git repos only)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_core.githooks import HOOKS_DIR_NAME, HookInstallError, install_git_hooks


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def _make_hooks(repo: Path) -> Path:
    hooks = repo / HOOKS_DIR_NAME
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return hooks


def test_install_sets_hookspath_to_githooks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _make_hooks(repo)

    returned = install_git_hooks(repo)

    assert returned == hooks.resolve()
    assert _git(repo, "config", "--get", "core.hooksPath") == HOOKS_DIR_NAME


def test_install_is_idempotent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _make_hooks(repo)

    install_git_hooks(repo)
    install_git_hooks(repo)  # second run must not raise

    assert _git(repo, "config", "--get", "core.hooksPath") == HOOKS_DIR_NAME


def test_install_raises_when_pre_push_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / HOOKS_DIR_NAME).mkdir()  # dir exists but pre-push absent

    with pytest.raises(HookInstallError, match="pre-push"):
        install_git_hooks(repo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest packages/core/tests/test_githooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core.githooks'`.

- [ ] **Step 3: Implement the module**

Create `packages/core/src/agent_core/githooks.py`:

```python
"""Install this clone's version-controlled git hooks (.githooks/).

`just install-hooks` wraps `main()`. Logic lives here (not in the recipe)
so it is directly unit-testable, mirroring daemon/install.py vs
daemon/cli.py.

A relative `core.hooksPath` of ".githooks" is resolved by git relative
to the working-tree root at hook-trigger time, so it is correct for the
main checkout and every linked worktree (each has its own committed
.githooks/ directory).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HOOKS_DIR_NAME = ".githooks"
REQUIRED_HOOKS = ("pre-push",)


class HookInstallError(Exception):
    """Raised when the versioned hooks directory is missing or incomplete."""


def install_git_hooks(repo_root: Path) -> Path:
    """Point `repo_root`'s git at `<repo_root>/.githooks`. Idempotent.

    Returns the resolved hooks directory. Raises HookInstallError if
    `.githooks/pre-push` is absent, so a broken checkout fails loudly
    instead of silently disabling the gate.
    """
    repo_root = repo_root.resolve()
    hooks_dir = repo_root / HOOKS_DIR_NAME
    for hook in REQUIRED_HOOKS:
        if not (hooks_dir / hook).is_file():
            raise HookInstallError(
                f"missing {HOOKS_DIR_NAME}/{hook} under {repo_root} — "
                "run this from the agent_core repo root"
            )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "core.hooksPath", HOOKS_DIR_NAME],
        check=True,
    )
    return hooks_dir


def main() -> int:
    try:
        hooks_dir = install_git_hooks(Path.cwd())
    except HookInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: git config failed (exit {exc.returncode})", file=sys.stderr)
        return 1
    print(f"git hooks installed: core.hooksPath -> {HOOKS_DIR_NAME} ({hooks_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/test_githooks.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Run the fast gate**

Run: `just check`
Expected: PASS (ruff, mypy, import-linter, fast pytest all green). `mypy` covers `packages/core/src` — the new module must be clean.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/githooks.py packages/core/tests/test_githooks.py
git commit -m "feat(githooks): install_git_hooks pure function + unit tests"
```

---

### Task 2: `.githooks/pre-push` hook script

**Files:**
- Create: `.githooks/pre-push`
- Test: `packages/core/tests/test_githooks.py` (append one test)

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_githooks.py`:

```python
def test_committed_pre_push_runs_just_check_and_is_executable() -> None:
    """The real .githooks/pre-push must exist, invoke `just check`, and be
    tracked with git's executable mode (100755) so it runs on a fresh clone.
    """
    repo_root = Path(__file__).resolve().parents[3]
    hook = repo_root / ".githooks" / "pre-push"
    assert hook.is_file(), f"{hook} missing"

    body = hook.read_text(encoding="utf-8")
    assert "just check" in body

    mode = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--stage", ".githooks/pre-push"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert mode and mode[0] == "100755", f"pre-push git mode is {mode[:1]}, want 100755"
```

(`parents[3]` from `packages/core/tests/test_githooks.py` → repo root.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest packages/core/tests/test_githooks.py::test_committed_pre_push_runs_just_check_and_is_executable -v`
Expected: FAIL — assertion `.githooks/pre-push missing`.

- [ ] **Step 3: Create the hook**

Create `.githooks/pre-push` with exactly:

```sh
#!/bin/sh
# Pre-push quality gate: run the same fast gate CI runs.
# Emergency bypass (use sparingly): git push --no-verify
exec just check
```

- [ ] **Step 4: Mark it executable in git's index**

```bash
git add .githooks/pre-push
git update-index --chmod=+x .githooks/pre-push
git ls-files --stage .githooks/pre-push
```
Expected: output begins `100755`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run --no-sync pytest packages/core/tests/test_githooks.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 6: Commit**

```bash
git add .githooks/pre-push packages/core/tests/test_githooks.py
git commit -m "feat(githooks): version-controlled pre-push hook runs just check"
```

---

### Task 3: `just install-hooks` recipe + self-install

**Files:**
- Modify: `justfile` (append a recipe)

- [ ] **Step 1: Add the recipe**

Append to `justfile` (after the `sync:` recipe at EOF):

```just
# Install this clone's git hooks (.githooks/) — run once per clone/worktree
install-hooks:
    uv run --no-sync python -m agent_core.githooks
```

- [ ] **Step 2: Verify the recipe lists**

Run: `just --list`
Expected: `install-hooks` appears in the recipe list.

- [ ] **Step 3: Run it against this repo (idempotent, intended end-state)**

Run: `just install-hooks`
Expected: prints `git hooks installed: core.hooksPath -> .githooks (...)`, exit 0.

- [ ] **Step 4: Verify git now uses the versioned hook**

Run: `git config --get core.hooksPath`
Expected: `.githooks`

- [ ] **Step 5: Smoke-test the hook fires (non-destructive)**

Run: `git hook run pre-push </dev/null` (Git ≥ 2.36; on Windows Git-Bash same command)
Expected: it executes `just check` (the full fast gate runs). If `git hook run` is unavailable, instead run `sh .githooks/pre-push` and confirm `just check` runs.

- [ ] **Step 6: Commit**

```bash
git add justfile
git commit -m "feat(githooks): just install-hooks recipe"
```

---

### Task 4: CI workflow — `check` job

**Files:**
- Create: `.github/workflows/ci.yml` (this task writes the `name`/`on`/`concurrency`/`check` parts; Task 5 appends `integration`)

- [ ] **Step 1: Create the workflow with the `check` job**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          fetch-depth: 0
          fetch-tags: true
      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
        with:
          enable-cache: true
          python-version: "3.12"
      - uses: extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3  # v4
      - run: uv sync --locked --all-packages
      - run: just check
      - run: uv cache prune --ci
```

- [ ] **Step 2: Validate YAML parses**

Run: `uv run --no-sync python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output, exit 0.

- [ ] **Step 3: Lint the workflow with actionlint (if available; non-blocking)**

Run: `actionlint .github/workflows/ci.yml` (skip with a note if `actionlint` is not installed — the authoritative validation is the PR run in Task 8).
Expected: no errors, or "actionlint not installed — deferred to PR run".

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat(ci): check matrix job (ubuntu+windows, SHA-pinned actions)"
```

---

### Task 5: CI workflow — `integration` job (Torch-free, empirically pinned)

**Files:**
- Modify: `.github/workflows/ci.yml` (append the `integration` job)

**Context for the implementer:** the `integration` job must run `pytest packages/core/tests -m slow` on `windows-latest` with `uv`+`git` available and **no Torch installed**. The exact `uv sync` invocation that yields (a) `agent_core` + `pytest` + `pytest-asyncio` + `looptime` importable for collection of `packages/core/tests`, and (b) no `torch` in the env, must be determined empirically. **Use the context7 MCP server** for authoritative `uv` `sync` semantics (`--package`, `--group`, `--no-install-package`, virtual-workspace behavior) rather than relying on training data.

- [ ] **Step 1: Determine the minimal Torch-free sync (empirical)**

On a Windows shell at the repo root, try in order and pick the first that satisfies BOTH gates below:

Candidate A: `uv sync --locked --package agent-core --group dev`
Candidate B: `uv sync --locked --no-install-package agent-core-voice --no-install-package qwen-tts`
Candidate C (fallback, accepts the cost): `uv sync --locked --all-packages` (Torch-free only if the lock has no base Torch; verify gate 2 still holds)

Gate 1 (collection works):
`uv run --no-sync pytest packages/core/tests -m slow -v` → the two tests in `test_daemon_install_integration.py` run (not collection-errored).

Gate 2 (no Torch):
`uv run --no-sync python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('torch') is None else 1)"` → exit 0.

Record the winning command as `<SYNC_CMD>` for Step 2.

- [ ] **Step 2: Append the `integration` job**

Append under `jobs:` in `.github/workflows/ci.yml` (replace `<SYNC_CMD>` with the Step 1 winner; example shown uses Candidate A):

```yaml
  integration:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          fetch-depth: 0
          fetch-tags: true
      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0
        with:
          enable-cache: true
          python-version: "3.12"
      - uses: extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3  # v4
      - run: uv sync --locked --package agent-core --group dev
      - run: uv run --no-sync pytest packages/core/tests -m slow -v
```

- [ ] **Step 3: Validate YAML parses**

Run: `uv run --no-sync python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output, exit 0. Confirm both jobs (`check`, `integration`) present:
`uv run --no-sync python -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(sorted(d['jobs']))"` → `['check', 'integration']`.

- [ ] **Step 4: Run the slow suite locally with the chosen sync (proof)**

Run `<SYNC_CMD>` then `uv run --no-sync pytest packages/core/tests -m slow -v`
Expected: PASS — `test_run_install_creates_working_venv_against_minimal_workspace` and both `test_defect_a_source_only_change_is_picked_up` params pass; no Torch pulled (re-confirm Gate 2).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat(ci): windows integration job runs the self-contained slow suite (no Torch)"
```

---

### Task 6: Docs — contributor bootstrap + spec reconciliation

**Files:**
- Create: `docs/setup/ci.md`
- Modify: `docs/setup/daemon.md` (add one cross-reference line)
- Modify: `docs/superpowers/specs/2026-05-18-phase1-ci-gate-design.md` (reconcile with implemented reality)

- [ ] **Step 1: Write `docs/setup/ci.md`**

Create `docs/setup/ci.md`:

```markdown
# CI & the pre-push gate

## One-time per clone or worktree

```
just install-hooks
```

This points `core.hooksPath` at the version-controlled `.githooks/`, so
`git push` runs `just check` first. Git will not auto-run committed hook
code on a fresh clone — running `just install-hooks` once is required
(humans and agents both run this exact recipe).

Emergency bypass (use sparingly): `git push --no-verify`.

## What CI runs (`.github/workflows/ci.yml`)

- **check** — `ubuntu-latest` + `windows-latest`, `fail-fast: false`:
  `uv sync --locked --all-packages` then `just check`.
- **integration** — `windows-latest`: the self-contained slow suite
  `pytest packages/core/tests -m slow` (includes the Phase 0 stale-cache
  regression), synced Torch-free.

Triggers: every PR, every push to `main`, and manual `workflow_dispatch`.
All third-party actions are pinned by commit SHA.

## One-time GitHub setup (owner account; manual)

- Branch ruleset on `main` (see the Phase 1 plan for the exact `gh api`
  call): requires `check (ubuntu-latest)`, `check (windows-latest)`, and
  `integration` green and the branch up to date; owner on the bypass
  list; no required reviews / signed commits / linear history.
- Settings → Notifications → Actions → **"Send notifications for failed
  workflows only."** Failures email; successes are silent. Failures stay
  rare because the pre-push hook catches breakage locally.
```

- [ ] **Step 2: Cross-reference from `docs/setup/daemon.md`**

Add this line to the "## Related" section of `docs/setup/daemon.md` (create the line; keep existing content):

```markdown
- `docs/setup/ci.md` — the CI gate and the one-time `just install-hooks` bootstrap.
```

- [ ] **Step 3: Reconcile the Phase 1 spec with what shipped**

In `docs/superpowers/specs/2026-05-18-phase1-ci-gate-design.md`:
- In §3 "Job `integration`", replace the ephemeral-daemon choreography
  description with: the slow suite is self-contained, so the job is
  `windows-latest` + Torch-free sync + `pytest packages/core/tests -m slow`;
  no `agent_core.yaml`, port, or `daemon status` polling.
- In §7 flagged item 1, mark it **resolved/moot for CI** (the slow suite
  never starts the supervised daemon, so the daemon-without-`cu130`
  question does not gate CI).
- In §6, change the "executable" assertion wording to: assert the
  committed `.githooks/pre-push` is tracked with git mode `100755`
  (cross-platform-meaningful) plus the temp-repo `core.hooksPath` test.

- [ ] **Step 4: Run the fast gate (docs shouldn't break it, but confirm)**

Run: `just check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/setup/ci.md docs/setup/daemon.md docs/superpowers/specs/2026-05-18-phase1-ci-gate-design.md
git commit -m "docs(ci): contributor bootstrap + reconcile Phase 1 spec with implementation"
```

---

### Task 7: `main` branch ruleset + notification posture

**Files:** none (imperative GitHub state + recorded commands). **No code.**

> **GATED — shared-state mutation.** Creating the ruleset changes the
> live GitHub repo. The executor MUST get explicit user confirmation
> immediately before running Step 2. The notification-posture step is
> documentation only (the implementer does NOT change account settings).

- [ ] **Step 1: Confirm there is no existing ruleset to clobber**

Run: `gh api repos/jeffrichley/agent_core/rulesets`
Expected: `[]` (greenfield, as recorded in the spec). If non-empty, STOP and surface to the user.

- [ ] **Step 2: Create the ruleset (after explicit user confirmation)**

Write the payload to a temp file and POST it:

```bash
cat > /tmp/phase1-ruleset.json <<'JSON'
{
  "name": "phase1-main-gate",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "bypass_actors": [
    { "actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "check (ubuntu-latest)" },
          { "context": "check (windows-latest)" },
          { "context": "integration" }
        ]
      }
    }
  ]
}
JSON
gh api --method POST repos/jeffrichley/agent_core/rulesets \
  --input /tmp/phase1-ruleset.json
```

Notes:
- `actor_id: 5` = the built-in **admin** repository role; `bypass_mode:
  always` preserves emergency direct push for the owner.
- `strict_required_status_checks_policy: true` = branch must be up to
  date before merge.
- No `required_signatures`, `pull_request`, or `required_linear_history`
  rules — deliberately omitted per the spec.

- [ ] **Step 3: Verify the ruleset**

Run: `gh api repos/jeffrichley/agent_core/rulesets`
Expected: one ruleset `phase1-main-gate`, `enforcement: active`. Then:
`gh api repos/jeffrichley/agent_core/rulesets/<id>` and confirm the three
required contexts and the admin bypass actor are present.

- [ ] **Step 4: Record the manual notification step (no action taken)**

Confirm `docs/setup/ci.md` (Task 6) documents the "failed workflows only"
posture. The implementer does not change the owner's account settings;
this is the user's one-time manual action.

- [ ] **Step 5: No commit** (this task creates no files; the doc was committed in Task 6).

---

### Task 8: Final verification — the PR is its own acceptance test

**Files:** none.

- [ ] **Step 1: Push the branch and open the PR**

```bash
git push -u origin feat/phase1-ci-gate
gh pr create --title "Phase 1: CI gate (workflow + pre-push hook + main ruleset)" \
  --body "Implements docs/superpowers/specs/2026-05-18-phase1-ci-gate-design.md. See plan docs/superpowers/plans/2026-05-18-phase1-ci-gate.md."
```

> The push itself exercises the new `pre-push` hook (`just check` runs).
> If it blocks the push, the gate works — fix the failure, do not
> `--no-verify` unless genuinely an emergency.

- [ ] **Step 2: Watch the CI run**

Run: `gh pr checks --watch`
Expected: `check (ubuntu-latest)`, `check (windows-latest)`, and
`integration` all succeed. This is the authoritative validation of the
workflow YAML and the integration sync (Task 5) — no mocking CI.

- [ ] **Step 3: Confirm the ruleset blocks a red merge**

In the PR, confirm GitHub shows the three required checks as required and
"Merge" is gated until green (visual confirmation in `gh pr view --web`
or the merge box). Do not merge here — merging is handled by
`superpowers:finishing-a-development-branch` after final review.

- [ ] **Step 4: Report status**

Report: branch pushed, PR number, all three CI contexts green, ruleset
active and gating. Hand off to the final whole-branch review and
`superpowers:finishing-a-development-branch`.

---

## Self-Review

**Spec coverage:**
- §3 CI workflow `check` → Task 4. `integration` (revised: no choreography) → Task 5. Triggers/concurrency/SHA-pins → Task 4. ✔
- §4 pre-push hook + `just install-hooks` + distribution doc → Tasks 1–3, 6. ✔
- §5 branch ruleset (exact `gh api`) + notification posture (manual, recorded) → Task 7 + Task 6. ✔
- §6 testing: install-hooks unit test (temp repo) → Task 1; committed-hook mode/`just check` test → Task 2; "PR is its own acceptance" → Task 8. ✔
- §7 flagged item 1 → resolved/moot, reconciled in Task 6 Step 3. Flagged item 2 (`--all-packages` kept for `check`) → Task 4 uses `--all-packages` as decided. ✔
- §9 out-of-scope (no pre-commit framework, no macOS, no GPU) → respected; nothing added. ✔
- §10 one-time setup → `just install-hooks` (Task 3/6), ruleset (Task 7), notification posture (Task 6 doc). ✔

**Placeholder scan:** `<SYNC_CMD>` in Task 5 is an explicitly empirical value with two concrete acceptance gates and a context7 directive (not a vague placeholder); a worked example (Candidate A) is shown. `<id>` in Task 7 Step 3 is the ruleset id returned by Step 2's POST. Action SHAs are real (resolved via `gh api`). No "TBD/TODO/handle appropriately".

**Type consistency:** `install_git_hooks(repo_root: Path) -> Path`, `HOOKS_DIR_NAME`, `HookInstallError`, `REQUIRED_HOOKS` are defined in Task 1 and used identically in Tasks 1–3 tests and the recipe. Status-check context strings (`check (ubuntu-latest)`, `check (windows-latest)`, `integration`) match the `matrix.os` job naming in Task 4/5 and the ruleset in Task 7 and `docs/setup/ci.md` in Task 6.
