# Workspace Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `agent-core` to a `uv` workspace layout with a single member at `packages/core/`, keeping every existing capability green (CLI, tests, lint, hooks, notify MCP server). This is migration Step 1 of the monorepo design — the foundation that lets later steps carve out additional packages without breaking Pepper.

**Architecture:** Move all source from `src/agent_core/` into `packages/core/src/agent_core/`, and all tests from `tests/` into `packages/core/tests/`. Split the root `pyproject.toml`: root becomes a `[tool.uv.workspace]` declaration with shared dev tools (ruff, pytest, mypy config); `packages/core/pyproject.toml` owns the `agent-core` package's project metadata, runtime deps, scripts, and hatchling build target. Wire up `import-linter` and `towncrier` so they're configured before sibling packages arrive.

**Tech Stack:** `uv` workspace, hatchling, pytest, ruff, import-linter, towncrier.

**Spec:** [`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](../specs/2026-04-28-monorepo-workspace-design.md)

**Scope guardrails:**
- This plan is **Step 1 only** of the spec's §4 migration. Subsequent steps (carving out `agent-core-notify`, `agent-core-email`, etc., and porting Pepper subsystems) are out of scope and get their own plans.
- No code logic changes. File moves and config splits only. If you find yourself editing `*.py` for behavior reasons, you're outside the plan.
- Pepper is not touched by this plan.

---

## Conventions to follow throughout

- Run on a feature branch off `main`; do not commit directly to `main`.
- Use `git mv` for renames so git records them as renames (preserves history hints).
- Commit messages follow the repo's Conventional Commits style: `chore(workspace):`, `build(workspace):`, `docs(workspace):`, etc.
- Run linting with `uv run --no-sync ruff check .`
- Run tests with `uv run --no-sync pytest -v`
- After every code-changing commit, add a towncrier fragment (Task 6 sets this up; before that point, draft the fragment text in the commit message and add it once Task 6 lands).

---

## File structure summary

**Created in this plan:**
- `packages/core/pyproject.toml` — package metadata for `agent-core`
- `packages/core/CHANGELOG.md` — initial empty changelog
- `packages/core/towncrier.toml` — towncrier config for core
- `packages/core/changelog.d/.gitkeep` — keep the dir under version control
- `packages/core/changelog.d/<this-pr>.changed.md` — fragment for the restructure
- `.importlinter` — architecture contracts (one-package contract today; grows in later plans)

**Modified in this plan:**
- `pyproject.toml` (root) — strip `[project]`, scripts, hatch target; add `[tool.uv.workspace]`, keep shared dev tools
- `justfile` — update test/lint paths to point at the workspace
- `.gitignore` — add `packages/*/CHANGELOG.md` is *not* needed (we want it tracked); just verify nothing new must be ignored

**Moved (via `git mv`) in this plan:**
- `src/agent_core/` → `packages/core/src/agent_core/`
- `tests/` → `packages/core/tests/`

**Untouched in this plan (deliberately):**
- `agent_core.yaml` — stays at repo root; CLI's default cwd-relative lookup still resolves
- `.mcp.json` — already uses `python -m agent_core.notify.mcp_server`, still importable
- `.claude/settings.json` — already uses `uv run --no-sync agent-core hooks run X`, the script is still installed
- `docs/`, `memory-compiler/`, `scripts/`, `tests/hooks/handoff-state.json` if any state files leak — none expected
- All `*.py` source — move only, no edits

---

## Task 1: Pre-flight checks and feature branch

**Files:**
- None (read-only verification + branch creation)

- [ ] **Step 1: Verify clean working tree**

Run: `git status`
Expected: `nothing to commit, working tree clean` (or only the in-progress plan/spec docs that are about to be committed). If you see modified files unrelated to this work, stop and resolve them before proceeding.

- [ ] **Step 2: Verify on `main` and up to date**

Run: `git rev-parse --abbrev-ref HEAD && git fetch origin main && git status -sb`
Expected output contains `main` and `## main...origin/main` with no `behind`/`ahead` markers.

- [ ] **Step 3: Snapshot current passing state**

Run: `uv sync && uv run pytest -q && uv run ruff check .`
Expected: pytest reports all tests passing (baseline: **183 passed, 2 skipped**); ruff reports a known **baseline of 9 errors** (7 auto-fixable, 2 E402 import-order in `cli.py`). These errors exist on `main` today and are out of scope for this restructure plan. Success criterion is **no new errors**, not zero errors. Note both numbers — they're regression baselines for later tasks.

- [ ] **Step 4: Create the feature branch**

Run: `git checkout -b feat/workspace-restructure`
Expected: `Switched to a new branch 'feat/workspace-restructure'`.

---

## Task 2: Move the source and test trees

**Files:**
- Move: `src/agent_core/` → `packages/core/src/agent_core/`
- Move: `tests/` → `packages/core/tests/`

- [ ] **Step 1: Create the target directory structure**

Run: `mkdir -p packages/core/src`
Expected: `packages/core/src` exists. Do NOT create `packages/core/src/agent_core` — `git mv` will create that as part of the rename.

- [ ] **Step 2: Move the source tree**

Run: `git mv src/agent_core packages/core/src/agent_core`
Expected: no output. Run `git status` and confirm a list of renamed files under `packages/core/src/agent_core/...`.

- [ ] **Step 3: Move the test tree**

Run: `git mv tests packages/core/tests`
Expected: no output. `git status` shows additional renames under `packages/core/tests/...`.

- [ ] **Step 4: Remove the now-empty `src/` directory**

Run: `rmdir src` (POSIX) or `Remove-Item src -ErrorAction SilentlyContinue` (PowerShell).
Expected: directory removed. If it complains it's not empty, list contents first — only `__pycache__` should remain. Delete `src/__pycache__` if present, then retry.

- [ ] **Step 5: Verify the new structure**

Run: `ls packages/core/src/agent_core | head -10 && ls packages/core/tests | head -10`
Expected: see the same subdirectories that previously lived under `src/agent_core/` and `tests/` (bus/, hooks/, email/, notify/, etc. on the source side; bus/, hooks/, plus the test_*.py files on the test side).

- [ ] **Step 6: Commit the moves**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore(workspace): move src/agent_core and tests under packages/core

Pure relocation. No file content changes. Splits across two commits would
fragment the rename detection, so source + tests move together.

This is migration Step 1 of the monorepo workspace design (sub-project A).
EOF
)"
```

Expected: commit succeeds. Run `git log --stat -1 | head -20` and confirm the diff lists renames (R100), not deletes + adds.

---

## Task 3: Split the `pyproject.toml`

**Files:**
- Create: `packages/core/pyproject.toml`
- Modify: root `pyproject.toml` (rewrite as workspace declaration)

- [ ] **Step 1: Create `packages/core/pyproject.toml`**

This is the package-owning pyproject. It carries `[project]`, `[project.scripts]`, runtime deps, and the hatchling build target. Ruff, dev deps, and pytest config stay at root for now (single-member workspace).

Create `packages/core/pyproject.toml` with:

```toml
[project]
name = "agent-core"
version = "0.1.0"
description = "Core infrastructure for AI agents - memory, knowledge compilation, and tooling"
requires-python = ">=3.12"
dependencies = [
    "claude-agent-sdk>=0.1.29",
    "python-dotenv>=1.0.0",
    "tzdata>=2024.1",
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
    "desktop-notifier>=5.0",
    "mcp>=1.9.0",
    "agentmail>=0.4",
    "aiosqlite>=0.20",
]

[project.scripts]
agent-core = "agent_core.cli:app"
agent-core-notify = "agent_core.notify.mcp_server:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core"]
```

The hatch path is relative to *this* `pyproject.toml`, so `src/agent_core` correctly resolves to `packages/core/src/agent_core`.

- [ ] **Step 2: Rewrite the root `pyproject.toml`**

The root pyproject becomes a workspace declaration plus shared tool config. Replace the file entirely with:

```toml
# Root pyproject — agent-core workspace (uv).
# Member packages live under packages/*. Each owns its [project] metadata.
# This file holds: workspace declaration, dev deps, shared ruff/pytest config.

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
agent-core = { workspace = true }

[dependency-groups]
# Dev tools only; real package deps live in member pyprojects.
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "freezegun>=1.5",
    "ruff>=0.14",
    "import-linter>=2.0",
    "towncrier>=23.11",
]

[tool.ruff]
line-length = 100
src = ["packages/core/src"]

[tool.pytest.ini_options]
testpaths = ["packages/core/tests"]
asyncio_mode = "auto"
```

Notes:
- `pythonpath = ["src"]` from the old root config is removed; uv's editable workspace install puts `agent_core` on the path automatically.
- `[tool.uv.sources]` will grow with each package added in later migration steps (one entry per workspace member).
- `[tool.ruff] src = ["packages/core/src"]` will also grow; for now there's only one package.

- [ ] **Step 3: Run `uv sync` to rebuild the venv**

Run: `uv sync`
Expected: `uv` resolves the workspace, installs `agent-core` editable from `packages/core`, plus all dev deps. No errors. Time should be similar to before.

If you see "Could not find a `pyproject.toml`" or "no project found", the workspace declaration is wrong — recheck `[tool.uv.workspace] members`.

- [ ] **Step 4: Verify the entry-point scripts exist**

Run: `ls .venv/Scripts/agent-core* 2>&1` (Windows) or `ls .venv/bin/agent-core* 2>&1` (POSIX)
Expected: `agent-core` and `agent-core-notify` executables present.

- [ ] **Step 5: Verify pytest discovers and runs all tests from root**

Run: `uv run --no-sync pytest -q`
Expected: all tests pass. Test count must match the snapshot from Task 1 Step 3. If a test fails or the count differs, stop and investigate before proceeding.

- [ ] **Step 6: Verify ruff passes**

Run: `uv run --no-sync ruff check .`
Expected: no errors. (Ruff config now points at `packages/core/src` — confirm by running `uv run --no-sync ruff check packages/core/src` and seeing the same result.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml packages/core/pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
build(workspace): split pyproject into root workspace + packages/core

Root pyproject becomes a uv workspace declaration with shared dev deps
and shared ruff/pytest config. packages/core/pyproject.toml owns the
agent-core distribution: project metadata, runtime deps, scripts, and
the hatchling wheel target.

Adds import-linter and towncrier to dev deps in preparation for Tasks
5 and 6.
EOF
)"
```

---

## Task 4: Update the justfile

**Files:**
- Modify: `justfile`

The current justfile hard-codes `tests/` and `.` paths. After the move, `tests/` no longer exists at the root; `.` still works for ruff but we want the recipes to read clearly.

- [ ] **Step 1: Read the current justfile**

```bash
cat justfile
```

Confirm it matches the version checked in (PowerShell shell, recipes for `test`, `test-quick`, `lint`, `format`, `gate`, `install`, `sync`).

- [ ] **Step 2: Replace the contents**

Overwrite `justfile` with:

```make
# agent_core justfile (workspace root)
set shell := ["powershell", "-NoProfile", "-Command"]

# Run all tests
test:
    uv run --no-sync pytest -v

# Run tests (fast, no output)
test-quick:
    uv run --no-sync pytest -q

# Lint
lint:
    uv run --no-sync ruff check .

# Format
format:
    uv run --no-sync ruff format .

# Architecture contracts
contracts:
    uv run --no-sync lint-imports

# Full quality gate (mirrors CI)
gate: lint contracts test

# Install agent-core as a global tool (isolated venv, no file lock conflicts)
install:
    uv tool install --reinstall "e:/workspaces/ai/agents/agent_core"

# Sync project dependencies (dev only)
sync:
    uv sync
```

Changes from the old justfile:
- `pytest tests/` → `pytest` (testpaths now in root pyproject)
- All `uv run` calls now use `--no-sync` (per the lock-prevention fix shipped earlier)
- New `contracts` recipe wires `import-linter` (config arrives in Task 5)
- `gate` runs lint + contracts + test in that order

- [ ] **Step 3: Verify the recipes work**

Run: `just test-quick`
Expected: tests pass.

Run: `just lint`
Expected: no errors.

Skip `just contracts` for now — `import-linter` config doesn't exist until Task 5.

- [ ] **Step 4: Commit**

```bash
git add justfile
git commit -m "chore(workspace): update justfile for new test/lint paths"
```

---

## Task 5: Wire up `import-linter`

**Files:**
- Create: `.importlinter`

`import-linter` is already in dev deps (added in Task 3 Step 2). Today there's only one package, so the contracts are minimal — but laying the foundation now means later plans (Steps 2+) only add new contracts without touching `import-linter` setup.

- [ ] **Step 1: Create `.importlinter`**

Create `.importlinter` at the repo root with one bootstrap contract — the bus core must not depend on any other `agent_core` subpackage. This is a real architectural property today (the bus is the foundation, everything else builds on it) and gives `import-linter` a non-trivial contract to enforce.

```ini
[importlinter]
root_packages =
    agent_core

[importlinter:contract:bus-core-self-contained]
name = bus core does not depend on other agent_core subpackages
type = forbidden
source_modules =
    agent_core.bus
forbidden_modules =
    agent_core.cli
    agent_core.email
    agent_core.notify
    agent_core.endpoints
    agent_core.bus_hooks
    agent_core.skills
```

This contract is the seed pattern. As later plans add `agent-core-discord`, `agent-core-scheduler`, etc., they extend `.importlinter` with sibling contracts ("no integration imports another integration").

- [ ] **Step 2: Run `lint-imports`**

Run: `uv run --no-sync lint-imports`
Expected: `Contracts: 1 kept, 0 broken.` (or equivalent "All contracts pass" message).

If the contract reports broken — meaning the bus core today does import from one of the listed forbidden modules — stop and investigate. Either the contract is wrong (the dependency is legitimate) or there's a real architectural smell to fix. Do not commit a passing run with the contract loosened until the situation is understood.

- [ ] **Step 3: Verify `just contracts` works**

Run: `just contracts`
Expected: same as Step 2.

- [ ] **Step 4: Commit**

```bash
git add .importlinter
git commit -m "$(cat <<'EOF'
chore(workspace): add import-linter with bus-core self-containment contract

Seeds .importlinter with a forbidden contract: agent_core.bus must not
depend on any other agent_core subpackage. This is a real architectural
property today and gives import-linter a non-trivial enforcement to run.
Future plans add sibling contracts as packages are carved out.
EOF
)"
```

---

## Task 6: Wire up towncrier for the core package

**Files:**
- Create: `packages/core/towncrier.toml`
- Create: `packages/core/CHANGELOG.md`
- Create: `packages/core/changelog.d/.gitkeep`
- Create: `packages/core/changelog.d/<pr>.changed.md`

`towncrier` is already in dev deps (added in Task 3 Step 2).

- [ ] **Step 1: Create `packages/core/towncrier.toml`**

```toml
[tool.towncrier]
name = "agent-core"
package = "agent_core"
package_dir = "src"
directory = "changelog.d"
filename = "CHANGELOG.md"
title_format = "## {version} ({project_date})"
issue_format = "[#{issue}](https://github.com/jeffrichley/agent_core/pull/{issue})"

[[tool.towncrier.type]]
directory = "added"
name = "Added"
showcontent = true

[[tool.towncrier.type]]
directory = "changed"
name = "Changed"
showcontent = true

[[tool.towncrier.type]]
directory = "deprecated"
name = "Deprecated"
showcontent = true

[[tool.towncrier.type]]
directory = "removed"
name = "Removed"
showcontent = true

[[tool.towncrier.type]]
directory = "fixed"
name = "Fixed"
showcontent = true

[[tool.towncrier.type]]
directory = "security"
name = "Security"
showcontent = true
```

Notes:
- All paths in `[tool.towncrier]` are relative to the config file's directory (`packages/core/`). Towncrier joins the config's parent dir with `directory`, `filename`, and `package_dir` to find fragments. Do NOT include `packages/core/` in those values — it produces a doubled path that silently finds zero fragments.
- One config per package keeps fragments scoped to their package.

- [ ] **Step 2: Create `packages/core/CHANGELOG.md` with an empty stub**

```markdown
# Changelog

All notable changes to `agent-core` are documented in this file. The format
is generated by towncrier from fragments in `changelog.d/` at release time.

<!-- towncrier release notes start -->
```

The `<!-- towncrier release notes start -->` marker is required — towncrier inserts new release sections immediately above it.

- [ ] **Step 3: Create `packages/core/changelog.d/` with a `.gitkeep`**

```bash
mkdir -p packages/core/changelog.d
touch packages/core/changelog.d/.gitkeep
```

The `.gitkeep` ensures the directory exists in version control even when no fragments are pending.

- [ ] **Step 4: Create the first fragment for this restructure**

Once the PR number is known, create `packages/core/changelog.d/<pr-number>.changed.md` with one sentence:

```markdown
Restructured the repo into a `uv` workspace. `agent-core` is now a member at `packages/core/`; subsequent integrations will land as sibling packages.
```

If the PR number is not yet known (you're committing locally first), name the file `+workspace-restructure.changed.md` — towncrier accepts a `+`-prefixed filename as a no-issue fragment, and it can be renamed to the real PR number before merge.

- [ ] **Step 5: Verify towncrier renders the fragment**

Run: `uv run --no-sync towncrier build --draft --version 0.2.0 --config packages/core/towncrier.toml`
Expected: a markdown preview that includes the "Changed" section with the fragment text. The `--draft` flag means no files are actually rewritten.

If `towncrier` cannot find fragments, recheck the `directory` path in `towncrier.toml`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/towncrier.toml packages/core/CHANGELOG.md \
        packages/core/changelog.d/
git commit -m "$(cat <<'EOF'
docs(workspace): add towncrier config and initial changelog for agent-core

Each package owns its own towncrier config and changelog.d/. The first
fragment records this restructure.
EOF
)"
```

---

## Task 7: Final integration smoke and verification

**Files:**
- None (read-only verification)

This task does not commit. It exists to catch anything the per-task verifications missed before pushing.

- [ ] **Step 1: Re-run the full quality gate**

Run: `just gate`
Expected: lint passes, contracts pass, all tests pass.

- [ ] **Step 2: Smoke-test the `agent-core hooks` CLI**

Run: `echo '{}' | uv run --no-sync agent-core hooks run SessionStart`
Expected: JSON output containing `hookSpecificOutput.additionalContext` with the current time. (Same behavior as before the restructure.)

- [ ] **Step 3: Smoke-test the notify MCP server**

Run: `echo '' | timeout 5 uv run --no-sync python -m agent_core.notify.mcp_server; echo "EXIT=$?"`
Expected: the server starts, errors on the empty stdin (a known harmless validation error), then exits with `EXIT=0` (the `os._exit(0)` safeguard).

- [ ] **Step 4: Smoke-test the bus CLI**

Run: `uv run --no-sync agent-core bus status`
Expected: bus status output (no errors). If the bus.sqlite file path is unhappy, check that `agent_core.yaml` is still at the repo root and being found.

- [ ] **Step 5: Verify Pepper is unaffected**

Run: `ls "E:/workspaces/ai/pepper/src/pepper" | head -5`
Expected: Pepper's source tree is untouched. (We did not edit Pepper in this plan; this is a paranoia check.)

---

## Task 8: Push the branch

**Files:**
- None (push only)

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/workspace-restructure`
Expected: branch pushed; URL printed for opening a PR.

- [ ] **Step 2: Open the PR**

Use `gh pr create` (or the GitHub UI) with title and body:

Title: `chore(workspace): migrate to uv workspace with packages/core`

Body:
```markdown
## Summary
- Migration Step 1 of the monorepo workspace design (sub-project A).
- Restructures the repo into a `uv` workspace. `agent-core` is now a single member at `packages/core/`; later steps add sibling packages.
- No code logic changes — file moves and config splits only.
- Wires up `import-linter` and `towncrier` so they're configured before sibling packages arrive.

## Test plan
- [x] `just gate` passes locally
- [x] `agent-core hooks run SessionStart` smoke
- [x] `python -m agent_core.notify.mcp_server` starts and exits cleanly
- [x] `agent-core bus status` works
- [ ] CI green on the PR
- [ ] Towncrier fragment renamed to the real PR number before merge

## Spec
[`docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md`](docs/superpowers/specs/2026-04-28-monorepo-workspace-design.md)

## Plan
[`docs/superpowers/plans/2026-04-28-workspace-restructure.md`](docs/superpowers/plans/2026-04-28-workspace-restructure.md)
```

- [ ] **Step 3: Rename the towncrier fragment to the real PR number**

After the PR is opened, GitHub assigns it a number (e.g., `#3`).

```bash
git mv packages/core/changelog.d/+workspace-restructure.changed.md \
       packages/core/changelog.d/3.changed.md
git commit -m "docs(workspace): bind towncrier fragment to PR number"
git push
```

(Adjust `3` to the real PR number.)

- [ ] **Step 4: Update the ROADMAP after merge**

Once the PR merges, mark sub-project A's row in `docs/ROADMAP.md` as 🟢 Step 1 shipped, with the merge commit hash. (This is a follow-up commit on `main` — not part of the feature branch — but recording it here so it isn't forgotten.)

---

## Definition of done

- [x] Spec exists and is approved (precondition; already done).
- [ ] Branch `feat/workspace-restructure` exists with all task commits.
- [ ] `just gate` passes locally.
- [ ] PR opened.
- [ ] CI green on PR (note: no CI workflows exist yet at the time of writing — that's deferred to a later plan; for now "CI green" means the local gate passes).
- [ ] Towncrier fragment renamed to the real PR number.
- [ ] PR merged to `main`.
- [ ] ROADMAP updated post-merge.
