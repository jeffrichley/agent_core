# Spec: add nightly workflow to run install-keystone (scenario 3) on schedule / dispatch (issue #511)

## Goal

Create `.github/workflows/nightly.yml` that runs the QA install-keystone test (scenario 3,
`test_install_identity_dynamic_keystone`) on a nightly cron schedule and on manual
`workflow_dispatch` only — keeping it out of the per-PR lane. This is ticket F-B3c in the
Track B testing/CI programme; the design authority is
`docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (§B3, Decision D3).

Pre-conditions already satisfied by F-B3b (#509 / #510):
- `test_install_identity_dynamic_keystone` is decorated `@pytest.mark.slow`, so it is already
  excluded from the per-PR `release-gate` job's `-m 'not slow'` filter.
- The `release-gate` job in `ci.yml` runs on `pull_request`, `push: main`, and
  `workflow_dispatch` — scenario 3 does not run per-PR.

What is missing: scenario 3 currently runs **nowhere** in CI (it is marked slow; the
`slow-tests` job covers `packages/core/tests` and `packages/agent-core-busproxy/tests` only;
no nightly trigger exists). This spec wires it up.

## Acceptance criteria

- `.github/workflows/nightly.yml` exists.
- The workflow triggers on `schedule` (cron `'0 3 * * *'`, 03:00 UTC daily) **and**
  `workflow_dispatch`; it does **not** trigger on `pull_request` or `push`.
- `workflow_dispatch` accepts an optional `release_tag` input (string, not required,
  default `''`).
- The workflow has one job, `install-keystone`, running on `ubuntu-latest` with
  `timeout-minutes: 30`.
- The job uses the same pinned action SHAs already in `ci.yml`:
  - `actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0` with
    `fetch-depth: 0` and `fetch-tags: true`.
  - `astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0` with
    `enable-cache: true` and `python-version: "3.12"`.
- The job includes `uv python install 3.12` (ensures a managed CPython exists for the
  daemon-install subprocess's `uv venv --python 3.12` calls).
- The job runs `uv sync --locked --all-packages`.
- The job runs:
  ```
  uv run pytest packages/agent-core-qa/tests/ -v -m slow --no-cov -n0
  ```
  with `env: AGENT_CORE_QA_RELEASE_TAG: ${{ inputs.release_tag || 'v0.3.0' }}`.
- For `workflow_dispatch` with a non-empty `release_tag` input, `AGENT_CORE_QA_RELEASE_TAG` is
  set to that value; for scheduled runs (`inputs.release_tag` evaluates to empty), it falls
  back to `v0.3.0`.
- The `release-gate` job in `.github/workflows/ci.yml` is **unchanged** — scenario 3
  continues to be excluded per-PR via `-m 'not slow'`.
- `just check` exits 0 on the resulting branch (the new YAML file has no effect on the
  Python gate).

## Approach

No GoF pattern applies; this is a straightforward CI plumbing change.

**Why a new file rather than adding `schedule` to `ci.yml`.** Adding a `schedule` trigger to
`ci.yml` would cause all three existing jobs (`check`, `slow-tests`, `release-gate`) to run
nightly too — generating unnecessary runner-minutes and noise. A separate `nightly.yml` keeps
the nightly scope to exactly what belongs there (the expensive install-keystone test) without
touching the per-PR gate. This follows the same separation already established between
`ci.yml`, `release.yml`, and `release-please.yml`.

**Job shape.** The `install-keystone` job mirrors the `release-gate` job's step sequence
(`checkout` → `setup-uv` → `uv python install 3.12` → `uv sync --locked --all-packages` →
`uv run pytest`) and extends it with `-m slow` instead of `-m 'not slow'`. The `auto_daemon`
session-scoped fixture (wired in `packages/agent-core-qa/tests/conftest.py`) starts and stops
the test-instance daemon around the session; scenario 3 additionally spawns a sandbox install
in a `/tmp/qa-<uuid>/` directory isolated by `AGENT_CORE_HOME`.

**`AGENT_CORE_QA_RELEASE_TAG` handling.** The test reads this env var and defaults to
`v0.3.0` when absent. Setting `${{ inputs.release_tag || 'v0.3.0' }}` in the step-level `env`
makes the nightly default explicit in the YAML (easier to update than the test file), while
`workflow_dispatch` invocations can override it to validate a specific release tag.

**`-n0` required.** The `source_daemon` fixture is session-scoped; `pytest-xdist` gives each
worker its own session, which would start multiple daemons racing on port 8787. `-n0` is
already used by `release-gate` for the same reason.

**`concurrency`.** `cancel-in-progress: false` — a nightly run that was already downloading
torch should not be cancelled if a second dispatch fires.

## Sub-requests (topologically sorted)

1. **Create `.github/workflows/nightly.yml`** with the exact shape described in the
   acceptance criteria: `schedule` + `workflow_dispatch` triggers; `install-keystone` job on
   ubuntu-latest; `timeout-minutes: 30`; `permissions: contents: read`; `concurrency` block
   with `cancel-in-progress: false`; the five steps (checkout, setup-uv,
   `uv python install 3.12`, `uv sync --locked --all-packages`, pytest with `-m slow`); and
   the `AGENT_CORE_QA_RELEASE_TAG` env var set at the step level.

## File-level changes

| File | Change |
|---|---|
| `.github/workflows/nightly.yml` | **Create** — nightly / dispatch workflow running the QA slow lane (`-m slow`) |

No other files change.

## Alternatives considered

1. **Add `schedule` to `ci.yml` and gate the new job with
   `if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'`.**
   Avoids a new file, but adding `schedule` to `ci.yml` would also trigger the existing
   `check`, `slow-tests`, and `release-gate` jobs nightly without their own `if:` guards —
   unless each job is also conditioned, which is fragile and breaks the CI invariant that
   every job in `ci.yml` runs on all three existing triggers. A separate file is cleaner.

2. **Run the keystone test in `release.yml` (on GitHub Release published).** The design doc
   explicitly calls for a **nightly** lane in addition to the release lane (§D3: "The heavy
   install-keystone (scenario 3, real torch install) stays in a release/nightly lane, not
   per-PR"). The release workflow fires only at publish time; nightly runs validate HEAD
   continuously and catch regressions before a release is cut. Both lanes are needed; this
   ticket wires the nightly one.

3. **Target the specific test file** (`test_install_identity_dynamic_keystone.py`) **instead
   of `-m slow`** to skip future slow QA tests that might be added. Forward-compatibility
   favours `-m slow` — new slow QA tests naturally belong in the same nightly lane, not
   requiring a YAML edit each time. `-m slow` is the correct filter.

## Open questions

None. All files were read. The pre-conditions (slow marker, `release-gate` job, `source_daemon`
fixture) are in place. The spec is fully grounded.

## Out of scope

- Track A #394 published-install gate (a separate workflow step in `release.yml` that calls
  `agent-core daemon install --release <tag>` against the published artifact; reuses
  `source_daemon` but is a different ticket).
- Updating `AGENT_CORE_QA_RELEASE_TAG` default away from `v0.3.0` when new releases ship —
  that is an operator concern (bump the input default or the test default at release time).
- Adding the nightly job to the Windows slow-tests matrix — the install-keystone test targets
  Linux paths (`/tmp/…`); Windows support is a separate concern.
- Any change to the Python gate (`ci.yml`, `pyproject.toml`, test code, or fixture logic).
