# Phase 2.5 — Release Artifacts + Bug Cleanup: Design

**Date:** 2026-05-20
**Status:** brainstormed; pending implementation plan
**Relationship to the maturity spec:** Extends Phase 2 (VCS-derived
versioning + towncrier) with a real release-artifact deploy model. It
also **retires** parts of Phase 2 (the towncrier setup, the
`just release` recipe) in favor of release-please. Where this document
and the Phase 2 spec disagree, **this document wins** for the items it
touches; everything else in Phase 2 (uv-dynamic-versioning, daemon-status
version surfacing, cache-keys with `tags = true`) stays.

---

## 1. Goal

Today's `agent-core daemon refresh` rebuilds wheels from the workspace
source on the deploy host. That model collided with three issues during
the v0.1.0 deploy on 2026-05-20:

- **B1** — `uv-dynamic-versioning` silently falls back to `0.0.0` when
  `uv sync` / `uv build` runs from the bare-repo top-level (uv's build
  isolation loses git visibility). Confirmed reproducible.
- **B2** — `daemon status` false-positive *"fallback — vulnerable to
  uv sync"* warning at `cli.py:164` when the status CLI is invoked from
  inside the daemon venv (e.g., when the workspace `.venv` is broken).
- **Windows file-lock thrash** — repeated `uv venv --clear` race against
  Windows handle-release timing during refresh.

The fix is structural: stop rebuilding on the deploy host. Build wheels
in CI, attach to a GitHub Release, install from the release artifacts.
This eliminates the entire class of "did the deploy host's workspace
build cleanly?" problems.

The phase also adopts **release-please** + **squash-merge** as the
release-management model, replacing manual `just release` + towncrier.
Conventional commits become a discipline; release-please automates
version bumps and CHANGELOG entries; the bot's release PR is the
shared-state confirm gate.

Out of scope (carried from umbrella spec): PyPI publishing, container
images, Docker.

## 2. Architecture overview

```
LOCAL                          GITHUB                              DAEMON BOX
─────                          ──────                              ──────────

agent does work                                                    daemon serving
in branch w/                                                       (whatever version)
many small
commits

  │
  ▼
gh pr create
  --title "feat(daemon): ..."   PR title lint workflow
  ──────────────────────────▶   validates title
                                CI (check + integration) runs
                                ruleset requires all green

owner SQUASH-merges via UI      conventional commits on main
                                ─────────────────────────────▶
                                release-please bot watches main
                                opens/updates release PR
                                "chore(release): X.Y.Z"
                                (or merges with existing release PR)

  ...more PRs accumulate...

owner merges release PR via UI  release-please:
                                  - tags vX.Y.Z on the merge commit
                                  - creates GH Release with auto
                                    CHANGELOG-from-commits body
                                  ───────────▶ "release: published" event
                                                ▼
                                release.yml workflow:
                                  - checkout vX.Y.Z
                                  - uv build --all-packages --wheel
                                  - gh release upload vX.Y.Z dist/*.whl
                                                │
                                                ▼
                                GH Release page now has 10 .whl assets

owner deploys:                                                     ▼
agent-core daemon refresh ◀───────────────────────────  fetch latest release
   (no flag = latest, or                                  download wheels
    --release vX.Y.Z to pin)                              uv pip install
                                                          --force-reinstall
                                                          --no-deps
                                                         start daemon
                                                         (clients reconnect)
```

**Six enforcement layers** keep the wrong thing hard:

1. **GH repo setting:** only squash-merge allowed (disable merge-commit
   and rebase-merge buttons).
2. **PR title lint workflow:** required check on every PR; PR can't
   merge unless title matches Conventional Commits regex.
3. **`phase1-main-gate` ruleset extension:** adds PR title check as a
   required status check.
4. **release-please bot:** owns version + CHANGELOG, opens release PR.
5. **release.yml workflow:** owns wheel build + attachment to GH Release.
6. **Client-side `daemon install --release`:** only path to put code in
   the daemon venv (source-based install removed entirely).

## 3. Components

### 3.1 `release-please-config.json` (repo root)

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "release-type": "simple",
  "packages": { ".": {} },
  "include-component-in-tag": false,
  "tag-separator": "v",
  "bump-minor-pre-major": false,
  "bump-patch-for-minor-pre-major": false
}
```

**`release-type: "simple"`** is the critical choice: it tells
release-please **not** to touch any source files for version bumping —
no `pyproject.toml` rewrites, no `__version__` updates. release-please
only:
1. Tracks current version in `.release-please-manifest.json`.
2. Computes next version from conventional commits.
3. Updates `CHANGELOG.md` (root) with a new release section.
4. Creates the tag on the merge commit.

`uv-dynamic-versioning` (already in place from Phase 2) reads the tag
at build time and derives the version. release-please and
uv-dynamic-versioning are perfectly orthogonal: release-please owns tag
creation, uv-dynamic-versioning owns reading the tag.

### 3.2 `.release-please-manifest.json` (repo root)

```json
{ ".": "0.1.0" }
```

Bootstrapped to the current released version (`0.1.0`). release-please
updates this file in every release PR.

### 3.3 `.github/workflows/release-please.yml`

Triggers on push to `main`. Runs the release-please action; bot opens
or updates the release PR based on commits since the last tag.

Permissions: `contents: write` + `pull-requests: write` (the bot creates
PRs and tags).

### 3.4 `.github/workflows/release.yml`

Triggers on `release: published` event (release-please fires this when
it creates the GH Release).

Steps:
1. `actions/checkout` with `ref: github.event.release.tag_name`,
   `fetch-depth: 0`, `fetch-tags: true`. The checkout is a normal
   (non-bare) worktree — **B1 sidestepped structurally.**
2. `astral-sh/setup-uv` (SHA-pinned).
3. `uv build --all-packages --wheel --out-dir dist/` — produces 10
   wheels with correct version baked in by uv-dynamic-versioning.
4. `gh release upload <tag> dist/*.whl` — attach wheels to the release
   (uses `GITHUB_TOKEN`).

Permissions: `contents: write` (to upload release assets).

### 3.5 `.github/workflows/pr-title-lint.yml`

Triggers on `pull_request: [opened, edited, synchronize]`. Runs
`amannn/action-semantic-pull-request` (SHA-pinned). Required types:
`feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `style`, `build`,
`ci`, `perf`, `revert`. Permissions: `pull-requests: read`.

### 3.6 `packages/core/src/agent_core/daemon/release.py` (new module)

Pure-ish functions, all HTTP I/O isolated behind a thin interface so
the unit tests can fake it:

```python
def resolve_version(version: str | None, *, repo: str, fetcher) -> str:
    """Resolve None -> latest release tag. 'vX.Y.Z' -> 'vX.Y.Z' (unchanged).
    Raises NoReleasesError if version is None and no releases exist.
    """

def list_release_wheels(version: str, *, repo: str, fetcher) -> list[WheelAsset]:
    """Query GH API for the release; return its .whl assets (name + download URL)."""

def download_wheels(assets: list[WheelAsset], dest: Path, *, fetcher) -> list[Path]:
    """Download each asset to dest/, skipping if file exists and size matches.
    Returns the local Path of each wheel."""

def install_wheels(wheel_paths: list[Path], venv_python: Path) -> None:
    """Run `uv pip install --python <venv_python> --force-reinstall --no-deps <wheels>`."""
```

`fetcher` is a callable `(url) -> bytes` so tests use a stub. Default
implementation uses `urllib.request` (stdlib, no extra dependency).

Repo URL is hardcoded to `jeffrichley/agent_core` for now; future
generalization (Phase 3+) can add a `--repo` flag.

Local cache lives at `~/.agent-core/releases/<tag>/`. The download step
checks for existing files first — re-installing the same version is a
no-op fetch.

### 3.7 `daemon/cli.py` changes

**`install` command:**
- Add `--release VERSION` option (default `None` = latest).
- **Remove** workspace-source code path entirely. Calls
  `find_workspace_root` and the existing `run_install` (uv venv + uv
  sync) are deleted.
- New flow: `resolve_version` → `list_release_wheels` →
  `download_wheels` → `install_wheels` → write install stamp.

**`refresh` command:**
- Same `--release` option pass-through.
- Flow: `stop` → `install(...)` → `start`.

**`status` command — B2 fix:**
- Replace the existing `if daemon_py == sys.executable:` check at
  `cli.py:164` (false-positive when status is run from inside the
  daemon venv) with `if not _daemon_venv_exists():` where
  `_daemon_venv_exists()` is a small helper that returns True iff the
  daemon venv's python is on disk:
  ```python
  def _daemon_venv_exists() -> bool:
      if sys.platform == "win32":
          return (_home() / ".venv" / "Scripts" / "python.exe").exists()
      return (_home() / ".venv" / "bin" / "python").exists()
  ```
  Intent: warn only when **no daemon venv exists**, regardless of which
  Python is running the status command. Same condition `_daemon_python()`
  already uses for its fallback logic — the two stay in lockstep.
- Also remove the lock-drift check (it's against workspace lock, which
  is now decoupled from prod). Optionally replace with a release-drift
  check that queries GH for the latest release tag and warns if newer
  than the installed version — **stretch goal, not required for Phase
  2.5**; flag for follow-up.

### 3.8 Install stamp schema change

Current schema:
```json
{
  "installed_at": "...",
  "installed_sha": "...",
  "python_version": "...",
  "extra": "...",
  "uv_lock_hash": "..."
}
```

New schema:
```json
{
  "installed_at": "...",
  "installed_sha": "...",
  "installed_version": "0.1.0",   // NEW — explicit version stamp
  "python_version": "...",
  "extra": "...",
  "release_tag": "v0.1.0"          // NEW — provenance: which GH Release
}
```

Removed: `uv_lock_hash` (lockfile no longer involved in the install).

Added:
- `installed_version` — saves the runtime `importlib.metadata.version`
  query in `daemon status`; also lets a tampered/partial install be
  detected (stamp says 0.1.0 but importlib reports 0.0.0 → corrupt).
- `release_tag` — provenance for "where did this come from."

**Migration of existing stamps:** stamps written by Phase 2 code carry
`uv_lock_hash` but lack `installed_version` and `release_tag`. The new
read code treats unknown fields as silently ignored and missing new
fields as `None`. On the next `install` / `refresh`, a fresh stamp is
written in the new schema; no explicit migration step. The lock-drift
check, which used `uv_lock_hash`, is also removed in §3.7 — so the
field becomes unread on read, simply discarded on the next write.

### 3.9 `justfile` cleanup

**Remove:**
- `release VERSION` recipe (towncrier + tag — replaced by release-please).

**Add (optional, not required):**
- `daemon-deploy VERSION` recipe that's a thin wrapper around
  `agent-core daemon refresh --release vX.Y.Z` with a confirmation
  prompt. **Stretch goal; not required for Phase 2.5.**

### 3.10 towncrier retirement

- Delete `[tool.towncrier]` from root `pyproject.toml`.
- Delete `changelog.d/` directory (10 subdirs with `.gitkeep` files).
- Remove `towncrier` from dev dependencies in root `pyproject.toml`.
- One-time `uv lock` to refresh.
- `CHANGELOG.md` itself is preserved — release-please will append to it
  from this point forward.
- `docs/setup/releases.md` is rewritten to describe the new flow.

### 3.11 `.mcp.json` cleanup

The uncommitted change from earlier today (workspace `.mcp.json`
emptied — notify entry removed because it's now agent-scoped via the
per-agent `~/.pepper/` and `~/.wren/` configs) is included in this PR.

### 3.12 Branch ruleset extension

The existing `phase1-main-gate` ruleset on `main` requires:
- `check (ubuntu-latest)` green
- `check (windows-latest)` green
- `integration` green
- Branch up-to-date

Phase 2.5 adds:
- `validate (pr-title-lint)` green (the PR title lint job)

Owner bypass list stays unchanged. The ruleset update is a one-time
GitHub UI action — documented in `docs/setup/releases.md`.

### 3.13 Repo setting: squash-only merge

GitHub Settings → General → Pull Requests:
- ✅ Allow squash merging
- ❌ Allow merge commits
- ❌ Allow rebase merging

This is a one-time toggle — survives forever. Documented in
`docs/setup/releases.md`.

## 4. Daily workflow (post-Phase-2.5)

### Developing a feature

```
git checkout -b feat/some-thing
# many small commits, any messages — agent or human, doesn't matter
git push -u origin feat/some-thing
gh pr create --title "feat(daemon): support custom port" --body "..."
# pr-title-lint passes; ci passes
# owner squash-merges via GH UI (only enabled merge button)
```

That conventional PR title becomes the single squash-commit on `main`.

### Cutting a release

```
# release-please bot opens (or updates) a release PR titled
# "chore(release): 0.2.0" automatically. It contains:
#   - CHANGELOG.md updated with entries derived from PR titles since v0.1.0
#   - .release-please-manifest.json bumped to 0.2.0

# When ready to ship, merge the release PR via GH UI.
# release-please:
#   - tags v0.2.0 on the merge commit
#   - creates a GH Release with body = the CHANGELOG section it just wrote
#   - that fires release: published event
#
# release.yml workflow:
#   - builds 10 wheels (uv-dynamic-versioning bakes 0.2.0 from the tag)
#   - uploads them as GH Release assets
```

### Deploying

```
# Production daemon box:
agent-core daemon refresh              # → installs latest GH Release
# or
agent-core daemon refresh --release v0.2.0   # → pin to specific version

# Verify:
agent-core daemon status   # → "installed version: 0.2.0"
# Pepper/Wren reconnect automatically (bus design from Phase 0-2)
```

### Rolling back

```
agent-core daemon refresh --release v0.1.0
# Downloads from cache (~/.agent-core/releases/v0.1.0/) if present,
# else fetches fresh from GH. Daemon restarts on the older version.
```

## 5. Testing strategy

All fast (Phase 1 `check` gate); integration handled by the release
flow itself (a real release will exercise it end-to-end).

**Unit tests** (`packages/core/tests/`):

- **`test_release_resolve_version`** — `resolve_version(None, ...)` calls
  the latest endpoint and returns the tag; `resolve_version("v0.1.0", ...)`
  returns unchanged; `resolve_version(None, ...)` raises NoReleasesError
  when fetcher returns empty.
- **`test_release_list_wheels`** — given fixture GH-API JSON, returns
  exactly the `.whl` assets (filters out other extensions).
- **`test_release_download_skip`** — second call with same destination
  and matching size skips the download.
- **`test_release_install_invokes_uv_pip`** — uses a fake subprocess
  runner; asserts the exact `uv pip install --python ... --force-reinstall --no-deps`
  command shape.
- **`test_daemon_status_no_false_positive_fallback`** (B2 regression) —
  invoke `status` with daemon venv present; assert no "fallback" warning
  is printed, regardless of which python is running the CLI.
- **`test_install_stamp_schema_v2`** — new stamp shape parses correctly;
  old shape with `uv_lock_hash` is migrated or fails cleanly.

**Integration / acceptance** (manual, post-merge):

- After the Phase 2.5 PR merges and v0.2.0 is cut via release-please,
  run `agent-core daemon refresh` on the box (with no `--release` flag)
  and confirm `installed version: 0.2.0`, Wren+Pepper reconnect.

## 6. Risks → mitigations

| Risk | Mitigation |
|---|---|
| First release-please run produces a confusing release PR | Bootstrap manifest at `0.1.0` so bot starts from a sensible baseline; preview by configuring the bot one PR earlier than the actual cutover (or accept that the first release PR is a learning artifact) |
| Squash-merge loses per-commit history on main | Acknowledged trade-off; PR commits remain in PR history forever |
| `uv build --all-packages` produces a wheel for `agent-core-briefs` (et al.) that the daemon doesn't strictly need | Install them anyway; they're tiny and harmless. Future: filter to daemon-relevant packages only |
| GH API rate limits on download | Local cache means most installs hit cache, not GH. Unauthenticated rate limit (60/hr/IP) is plenty for this scale |
| Stamp schema change breaks existing daemon installs | Stamp read code gracefully treats missing fields as `unknown` and continues |
| release-please bot doesn't open a PR when expected | Check workflow logs; ensure conventional commits since last tag exist |
| amannn/action-semantic-pull-request goes unmaintained | Standard pattern; could swap for an inline regex check in ~10 lines |
| Removed source-based install hurts dev iteration | Daemon code is dev-tested by running it from workspace `.venv` directly, not by reinstalling into the prod daemon. Phase 3's dev daemon instance is the proper testing mode. |

## 7. Rollout

1. **Phase 2.5 PR** containing items in §3 → passes `phase1-main-gate`
   (still the existing gate; PR title lint is added IN this PR but only
   becomes a required check after the ruleset update in step 2) → squash
   merged via PR through the gate.
2. **One-time GH UI actions (after merge):**
   - Toggle repo settings to squash-only merge.
   - Update `phase1-main-gate` ruleset to add `validate (pr-title-lint)`
     as a required check.
3. **Bootstrap release flow:**
   - Bot's first release PR appears automatically on the next push to
     main (which is step 1's merge).
   - Merge it as `v0.2.0` (or `v0.1.1` if release-please decides the
     conventional commits in the PR warrant only a patch).
   - `release.yml` builds wheels and uploads.
4. **Deploy:**
   - `agent-core daemon refresh` on the box → installs latest
     automatically.
   - Verify `daemon status` reports the new version.

## 8. Explicit uncertainties (flagged for plan-time verification)

These are bits where I'm not 100% sure of the exact API/syntax; the
implementation plan should verify with context7 or a small test:

- **U1:** Exact `release-please-config.json` schema fields. The
  `release-type: "simple"` + manifest mode combination is documented
  but the precise key names (`include-component-in-tag`,
  `tag-separator`, `bump-minor-pre-major`) should be verified against
  the current release-please version.
- **U2:** Whether release-please's release PR creates the tag at PR
  merge time or at separate workflow run time. The `release.yml`
  `on: release: published` trigger depends on release-please firing
  that event, which I believe it does, but verify.
- **U3:** `gh release upload` from inside the `release.yml` workflow
  needs `GITHUB_TOKEN` with `contents: write` permission. Confirm the
  default `GITHUB_TOKEN` has enough scope; might need a `pat` instead.
- **U4:** The `amannn/action-semantic-pull-request` action's exact
  parameter shape and SHA to pin.
- **U5:** uv's behavior when `uv pip install --force-reinstall --no-deps`
  is invoked with the daemon Python — specifically whether it correctly
  refreshes `*.dist-info` metadata for already-installed-but-different-
  version wheels on Windows. Today's session confirmed it works
  (0.6s, surgical), so likely fine — but verify in tests.

## 9. What stays from Phase 2

For clarity, here's what Phase 2 work survives Phase 2.5:

- `uv-dynamic-versioning` on all 10 members (the way wheels get versions)
- `[tool.uv] cache-keys` with `tags = true` (Phase 0/2 cache invalidation)
- `daemon status` printing `installed version:`
- `phase1-main-gate` branch ruleset (extended, not replaced)
- The Phase 1 CI workflow (`check` + `integration` jobs)
- The pre-push hook + `just install-hooks`

What Phase 2 work is retired:
- `[tool.towncrier]` config + `changelog.d/` directory
- `just release` recipe
- `towncrier` dev dependency
- The "humans write fragments per PR" discipline (replaced by
  "conventional PR titles")
