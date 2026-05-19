# Phase 1 — CI Gate: Design

**Status:** approved in brainstorm 2026-05-18, ready for implementation plan.

**Relationship to the maturity spec:** This refines the `Phase 1 — CI gate`
section of `docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md`.
Where the two differ, **this document wins** (it carries decisions the
umbrella spec did not settle: CPU-only integration deps, the concrete
pre-push hook mechanism, the `check` matrix decision, concurrency). The
umbrella spec's dependency order is unchanged: Phase 0 (done) → **Phase 1** →
(2 ∥ 3) → 4.

---

## 1. Goal

A CI gate so `main` is never red, with failures **rare but loud**:

- Every push and PR runs the same quality gate developers run locally
  (`just check`), on the OSes whose runtime actually diverges.
- `main` is protected by a branch ruleset that requires the gate green.
- A `pre-push` git hook keeps `main` green *by construction*, so CI
  failures stay rare and therefore meaningful.
- Failure emails stay **on** (the wanted alarm); they fire rarely because
  the pre-push hook catches breakage before it leaves the developer's
  machine.

Phase 1 must land before Phases 2/3 so those are CI-protected.

## 2. Context (verified 2026-05-18)

- **Greenfield CI:** the repo has no `.github/` directory and zero branch
  rulesets. Nothing to migrate.
- **`justfile` exists:** `check: lint typecheck contracts test`.
  `pyproject.toml` `addopts` already applies `-m 'not slow'`, so the
  fast/slow split the design relies on already works. `set windows-shell
  := ["cmd.exe", "/c"]` is already configured.
- **No competing git hook in this repo.** `.git/hooks/` contains only
  stock `*.sample` files. `core.hooksPath` is set (repo-local only; no
  global) to the absolute path of `.git/hooks` — git's default made
  explicit. The gstack secret-scan hook lives in the **separate
  `~/.gstack/` repository** and scans *that* repo's commits, not
  agent_core's. The umbrella spec's "leave the existing secret-scan hook
  untouched" caveat therefore does not apply here: repointing
  `core.hooksPath` shadows nothing.
- **Torch/CUDA weight:** Torch lives almost entirely in
  `agent-core-voice` (vendored Qwen3-TTS). The daemon core only passes
  `--extra cu130` through `install.py`; it does not import torch itself.
  `pytest` `testpaths` includes `packages/agent-core-voice/tests`, so the
  fast suite collects voice tests.

## 3. The CI workflow (`.github/workflows/ci.yml`)

**Triggers:** `pull_request`, `push: [main]`, `workflow_dispatch`. Nothing
scoped away — full visibility.

**Concurrency:** group `${{ github.workflow }}-${{ github.ref }}`,
`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`.
Superseded-run cancellation applies to PRs only — `push: [main]` runs
always finish so every commit on `main` has a recorded green check.

**Hardening:** a top-level `permissions: contents: read` restricts the
workflow token to read-only access. Each job carries `timeout-minutes: 20`
to prevent runaway billing from hung steps.

**Supply chain:** every third-party action is pinned by **commit SHA**
(a tag can be moved; a SHA cannot), with a trailing comment recording the
human-readable version.

### Job `check` — the fast gate

- Matrix `os: [ubuntu-latest, windows-latest]`, `fail-fast: false` (an
  ubuntu break must not mask a Windows-only one; Windows is prod).
- Rationale for the matrix: the OS-divergent bug surface for a Python
  project (paths, newlines, shell/process, signals, platform-gated code)
  splits along **Windows ↔ POSIX**. `ubuntu` is the cheap POSIX sample;
  `windows` is prod. macOS would be a second POSIX sample at GitHub's
  ~10× minutes multiplier with ~no unique catch (no Mac-specific code in
  agent_core today). See §6 for when macOS earns a slot.
- Steps:
  1. `actions/checkout` with `fetch-depth: 0` and `fetch-tags: true`
     (later phases' `git describe` needs full history + tags).
  2. `astral-sh/setup-uv` (SHA-pinned), `enable-cache: true`,
     `python-version: "3.12"`.
  3. `extractions/setup-just` (SHA-pinned).
  4. `uv sync --locked --all-packages` — **`--locked`**, not `--frozen`:
     CI *must* fail on a stale lockfile.
  5. `just check`.
  6. `uv cache prune --ci`.

### Job `integration` — slow suite (Windows only)

- Runs on `windows-latest` only: the stale-cache regression must hit the
  real prod shell; it cannot be POSIX.
- **Narrow, Torch-free sync:** `uv sync --locked --package agent-core`.
  This deliberately excludes `agent-core-voice` / `qwen-tts`. A
  GitHub-hosted runner has no GPU; `--all-packages` here would pull
  multi-GB Torch that can never be exercised. Do not "widen" this sync.
- Steps:
  1. Checkout, `setup-uv` (SHA-pinned, `enable-cache: true`,
     `python-version: "3.12"`), `setup-just` (as above).
  2. `uv sync --locked --package agent-core`.
  3. `uv run --no-sync pytest packages/core/tests -m slow -v`.
- The slow suite (`packages/core/tests/test_daemon_install_integration.py`)
  is self-contained: it builds its own temporary workspace, needs no
  running daemon, no `agent_core.yaml`, and no free port. No ephemeral
  daemon is spun up; no teardown step is needed.
- Because the job is light and self-contained, it is a **required PR
  check** (not deferred to `workflow_dispatch`-only) — a ruleset cannot
  require a check that does not run on PRs.

## 4. Pre-push hook + install recipe

- **`.githooks/pre-push`** — version-controlled (so it is reviewed in
  PRs and passes through Phase 1's own gate). A POSIX `sh` script (Git
  for Windows ships `sh`, so it runs identically on the Windows box and on
  macOS/Linux). It runs `just check` and forwards the exit code; non-zero
  aborts the push. No per-OS logic in the hook.
- **`just install-hooks`** — runs `git config core.hooksPath .githooks`
  (relative path; resolves in the main checkout and in linked worktrees,
  since `.githooks/` is version-controlled and present in every
  checkout). Idempotent — re-running re-asserts the config.
- **One-time per-clone bootstrap:** git deliberately will not auto-run
  committed hook code, so a fresh clone or new worktree must run
  `just install-hooks` once. This is inherent to git, not a design
  choice; both candidate mechanisms had it, so it is accepted.
- **`git push --no-verify`** is the deliberate emergency escape hatch.
- **Distribution ("humans and agents get it identically"):**
  `just install-hooks` is the single surface. It is documented in the
  contributor bootstrap under `docs/setup/` and referenced from the
  agent/daemon setup path, so an agent provisioning a workspace runs the
  same recipe a human does.

## 5. Branch ruleset on `main` + notification posture

- **Branch ruleset** created from scratch via a `gh api` call whose exact
  JSON payload is specified in the implementation plan (reproducible and
  recorded, not a one-off UI click):
  - Required status checks before merge: `check (ubuntu-latest)`,
    `check (windows-latest)`, `integration` — all green.
  - **Strict / up-to-date:** the branch must be current with `main`
    before merge (no merging stale branches that passed against old
    code).
  - **Bypass list: the repo owner** — emergency direct push to `main`
    preserved.
  - **Deliberately not enabled:** required reviews (solo repo), signed
    commits, linear history. The pre-push hook is the real enforcement;
    the ruleset is the backstop.
- **Notification posture** (one-time, owner's GitHub account; recorded in
  the plan as a **manual** step — the implementer does not change account
  settings): Settings → Notifications → Actions → **"Send notifications
  for failed workflows only."** Failures always email (the wanted alarm);
  successes are silent. Noise is controlled by *preventing* failures
  (pre-push), not by muting the alarm.

## 6. Testing strategy

- **`just install-hooks` unit test:** in a throwaway temp git repo,
  run the recipe and assert `git config core.hooksPath` resolves to
  `.githooks`. In addition, the test asserts the committed
  `.githooks/pre-push` is tracked with git mode `100755`
  (cross-platform-meaningful) — this check runs against the real repo,
  not the temp repo. Never touches the real repo config.
- **Workflow + ruleset validated by use:** the Phase 1 PR is itself the
  first thing the new gate runs on. If `check`/`integration` do not go
  green on Phase 1's own PR, Phase 1 is not done. No mocking CI.
- **Hook exercised live:** every push from the moment it is installed;
  `--no-verify` is the tested escape path.

## 7. Flagged items (decisions recorded)

1. **~~`integration` daemon must start without `cu130`.~~** **RESOLVED /
   MOOT FOR CI.** The `integration` job's slow suite never starts the
   supervised daemon — it is fully self-contained (builds its own temp
   workspace). The question of whether the daemon can start without
   `cu130` does not gate CI and is therefore moot for this phase.
2. **`uv sync --all-packages` in `check` pulls CPU Torch.** The fast
   suite collects `packages/agent-core-voice/tests`, which likely import
   Torch, so `--all-packages` is the safe correctness default. Decision:
   **keep `--all-packages` for Phase 1** (do not expand Phase 1 scope).
   Leaning the sync out (plus marking voice tests so they skip cleanly
   without Torch) is recorded as a deliberate **future optimization**,
   not Phase 1 work.

## 8. Risks → mitigations

| Risk | Mitigation |
|---|---|
| ~~Integration job can't run CPU-only (daemon hard-imports GPU stack)~~ | MOOT — the slow suite is self-contained and never starts the daemon (flagged item 1, resolved) |
| Stale lockfile silently passes | `uv sync --locked` (not `--frozen`) fails CI fast |
| Ubuntu-only failure masks a Windows-only break | Matrix includes `windows-latest`; `fail-fast: false` |
| CI failures become ignorable noise | Pre-push keeps failures rare; "failed-only" email posture keeps the alarm meaningful |
| Hook silently not installed on a fresh clone/worktree | Documented one-time `just install-hooks` in `docs/setup/` + agent setup path; unit-tested recipe |
| A moved action tag injects malicious code | All third-party actions SHA-pinned |
| `check` job slow due to Torch in `--all-packages` | Accepted for Phase 1; lean-out recorded as future optimization (flagged item 2) |

## 9. Explicitly out of scope

- `pre-commit` framework (redundant with `just check`; adds a toolchain
  dependency).
- Required reviews, signed commits, linear history on `main`.
- macOS in the `check` matrix — revisit only when agent_core gains
  Mac-specific code (e.g. a `launchd` auto-start analog to Phase 4's
  Task Scheduler, or a `sys.platform == "darwin"` branch).
- GPU/CUDA testing in CI (no GPU on hosted runners; GPU correctness is
  validated by the real prod deploy + live-verify).
- Phases 2–4 (versioning/releases, dev-prod instance-parameterization,
  daemon auto-start) — separate specs/plans.

## 10. One-time setup (recorded so it is not lost)

- Per clone / per new worktree: `just install-hooks`.
- Create the `main` branch ruleset (exact `gh api` call specified in the
  implementation plan).
- Set the GitHub Actions notification posture to "failed workflows only"
  (owner's account; manual; not performed by the implementer).
