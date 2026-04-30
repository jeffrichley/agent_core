# Lint + Type Discipline Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `agent_core` from ad-hoc lint/type checks to a stable, enforceable baseline with Ruff + mypy discipline close to `factory`, without destabilizing feature delivery.

**Architecture:** Introduce tool configuration in stages, lock a "no new debt" baseline, then ratchet strictness by package (`packages/core`, `packages/agent-core-channel`, then others). Use short PRs with explicit pass/fail gates.

**Tech Stack:** Python 3.12, uv workspace, Ruff (workspace-level config), mypy (workspace-level config), pytest.

**Reference baseline:** `E:/workspaces/businesses/factory/pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.format]`, `[tool.mypy]`).

---

## Current state (as of this plan)

- `agent_core` has minimal Ruff config in root `pyproject.toml` (`line-length`, `src` only).
- `agent_core` has no mypy config (`[tool.mypy]` absent).
- Ruff debt exists in files outside the responsive-inbox/channel-relay scope.
- We want consistency with `factory`, but staged to avoid one giant, noisy PR.

---

## Guardrails

- Do not block product work with a "big-bang lint cleanup" PR.
- Keep each PR under a single intent:
  - config bootstrap
  - baseline/no-new-debt gate
  - package-specific cleanup
  - strictness ratchet
- When enabling a new rule, fix violations in the same PR or use a targeted temporary ignore with a dated TODO.
- Avoid repository-wide formatting-only churn unless explicitly approved.

---

## Phase 1: Tooling baseline and reproducibility

### Task 1.1 - Add missing type-check tooling dependencies

- [ ] Add `mypy` and needed stub packages to root dev dependencies in `pyproject.toml`.
- [ ] Include `pydantic.mypy` plugin dependency only if required by current package models.
- [ ] Run `uv sync` and verify tools are invokable from workspace root.

**Exit criteria:**
- `uv run ruff --version` succeeds.
- `uv run mypy --version` succeeds.

### Task 1.2 - Add initial mypy config (non-destructive)

- [ ] Add `[tool.mypy]` in root `pyproject.toml`.
- [ ] Scope to current production source trees first:
  - `packages/core/src`
  - `packages/agent-core-channel/src`
- [ ] Start with practical checks:
  - `python_version = "3.12"`
  - `warn_unused_ignores = true`
  - `no_implicit_optional = true`
  - `check_untyped_defs = true`
  - leave strict flags (`disallow_untyped_defs`, etc.) for later phases.

**Exit criteria:**
- `uv run mypy` runs and reports findings (not tool/config failures).

### Task 1.3 - Expand Ruff config to explicit policy

- [ ] Add `[tool.ruff.lint]` with a curated starting `select` set aligned with `factory` philosophy:
  - `F`, `E`, `I`, `UP`, `B`, `RUF100`
- [ ] Add `[tool.ruff.format]` defaults for consistency.
- [ ] Add `extend-exclude` entries for generated/local trees if needed.
- [ ] Keep `line-length = 100`.

**Exit criteria:**
- `uv run ruff check packages/core packages/agent-core-channel` runs with predictable output.

---

## Phase 2: Establish baseline ("no new debt")

### Task 2.1 - Capture and pin current debt

- [ ] Generate a baseline report for Ruff and mypy findings.
- [ ] Decide one strategy:
  - fix immediately if count is small, or
  - temporarily suppress with file-scoped ignores and TODO tags if count is large.
- [ ] Document baseline counts in `handoff.md` or a dedicated quality note.

**Exit criteria:**
- CI/local command can fail only on *new* violations, not legacy backlog.

### Task 2.2 - Wire standard quality commands

- [ ] Define canonical commands in docs (`README` or contributor guide):
  - `uv run ruff check ...`
  - `uv run mypy ...`
  - `uv run pytest -q`
- [ ] Optional: add task aliases (`just`, `make`, or script entrypoints) if the project wants parity with `factory`.

**Exit criteria:**
- Every contributor/agent has one obvious lint/type command path.

---

## Phase 3: Package-by-package debt burn down

### Task 3.1 - `packages/core` cleanup pass

- [ ] Fix known Ruff violations in `packages/core/src` and relevant tests.
- [ ] Fix mypy errors in `packages/core/src` highest-risk modules first:
  - CLI entry paths
  - endpoint/session/message handling
  - bus/persistence paths
- [ ] Keep refactors minimal and behavior-preserving.

**Exit criteria:**
- `packages/core` passes agreed Ruff profile.
- `packages/core/src` passes configured mypy profile.

### Task 3.2 - `packages/agent-core-channel` cleanup pass

- [ ] Resolve Ruff issues with same rule profile.
- [ ] Resolve mypy issues in SSE relay + stdio server.

**Exit criteria:**
- `packages/agent-core-channel` passes Ruff + mypy.

### Task 3.3 - Remaining workspace packages

- [ ] Apply same process to:
  - `packages/credentials`
  - `packages/notify`
  - `packages/agent-core-discord`
- [ ] Track progress per package with checkbox updates in this doc.

**Exit criteria:**
- All maintained packages pass baseline Ruff + mypy config.

---

## Phase 4: Ratchet to `factory`-level discipline

### Task 4.1 - Tighten Ruff rule set

- [ ] Evaluate and (if useful) add from `factory`:
  - `N`, `C4`, `TID`, `BLE`, targeted `TRY*`, targeted security rules (`S10x`, etc.).
- [ ] Add per-file ignores only where justified.
- [ ] Enforce cleanup of stale ignores with `RUF100`.

**Exit criteria:**
- Final Ruff policy is explicit and documented.

### Task 4.2 - Tighten mypy strictness progressively

- [ ] Enable one strict flag at a time, fix, then lock:
  - `disallow_untyped_defs`
  - `disallow_untyped_decorators`
  - `warn_redundant_casts`
  - `strict_equality`
  - `warn_return_any`
- [ ] Add/adjust plugin config only where validated.

**Exit criteria:**
- Stable mypy profile approaching `factory` strictness without high false-positive churn.

---

## Phase 5: CI enforcement and maintenance

### Task 5.1 - Enforce gates in CI

- [ ] Add/verify CI jobs for Ruff + mypy + tests.
- [ ] Fail PRs on violations once baseline debt is cleared.
- [ ] Keep gates fast enough for frequent agent loops.

### Task 5.2 - Define maintenance policy

- [ ] "No new ignores without justification" policy.
- [ ] Monthly or milestone-based "strictness ratchet" review.
- [ ] Document exception process for urgent hotfixes.

---

## Suggested PR sequence

1. `chore(quality): bootstrap mypy config and ruff policy`
2. `chore(quality): establish baseline and no-new-debt gate`
3. `chore(quality): clean ruff/mypy in packages/core`
4. `chore(quality): clean ruff/mypy in agent-core-channel`
5. `chore(quality): ratchet strictness toward factory profile`
6. `chore(ci): enforce lint/type gates`

---

## Success definition

- Developers and agents can run one standard quality command set locally.
- CI reliably blocks regressions in lint/type quality.
- Remaining ignores are intentional, documented, and time-bounded.
- `agent_core` quality posture is comparable to `factory`, adapted for workspace structure.

---

## Baseline snapshot (2026-04-30)

Commands run:

- `uv run ruff check packages/core packages/agent-core-channel --statistics`
- `uv run mypy`

Ruff baseline:

- 113 total findings
- largest buckets:
  - `UP017` (52)
  - `UP037` (11)
  - `B017` (9)
  - `B008` (8)
  - `I001` (8)
- 85 findings auto-fixable by Ruff (`--fix`)

Mypy baseline:

- 55 errors across 9 files
- largest concentration:
  - nullable store/connection access in `bus/persistence.py` and `bus/core.py`
- additional clusters:
  - scheduler argument typing
  - MCP stdio server stream typing
  - a small set of index/arg-type assignment issues

Baseline cleanup notes:

- Added `mypy`, `types-PyYAML`, and `types-psutil` to dev dependencies.
- Added initial `[tool.mypy]` config.
- Expanded Ruff config to explicit lint/format policy.
