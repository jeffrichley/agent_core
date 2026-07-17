# Spec: release.yml → PyPI Trusted Publishing (OIDC) for all 12 packages (issue #392)

## Goal

Extend `.github/workflows/release.yml` to build all 12 installable workspace packages as wheels + sdists and publish them to PyPI via OIDC Trusted Publishing (no long-lived secret stored). TestPyPI is published first as an irreversibility guard; the existing GitHub-Release wheel feed is kept in parallel. Addresses issue #392 under the Track A packaging sub-project described in `docs/superpowers/specs/2026-07-16-theme-f-track-a-pypi-launch-design.md`.

**Dependency:** This ticket assumes A1-1 (workspace `{ workspace = true }` refs replaced with real version pins in each package's `[project] dependencies`) is already merged. The `uv build` step will produce broken `Requires-Dist` metadata if run before A1-1.

---

## Acceptance criteria

- `.github/workflows/release.yml` is restructured into 4 jobs: `build`, `publish-testpypi`, `publish-pypi`, `upload-github-release`.
- The `build` job builds all 12 packages listed below — wheels **and** sdists — by invoking `uv build --package <name> --out-dir dist/` in a loop. The 13th workspace member `qwen-tts` (vendored at `packages/agent-core-voice/vendor/Qwen3-TTS`) is **not** built.
- The 12 target packages are: `agent-core`, `agent-core-briefs`, `agent-core-busproxy`, `agent-core-channel`, `agent-core-credentials`, `agent-core-discord`, `agent-core-hatchery`, `agent-core-inbound`, `agent-core-notify`, `agent-core-qa`, `agent-core-voice`, `agent-core-webcam`.
- Build artifacts are uploaded via `actions/upload-artifact` (SHA-pinned) so the three downstream jobs can consume them without re-running a checkout.
- `publish-testpypi` runs after `build`, is bound to the `testpypi` GitHub Environment, has `permissions: id-token: write`, and uses `pypa/gh-action-pypi-publish` (SHA-pinned) with `repository-url: https://test.pypi.org/legacy/` and `packages-dir: dist/`.
- `publish-pypi` runs after `publish-testpypi` succeeds (`needs: publish-testpypi`), is bound to the `pypi` GitHub Environment, has `permissions: id-token: write`, and uses `pypa/gh-action-pypi-publish` with `packages-dir: dist/` (defaults to pypi.org — no `repository-url` needed).
- `upload-github-release` runs after `build` (parallel to the PyPI chain; `needs: build`), has `permissions: contents: write`, exports `requirements.txt` via the same `uv export` command as today, and uploads `dist/*.whl dist/requirements.txt` to the GitHub Release via `gh release upload` (matching current scope — sdists go to PyPI but not the GH Release feed).
- The workflow-level `permissions:` block is narrowed to `contents: read` (the minimal grant that allows `actions/checkout` to function). Each job overrides with only what it requires.
- All actions are SHA-pinned with a version comment (e.g., `pypa/gh-action-pypi-publish@<SHA>  # release/v1`), matching the convention in `ci.yml` and the existing `release.yml`.
- **Human pre-conditions** (not code changes; must be complete before the workflow can successfully publish):
  - 12 Pending Publishers configured on **test.pypi.org**: publisher type = GitHub Actions, repository = `jeffrichley/agent_core`, workflow filename = `release.yml`, environment name = `testpypi`.
  - 12 Pending Publishers configured on **pypi.org**: same, environment name = `pypi`.
  - `testpypi` and `pypi` **GitHub Environments** created in the repo's Settings → Environments. (The `pypi` environment should add a required-reviewer protection rule so a human approves the PyPI push after verifying TestPyPI went cleanly.)
- `uv add agent-core` from pypi.org succeeds in a clean venv after the first release (verified by a human after the first publish; automated gate is A1-4 scope).

---

## Approach

No GoF pattern fits this work. It is CI pipeline plumbing. The design follows Google's "make the right thing easy" principle: OIDC Trusted Publishing makes the no-stored-secret path the **only** path, consistent with Theme D's secrets posture cited in the design doc.

**Why 4 jobs instead of 1.** OIDC `id-token` tokens are scoped per job. GitHub's official guidance for Trusted Publishing requires `id-token: write` on the job that calls the publish action; granting it workflow-wide exposes the token to the build job unnecessarily. Separating `publish-testpypi` and `publish-pypi` into independent jobs also creates a natural gate: `publish-pypi` only runs if TestPyPI succeeds, catching bad upload configs before the irreversible pypi.org push. The `upload-github-release` job is parallel to the PyPI chain so a PyPI hiccup does not block the GitHub Release (which existing users may depend on for `agent-core daemon install`).

**Per-package `uv build` instead of `--all-packages`.** The workspace has 13 members (line 6–9 of `pyproject.toml`): the 12 publishable packages under `packages/` plus `packages/agent-core-voice/vendor/Qwen3-TTS`. There is no `--exclude-package` flag on `uv build`, so `--all-packages` would build `qwen-tts` and include it in `dist/`. The safe alternative is to invoke `uv build --package <name>` in a shell loop over the explicit list of 12 names. This makes the intended publish surface auditable from a direct reading of the workflow.

**Sdists.** The current command uses `--wheel` (wheels only). Removing the flag causes `uv build` to build both wheel and sdist by default, which is the correct PyPI publication shape. PyPI users on platforms without a prebuilt wheel can install from the sdist; sdists are also required for certain downstream tooling.

**Permissions narrowing.** The current workflow has a top-level `permissions: contents: write` which grants that permission to all jobs including the build step. The narrower pattern — `permissions: contents: read` at workflow level, per-job override — follows the principle of least privilege. Job-level permissions fully replace the workflow-level grant for that job in GitHub Actions (they do not accumulate).

**TestPyPI as rehearsal.** The design doc identifies "first-publish irreversibility" as the primary risk (§8). Publishing to TestPyPI in `publish-testpypi` (which runs first, before `publish-pypi`) validates the upload machinery — OIDC token, artifact shape, version string — against a forgiving index before committing the name+version to pypi.org. The `pypi` GitHub Environment's required-reviewer gate gives a human a final check window between TestPyPI success and the real upload.

**GitHub Release feed preserved.** The `upload-github-release` job replicates the existing `gh release upload dist/*.whl dist/requirements.txt` call. Sdists are not added to the GitHub Release because the daemon's `install` command fetches wheels (not sdists) from GH Releases. The two feeds (PyPI and GH Release) are kept in parallel as directed by the issue ("keep the private GitHub-Release feed in parallel for now").

---

## Sub-requests (topologically sorted)

1. **Create GitHub Environments** (`testpypi`, `pypi`) in Settings → Environments. Add at least one required reviewer to the `pypi` environment. (Human one-time step; blocks CI; done before merging the workflow change.)

2. **Register 12 Pending Publishers on test.pypi.org**: for each of the 12 package names, create a Pending Publisher (GitHub Actions, repo `jeffrichley/agent_core`, workflow `release.yml`, environment `testpypi`). (Human one-time step.)

3. **Register 12 Pending Publishers on pypi.org**: same 12 packages, environment `pypi`. (Human one-time step.)

4. **Rewrite `.github/workflows/release.yml`** — replace the single `build-and-upload` job with 4 jobs:

   **`build` job** (checkout + build):
   ```yaml
   build:
     runs-on: ubuntu-latest
     timeout-minutes: 20
     permissions:
       contents: read
     steps:
       - uses: actions/checkout@<SHA>  # v7.0.0
         with:
           ref: ${{ github.event.release.tag_name }}
           fetch-depth: 0
           fetch-tags: true
       - uses: astral-sh/setup-uv@<SHA>  # v8.2.0
         with:
           python-version: "3.12"
       - name: Build wheels and sdists for all 12 packages
         run: |
           for pkg in agent-core agent-core-briefs agent-core-busproxy agent-core-channel \
                      agent-core-credentials agent-core-discord agent-core-hatchery \
                      agent-core-inbound agent-core-notify agent-core-qa \
                      agent-core-voice agent-core-webcam; do
             uv build --package "$pkg" --out-dir dist/
           done
       - name: List built artifacts
         run: ls -la dist/
       - name: Upload build artifacts
         uses: actions/upload-artifact@<SHA>  # v4
         with:
           name: dist
           path: dist/
   ```

   **`publish-testpypi` job** (publish to TestPyPI):
   ```yaml
   publish-testpypi:
     needs: build
     runs-on: ubuntu-latest
     timeout-minutes: 10
     permissions:
       id-token: write
     environment:
       name: testpypi
       url: https://test.pypi.org/p/agent-core
     steps:
       - name: Download build artifacts
         uses: actions/download-artifact@<SHA>  # v4
         with:
           name: dist
           path: dist/
       - name: Publish to TestPyPI
         uses: pypa/gh-action-pypi-publish@<SHA>  # release/v1
         with:
           repository-url: https://test.pypi.org/legacy/
           packages-dir: dist/
   ```

   **`publish-pypi` job** (publish to PyPI):
   ```yaml
   publish-pypi:
     needs: publish-testpypi
     runs-on: ubuntu-latest
     timeout-minutes: 10
     permissions:
       id-token: write
     environment:
       name: pypi
       url: https://pypi.org/p/agent-core
     steps:
       - name: Download build artifacts
         uses: actions/download-artifact@<SHA>  # v4
         with:
           name: dist
           path: dist/
       - name: Publish to PyPI
         uses: pypa/gh-action-pypi-publish@<SHA>  # release/v1
         with:
           packages-dir: dist/
   ```

   **`upload-github-release` job** (keep existing GH Release feed):
   ```yaml
   upload-github-release:
     needs: build
     runs-on: ubuntu-latest
     timeout-minutes: 15
     permissions:
       contents: write
     steps:
       - uses: actions/checkout@<SHA>  # v7.0.0
         with:
           ref: ${{ github.event.release.tag_name }}
           fetch-depth: 0
           fetch-tags: true
       - uses: astral-sh/setup-uv@<SHA>  # v8.2.0
         with:
           python-version: "3.12"
       - name: Download build artifacts
         uses: actions/download-artifact@<SHA>  # v4
         with:
           name: dist
           path: dist/
       - name: Export pinned requirements (cu130 extra)
         run: |
           uv export --frozen --no-dev --extra cu130 --no-emit-workspace \
                     --no-hashes --format requirements-txt \
                     > dist/requirements.txt
       - name: Upload artifacts to GH Release
         env:
           GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
         run: gh release upload ${{ github.event.release.tag_name }} dist/*.whl dist/requirements.txt
   ```

5. **Look up and substitute SHA pins** for `actions/upload-artifact@v4`, `actions/download-artifact@v4`, and `pypa/gh-action-pypi-publish@release/v1` — following the `grep -r 'actions/checkout'` pin convention visible in `ci.yml`. Use `gh api repos/<owner>/<action-repo>/git/ref/tags/<tag>` or `git ls-remote` to resolve SHA from tag.

---

## File-level changes

| File | Change |
|------|--------|
| `.github/workflows/release.yml` | **Rewrite** — replace single `build-and-upload` job with 4 jobs: `build`, `publish-testpypi`, `publish-pypi`, `upload-github-release`; narrow workflow-level permissions; pin new action SHAs |

---

## Alternatives considered

1. **Keep a single job with sequential steps instead of splitting into 4 jobs.** Simpler YAML. Ruled out: OIDC `id-token: write` must be granted per-job (not just per-step), and a single job would require granting it to the checkout/build steps unnecessarily, widening the attack surface. Per-job scoping is the security invariant PyPI's Trusted Publishing model depends on.

2. **Use `uv build --all-packages` and `rm dist/qwen_tts-*` to exclude `qwen-tts`.** Slightly shorter. Ruled out: the remove step is fragile (silently a no-op if the package is renamed or moves) and obscures the intended publish surface. The explicit per-package loop is self-documenting and cannot accidentally publish `qwen-tts`.

3. **Only publish to TestPyPI on pre-release tags and to real PyPI on full releases.** Would avoid TestPyPI cost on every release. Ruled out: the issue explicitly says "Rehearse on TestPyPI first"; the design doc identifies first-publish irreversibility as a top risk. Paying the TestPyPI round-trip on every release is cheap insurance given the 12-package breadth.

4. **Upload sdists to the GitHub Release as well as PyPI.** Symmetric. Ruled out: the GitHub Release feed is consumed by `agent-core daemon install`, which only installs wheels. Adding sdists to the GH Release would bloat the release assets without benefiting any existing consumer. PyPI users who need sdists (non-wheel platforms) get them from PyPI.

---

## Open questions

None that block the spec. The Worker needs to look up current SHA pins for three new actions (`upload-artifact@v4`, `download-artifact@v4`, `pypa/gh-action-pypi-publish@release/v1`) at implementation time — these cannot be resolved without internet access. This is a routine pin-lookup step, not an architectural uncertainty.

---

## Out of scope

- A1-1 (workspace refs → pinned `Requires-Dist`): prerequisite ticket, not part of this change.
- A1-2 (release-please single synchronized version train): separate ticket.
- A1-4 (agent-core-qa round-trip gate against a real (Test)PyPI install): separate ticket.
- Configuring the Trusted Publishers on PyPI and TestPyPI via API (the `pypa/trusted-publishing` CLI): the one-time setup is done manually in the browser; automating it is YAGNI at this scale.
- Retiring the GitHub-Release wheel feed: explicitly deferred per the issue ("keep the private GitHub-Release feed in parallel for now").
- Adding `skip-existing: true` to the TestPyPI publish step: not needed for a release-triggered workflow (each release has a unique version); adds complexity without benefit.
- Publishing `qwen-tts` to PyPI: explicitly excluded per the design doc (§2 Non-goals).
