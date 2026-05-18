# agent_core Project Maturity — Design Spec

**Date:** 2026-05-18
**Status:** Approved (design); pending implementation plan
**Topic:** Build a world-class CI / versioning / dev-prod / reboot-resilience
foundation for agent_core, with the daemon-stale-code defect ("Defect A")
as the keystone.

---

## 1. Goal

Mature agent_core from "works on Jeff's box, deployed by hand" to a
world-class, self-protecting foundation:

- The daemon can never silently ship stale code (**Defect A**, eliminated).
- A real CI gate exists and `main` is never red — without turning CI
  failure emails into ignorable noise.
- Versioning makes "what code is actually running" a readable fact.
- The daemon can be iterated on safely while live agents (Pepper, Wren)
  keep running.
- The daemon comes back automatically after a reboot.

This is **one comprehensive spec** with a **phased rollout** — each phase
is an independently shippable, independently revertible PR. The spec is
the coherent blueprint; the phases keep each implementation plan small
and ship the urgent fix first.

## 2. Context & current state (verified 2026-05-18)

- uv **workspace monorepo**: root `pyproject.toml` `[tool.uv.workspace]`,
  10 member packages under `packages/*`, `uv.lock` at root, Python 3.12,
  build backend `hatchling`.
- Every member pins a **static** `version = "0.1.0"` — never bumped.
- A canonical quality gate already exists as the **`justfile`**:
  `just check` = `ruff check` → `mypy` → `lint-imports` → `pytest -q`
  (`-m 'not slow'` by default). `just sync` = `uv sync --dev`.
- **No CI at all** (no `.github/`). `main` is **completely unprotected**:
  classic branch protection absent, repo rulesets `[]`, rules on `main`
  `[]`. Nothing server-side enforces anything today.
- `towncrier` is a dev dependency and each package already has a
  `towncrier.toml` (changelog-fragment config); no release process drives
  it. Only non-semver tags exist (`pepper-cutover-*`).
- Dev pattern already in use: git worktrees under `.worktrees/`.
- The daemon installs into an isolated venv via
  `uv sync --frozen --no-editable --no-dev` with `UV_PROJECT_ENVIRONMENT`
  pointed at `~/.agent-core/.venv`, recording an install stamp
  (`installed_sha`, `uv_lock_hash`, etc.). Pure/impure split already a
  project convention — see `packages/core/src/agent_core/daemon/install.py`
  (`build_uv_sync_command` pure, `run_install` impure).

## 3. The research finding that reshaped this design

The original premise (from prior sessions) was: "Defect A happens because
the version string never changes, so give every commit a unique
VCS-derived version and uv's cache will rebuild." **Deep research
empirically disproved this.**

A throwaway uv workspace (uv 0.7.13, same as the daemon) with
`uv-dynamic-versioning` active was used to mutate source between
`uv sync --frozen --no-editable` runs:

- `git describe` advanced every commit, but `uv sync` printed
  `Audited 1 package` (no rebuild) and the venv ran the **old code** with
  the **old version**. Defect A reproduced *identically with VCS
  versioning*.

**Real root cause:** uv's build cache for a **local / workspace path
dependency** is keyed by the **`pyproject.toml` (or `setup.py`/`setup.cfg`)
last-modified time**, *not* the version string. uv only rebuilds a
first-party dir if those files change or a `src` dir is added/removed.
Confirmed against uv's caching docs and uv issue
[#15224](https://github.com/astral-sh/uv/issues/15224) (OPEN; charliermarsh:
"We don't rebuild on arbitrary file changes... we _do_ rebuild if the
`pyproject.toml` is modified"). The "(name, version)" cache key is true
for *published PyPI wheels*, not local workspace dirs. `--no-editable`
makes it worse than editable (editable picks up `src/` via the `.pth`
link; `--no-editable` builds a real wheel and drops source changes
silently).

**The actual fix (empirically verified):** add a git-commit cache key to
every member `pyproject.toml`:

```toml
[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
```

With this, a source-only change + new commit under
`uv sync --frozen --no-editable` **correctly rebuilds** every time.

**The Google lens (Jeff's question — "what does Google do?"):** Google's
monorepo does **not** use per-package SemVer or VCS version strings
internally. Code is identified by **commit**; Bazel's build cache is
**content-addressed**, so stale builds are structurally impossible.
SemVer appears only at the *external publishing* boundary. Our cache-key
fix is the uv analogue of Bazel's commit/content invalidation; the
existing install stamp's `installed_sha` is the "code identity = commit"
signal. Conclusion adopted: the Google-correct part (cache invalidation)
is mandatory and small; VCS *version strings* are optional human polish;
full SemVer release automation is ceremony for a non-published
solo+agents repo.

**Consequence for this design:** VCS versioning is decoupled from
correctness. Defect A's fix is ~2 lines/package and certain. Versioning
becomes a separate, optional release-hygiene pillar (adopted at "middle"
depth — see Phase 2).

## 4. Phased blueprint

Dependency order: **0 → 1 → (2 ∥ 3) → 4**. Phase 0 is unblocked and
urgent (it removes an *active* operational hazard — Pepper/Wren currently
depend on a manual surgical `uv cache clean` on every `daemon refresh`).
Phase 1 should land before 2/3 so they are CI-protected. Phase 4 depends
on Phase 3 (auto-start targets the prod instance).

### Phase 0 — Defect-A cache-key fix (standalone, ships first)

- Add `[tool.uv] cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]`
  to **all 10** `packages/*/pyproject.toml`.
  - `cache-keys` **replaces** uv's defaults, so the
    `{ file = "pyproject.toml" }` entry **must** be present or
    dependency/metadata edits stop invalidating.
  - The git key invalidates on every commit. The daemon only ever
    deploys *committed* state (it records `installed_sha`), so this is
    correct. Documented caveat: a build of *uncommitted* changes would
    not be seen by the git key — the daemon never does that.
- One-time `uv lock` after the metadata edits.
- **Guard test** (`packages/core/tests/`): parse *every*
  `packages/*/pyproject.toml` and assert the **full expected
  `cache-keys` list** (not merely presence of the git key). A new package
  physically cannot reintroduce Defect A without failing `just check`/CI.
- **Regression test** (marked `slow`): make a trivial source change to a
  member, commit it, run daemon install/refresh into a temp venv, assert
  the installed file in that venv reflects the new code.
- After merge+deploy: do the manual surgical-clean verification *one last
  time*, then update `docs/setup/daemon.md` and the
  `daemon-refresh-stale-cache-hazard` memory to retire the manual
  procedure.
- **Alternatives considered:** version-string bump via VCS/release tool
  — *rejected, empirically proven not to fix Defect A* (§3).

### Phase 1 — CI gate

`.github/workflows/ci.yml`, two jobs:

- **`check`** — matrix `[ubuntu-latest, windows-latest]`,
  `fail-fast: false` (an ubuntu failure must not mask a Windows-only
  break; prod is Windows). Steps: `actions/checkout` with
  `fetch-depth: 0` + `fetch-tags: true` (git-describe needs history);
  `astral-sh/setup-uv` (v8.x, SHA-pinned in the real file)
  `enable-cache: true` `python-version: "3.12"`;
  `extractions/setup-just`; `uv sync --locked --all-packages`
  (**`--locked`**, not `--frozen` — CI *should* fail on a stale lock);
  `just check`; `uv cache prune --ci`.
- **`integration`** — `windows-latest` (bounce/refresh must hit the real
  prod shell). Spins an **ephemeral daemon** (temp HOME + free port
  pre-Phase-3; `--instance dev` once Phase 3 lands), polls
  `agent-core daemon status` until running, runs `pytest -m slow`
  (incl. the Phase 0 regression), always-teardown via
  `agent-core daemon stop`, uploads the daemon log on failure. Exact
  start command / readiness signal verified against the real daemon CLI
  at plan time.
- **Triggers:** `pull_request` + `push: [main]` + `workflow_dispatch`.
  Nothing scoped away to hide failures — full visibility.
- **Branch ruleset on `main`** (created from scratch; greenfield):
  require `check` (both OS) + `integration` green and branch up-to-date
  before merge; **owner on the bypass list** (emergency direct push
  preserved); **no** required reviews, signed commits, or linear history.
- **Pre-push hook = PRIMARY, required:** a `pre-push` git hook runs
  `just check` before every push, installed via a small
  `agent-core`/`just` recipe so humans and agents get it identically.
  `git push --no-verify` is the deliberate emergency escape hatch. Its
  purpose is to keep `main` green *by construction* so CI failures stay
  **rare and therefore meaningful**.
- **Notification posture (one-time, Jeff's account — not changed for
  him):** GitHub → Settings → Notifications → Actions → **"Send
  notifications for failed workflows only"**. Failures always email
  (the alarm Jeff wants); successes are silent. CI noise is controlled by
  *preventing failures* (pre-push), not by suppressing alerts: Jeff wants
  the failure alarm to keep working, just to fire rarely.
- **No `pre-commit` framework** (redundant with `just check` + the
  existing secret-scan hook, which is left untouched).
- **Alternatives considered:** ubuntu-only (rejected — never exercises
  the Windows prod shell); windows-only (rejected — loses cheap fast
  path); silence Actions emails / scope triggers to hide failures
  (rejected after Jeff's correction — he wants the alarm, just rarely).

### Phase 2 — Versioning & releases (middle depth)

Final combined per-member `pyproject.toml` shape (example: `core`):

```toml
[build-system]
requires = ["hatchling", "uv-dynamic-versioning"]
build-backend = "hatchling.build"

[project]
name = "agent-core"
dynamic = ["version"]            # replaces: version = "0.1.0"
requires-python = ">=3.12"
# ...existing dependencies unchanged...

[tool.hatch.version]
source = "uv-dynamic-versioning"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core"]

[tool.uv-dynamic-versioning]
fallback-version = "0.0.0"       # used only when .git absent (sdist)

[tool.uv]
cache-keys = [{ file = "pyproject.toml" }, { git = { commit = true } }]
```

- Tool: **`uv-dynamic-versioning`** (hatchling plugin, dunamai-backed —
  uv-workspace-native; chosen over `hatch-vcs`/`setuptools-scm` which pin
  a concrete version into metadata and need extra root-finding config in
  a monorepo). Version format e.g. `0.4.0.post7.dev0+g<sha>`.
- **Single lockstep `vX.Y.Z` tag series** for all 10 packages (they
  version-lock via `{ workspace = true }` and ship as one daemon deploy).
  Configure the tool's tag `pattern` to match only `^v\d+\.\d+\.\d+` so
  the existing `pepper-cutover-*` tags are ignored. Exact
  `pattern`/`style`/`bump` keys verified via context7 at plan time.
- The git-derived version is baked into the built wheel, so the daemon
  venv's installed metadata is a true "what's running" signal.
  **`daemon status` surfaces it** next to `installed_sha`.
- `uv.lock` interaction (research-verified): workspace members with a
  dynamic version omit the `version` field in `uv.lock` → **no lockfile
  thrash**, `--frozen` stays valid for the daemon. One-time `uv lock`
  after the metadata switch (CI's `--locked` catches it if forgotten).
- **Releases:** towncrier (already configured per-package) drives the
  changelog. A release = add fragments + `towncrier build` + an
  annotated `vX.Y.Z` tag. No version-bump commit — the tag *is* the
  version input.
- **`.git` absence:** only sdist/tarball builds lack `.git`;
  `fallback-version` covers them. The daemon path always builds from the
  checkout (has `.git`).
- **Alternatives considered:** cache-key-only / no version string (most
  Google-like, viable but loses the readable "what's running" win); full
  SemVer automation via release-please/commitizen + Conventional-Commits
  enforcement (rejected — ceremony for a non-published repo; both the
  research and the Google model say skip it).

### Phase 3 — Dev/prod instance-parameterization

- The daemon takes an **instance selector**:
  `agent-core daemon <start|stop|status|refresh> --instance {prod|dev}`,
  env `AGENT_CORE_INSTANCE`, **default `prod`**.
- **PROD** (default): port **8789**, home `~/.agent-core/` — the live
  Pepper+Wren home (their busproxy `.mcp.json` already targets
  `127.0.0.1:8789`).
- **DEV**: port **8788**, home `~/.agent-core-dev/` — separate venv,
  install-stamp, `bus.sqlite`, scheduler, state, `endpoints.d`, logs.
- **Shared:** the git tree, the (now commit-keyed) uv cache, the
  `agent-core` CLI. **Isolated per instance:** venv, port, stamp, state
  DB, scheduler jobs, endpoints.d, logs.
- **Safety invariant:** a dev `refresh` / bounce / crash can **never**
  touch prod's venv, port, or state. Live Pepper & Wren keep running on
  8789 while iteration happens on 8788.
- **Backward compatibility:** with no `--instance`, behavior resolves to
  `prod` with today's exact paths/port — existing Pepper/Wren behavior is
  unchanged; the dev instance is purely additive.
- **Alternatives considered:** worktree-based dev (additive, not a
  replacement — still needs port/home separation); daemon-only Docker
  (kept as an optional future artifact only; agents never containerized).

### Phase 4 — Daemon auto-start at boot

- New commands `agent-core daemon install-autostart [--instance prod]`
  and `uninstall-autostart`. Registers a **Windows Task Scheduler** task:
  trigger = at Jeff's logon; action = the **prod** venv's
  `agent-core.exe daemon start --instance prod`; settings =
  restart-on-failure (small retry count), start-when-available,
  ignore-new-instance, no execution time limit; no admin (binds
  localhost only). Idempotent (re-run replaces). **Prod instance only.**
- **Pure/impure split** (project convention, mirrors
  `build_uv_sync_command`/`run_install`):
  `build_autostart_task(*, instance, agent_core_exe, account) -> str`
  is pure (returns the Task Scheduler XML / `schtasks` argv,
  deterministic, no I/O); `install_autostart(...)` is the thin impure
  shell that runs the single `schtasks /create /xml … /f`.
- Exact `schtasks` vs `Register-ScheduledTask` mechanism and absolute
  prod-venv exe path verified against the real CLI at plan time.
- **Pepper-session auto-launch is OUT of scope** (its blocker — the
  Claude Code onboarding/preview prompt on version drift — is unsolved).
  One pointer line links it to the always-on-Pepper goal so the 24/7
  thread is not lost (the always-on-Pepper 24/7 goal, tracked
  separately).

## 5. Testing strategy

All fast tests run in `just check` (hence pre-push and the CI `check`
job); slow/integration run in the CI `integration` job.

- **Phase 0:** guard unit test parses every `packages/*/pyproject.toml`
  and asserts the full expected `cache-keys` list; slow integration:
  commit → `daemon refresh` → installed file reflects the change.
- **Phase 2:** built-wheel version matches the `git describe` shape;
  `fallback-version` used when `.git` absent; `daemon status` surfaces
  the version; assert `uv.lock` unchanged after a commit (no thrash).
- **Phase 3:** isolation tests — starting/refreshing dev leaves prod's
  venv/port/home/stamp/state provably untouched; no-`--instance`
  resolves to prod with today's paths.
- **Phase 4:** unit tests on the pure builder — parse the emitted XML
  and assert trigger (logon, correct account), action (absolute prod-venv
  exe, args `daemon start --instance prod`), every settings value;
  determinism (byte-identical output for identical inputs, snapshot);
  `instance="dev"` rejected; builder's task name == the name
  `uninstall` targets (idempotent replace); exe path with spaces
  correctly escaped (tested against what Task Scheduler would actually
  reject — the fake must mirror the real tool's rejection behavior, not a
  happy-path stub). Thin-wrapper test
  monkeypatches the subprocess runner and asserts correct `schtasks`
  verb + error surfacing. Real registration is a **manual acceptance
  step** in the runbook (CI cannot reboot Windows; registering a real
  task would test Microsoft's scheduler, not our code). Boundary
  principle: unit-test the artifact we own; do not test the OS.

## 6. Risks → mitigations

| Risk | Mitigation |
|---|---|
| `cache-keys` replaces uv defaults; omitting the pyproject file entry breaks metadata invalidation | Guard test asserts the *whole* expected list, not just the git key |
| A future package omits the cache-key (Defect A returns) | Guard test fails `just check`/CI |
| `.git` absent on an sdist build → version plugin fails | `fallback-version`; the daemon path always has `.git` |
| Forgot `uv lock` after the dynamic-version switch | CI `uv sync --locked` fails fast |
| Instance default not prod → Pepper/Wren break | Explicit `prod` default + a test asserting no-`--instance` == prod paths/port |
| Auto-start launches a stale daemon | Freshness is Phase 0's job (commit-keyed cache); runbook notes the ordering dependency (0 before 4) |
| Windows Task Scheduler path/logon quirks | Absolute path to the prod-venv `agent-core.exe`; mechanism verified at plan time; manual reboot acceptance step |
| CI failures become ignorable noise | Pre-push keeps failures rare; "failed-only" email posture keeps the alarm meaningful |

## 7. Rollout

Each phase = its own PR, independently revertible.

1. **Phase 0** — standalone PR; merge; deploy; surgical-clean verify one
   last time; update docs + the daemon-refresh memory to retire the
   manual procedure.
2. **Phase 1** — CI workflow + pre-push hook recipe + branch ruleset PR.
   Every later PR is CI-gated from here.
3. **Phases 2 & 3** — independent CI-gated PRs; may interleave.
4. **Phase 4** — after Phase 3 (targets the prod instance).

## 8. Explicitly out of scope

- Pepper-session auto-launch (separate future brainstorm; blocker
  unsolved — the always-on-Pepper 24/7 goal, tracked separately).
- PyPI publishing (design stays PyPI-compatible — PEP 440 versions,
  standard wheels — but publishing is not built).
- SemVer release automation (release-please / python-semantic-release /
  commitizen) and Conventional-Commits enforcement.
- Docker (optional future reproducible-build artifact only; agents are
  never containerized).
- Classic GitHub branch protection (rulesets only).

## 9. One-time setup (recorded so it is not lost)

- After Phase 2 metadata edits: `uv lock` once.
- Phase 1: create the `main` ruleset (`gh` call specified in the plan);
  set the GitHub Actions notification posture to "failed workflows only"
  (Jeff's account, manual).
- Phase 0 post-deploy: final manual surgical-clean verification, then
  memory/docs update.
