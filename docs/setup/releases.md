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
  → release.yml workflow builds wheels + requirements.txt, attaches them
  → on the daemon box: `agent-core daemon refresh` (no args = latest)
```

## One-time repo configuration (post-Phase-2.5 merge)

These are GitHub UI actions, one-time:

1. **Repo Settings → General → Pull Requests:** uncheck "Allow merge commits"
   and "Allow rebase merging". Leave only "Allow squash merging".
2. **Repo Settings → Branches → `phase1-main-gate` ruleset → Required status
   checks:** add `Validate PR title` (the job name from `pr-title-lint.yml`).
   Existing required checks (`check (ubuntu-latest)`, `check (windows-latest)`,
   `integration`) stay.

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
agent-core daemon refresh                       # latest release
agent-core daemon refresh --release v0.1.0      # specific version (rollback)
```

`refresh` does: `stop` → fetch wheels + requirements.txt from GH Release →
ensure venv exists → `uv pip install --requirement requirements.txt` →
`uv pip install --force-reinstall --no-deps <wheels>` → `start`.

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
previously-used version is offline-fast (no re-download).
