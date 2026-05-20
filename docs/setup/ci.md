# CI & the pre-push gate

## One-time per clone or worktree

On a fresh clone OR each new `git worktree add`, in this order:

```
uv sync          # or: just sync  — populates the env
just install-hooks
```

**`core.hooksPath` is per-worktree-local in git.** It does NOT carry over
when you `git worktree add` a new linked worktree — each fresh worktree
starts with the default `.git/hooks` (empty) and the pre-push hook will
NOT fire until you run `just install-hooks` there. If you forget, the
fast guard test `packages/core/tests/test_hookspath_local_guard.py` fails
on the next `just check` with a clear "run `just install-hooks`" message
(the guard skips in CI, where the hook isn't needed).

`just install-hooks` runs `uv run --no-sync python -m agent_core.githooks`,
which points `core.hooksPath` at the version-controlled `.githooks/`. It
uses `--no-sync`, so the uv environment must already exist — run `uv sync`
(or `just sync`) FIRST, otherwise `agent_core` is not importable and the
recipe fails.

Git will not auto-run committed hook code on a fresh clone, so this
one-time bootstrap is required. Humans and agents run the identical
recipe.

After install, `git push` runs `just check` first. Emergency bypass (use
sparingly): `git push --no-verify`.

## What CI runs (`.github/workflows/ci.yml`)

- **check** — `ubuntu-latest` + `windows-latest`, `fail-fast: false`,
  `timeout-minutes: 20`: `uv sync --locked --all-packages` then
  `just check` then `uv cache prune --ci`.
- **integration** — `windows-latest`, `timeout-minutes: 20`: a narrow
  Torch-free sync `uv sync --locked --package agent-core`, then the
  self-contained slow suite `uv run --no-sync pytest packages/core/tests
  -m slow -v` (the Phase 0 stale-cache regression). The narrow sync is
  intentional — `--all-packages` here would pull multi-GB Torch the
  hosted runner cannot use; do not "widen" it.

Triggers: every PR, every push to `main`, and manual `workflow_dispatch`.
All third-party actions are pinned by commit SHA. `concurrency` cancels
superseded runs for PRs only — `push: main` runs always finish so every
commit on `main` has a recorded check.

## One-time GitHub setup (owner account; manual)

- Branch ruleset on `main` (exact `gh api` call in the Phase 1 plan):
  requires `check (ubuntu-latest)`, `check (windows-latest)`, and
  `integration` green and the branch up to date; owner on the bypass
  list; no required reviews / signed commits / linear history.
- Settings → Notifications → Actions → **"Send notifications for failed
  workflows only."** Failures email; successes are silent. Failures stay
  rare because the pre-push hook catches breakage locally.

## Related

- `docs/setup/releases.md` — VCS-derived versioning and how to cut a release.
