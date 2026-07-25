# Spec: ci.yml `release-gate` job for the fast-6 QA scenarios (issue #510)

## Goal

Add (or verify) a `release-gate` job in `.github/workflows/ci.yml` that runs the fast 6 QA scenarios per-PR against an auto-started test-instance daemon, making daemon and round-trip regressions fail PRs automatically without any hand-started daemon. This is ticket F-B3b of the Track B testing/CI programme; the design authority is `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (§B3, Decision D3).

## Acceptance criteria

- `.github/workflows/ci.yml` has a `release-gate` job that fires on all top-level triggers (`pull_request`, `push: branches: [main]`, `workflow_dispatch`).
- The job runs on `ubuntu-latest` with `timeout-minutes: 10`.
- The job steps are: `actions/checkout` (with `fetch-depth: 0` and `fetch-tags: true`), `astral-sh/setup-uv` (with `python-version: "3.12"`), `uv sync --locked --all-packages`, then `uv run pytest packages/agent-core-qa/tests/ -v -m 'not slow' --no-cov -n0`.
- The `-m 'not slow'` filter excludes `test_install_identity_dynamic_keystone` (decorated `@pytest.mark.slow`) leaving exactly 6 scenarios.
- The `-n0` flag disables pytest-xdist parallelism so the session-scoped `source_daemon` fixture is shared across all 6 tests in a single worker session; running xdist would start multiple daemon processes on the same port and cause port conflicts.
- `packages/agent-core-qa/src/agent_core_qa/fixtures.py` defines a `source_daemon` session-scoped fixture that: TCP-probes port 8787 for idempotency; when the port is cold, runs `agent-core daemon init --force --instance test` (regenerates config from current template) then `agent-core daemon start --instance test` (fire-and-forget detached start); polls TCP on `127.0.0.1:8787` at 0.2 s intervals up to 30 s; yields the daemon URL; tears down via `agent-core daemon stop --instance test` in the `finally` block.
- `packages/agent-core-qa/tests/conftest.py` declares `pytest_plugins = ["agent_core_qa.fixtures"]`, a session-scoped `auto_daemon(source_daemon: str) -> None` autouse fixture, and no `daemon_liveness_required` autouse fixture.
- `packages/core/src/agent_core/daemon/config_template.py` TEST-instance block includes both a `builtin.scheduler` entry named `scheduler` and a `builtin.stub` entry named `discord` (needed by scenarios 5 and 6).
- A broken daemon start (e.g. `daemon init` exits non-zero) causes the session fixture to raise, which fails all 6 scenarios — a broken daemon/round-trip path fails the PR.
- No daemon process belonging to the test instance remains after the session exits.

## Approach

No GoF pattern applies. This is SRP at the CI layer: one job owns the release gate, one fixture owns daemon lifecycle, one autouse wrapper opts the session in. The discipline is from the design spec D3: "Build a session-scoped fixture that **auto-starts a source-installed daemon** … Run the fast qa scenarios **per-PR in `ci.yml`**."

**Current state (verified against working tree).** Investigating the repository reveals that the F-B3a Worker (#509) implemented the full B3 scope, including the `release-gate` CI job. As of this writing all components appear to be in place:

- `.github/workflows/ci.yml` lines 84-98: `release-gate` job present with the correct shape.
- `packages/agent-core-qa/src/agent_core_qa/fixtures.py`: `source_daemon` session-scoped fixture defined with idempotency check, `--force` init, health-poll, teardown.
- `packages/agent-core-qa/tests/conftest.py`: `pytest_plugins = ["agent_core_qa.fixtures"]`, session-scoped `auto_daemon` autouse, no `daemon_liveness_required`.
- `packages/agent-core-qa/tests/test_install_identity_dynamic_keystone.py`: `@pytest.mark.slow` present.
- `packages/core/src/agent_core/daemon/config_template.py`: TEST block includes `builtin.scheduler` (name: scheduler) and `builtin.stub` (name: discord).

**Worker task.** Read each file listed in the acceptance criteria and verify it satisfies the criteria exactly. If all criteria are met as found, no code change is needed — the ticket is done and the Worker should note that in the implementation record. If any gap is found (missing job, wrong flag, absent fixture, missing slow mark, missing config entry), implement the targeted fix.

**Scenario count.** There are 7 test files in `packages/agent-core-qa/tests/`. With `-m 'not slow'`, the keystone (scenario 3) is excluded, leaving 6. Scenarios 4 (briefs) and 7 (voice) skip gracefully in CI because their endpoints are not wired in the TEST daemon config template — this is intentional per the design spec ("scenario 4 gracefully skips when the playbook is not found"; "scenario 7 gracefully skips when `synthesize_speech` is not mounted"). Skip != fail; these two skips are acceptable in the per-PR lane.

## Sub-requests (topologically sorted)

1. **Read `packages/core/src/agent_core/daemon/config_template.py`** — confirm the TEST-instance block contains both `builtin.scheduler` (name: `scheduler`) and `builtin.stub` (name: `discord`) in addition to the `qa` MCP endpoint and `stub` endpoint. If either entry is absent, add it.

2. **Read `packages/agent-core-qa/src/agent_core_qa/fixtures.py`** — confirm `source_daemon` is `scope="session"`, uses `sys.executable -m agent_core.cli daemon init --force --instance test` and `sys.executable -m agent_core.cli daemon start --instance test`, health-polls `127.0.0.1:8787`, yields `http://127.0.0.1:8787`, and tears down via `daemon stop --instance test` in a `finally` block. If absent or incorrect, implement or fix it.

3. **Read `packages/agent-core-qa/tests/conftest.py`** — confirm `pytest_plugins = ["agent_core_qa.fixtures"]` is present, `auto_daemon(source_daemon: str) -> None` is a session-scoped autouse fixture, `daemon_url` is session-scoped, and `daemon_liveness_required` is absent. If any gap, fix it.

4. **Read `packages/agent-core-qa/tests/test_install_identity_dynamic_keystone.py`** — confirm `@pytest.mark.slow` decorates the test function. If absent, add it.

5. **Read `.github/workflows/ci.yml`** — confirm the `release-gate` job exists with the exact shape specified in the acceptance criteria (ubuntu-latest, timeout 10 min, checkout with full history, setup-uv with python 3.12, `uv sync --locked --all-packages`, `uv run pytest packages/agent-core-qa/tests/ -v -m 'not slow' --no-cov -n0`). If absent or incorrect, add/fix it.

6. **If any gap was found and fixed, confirm ruff-clean.** Run `uv run ruff check <changed file>` on any modified Python file. CI won't catch this at the `release-gate` job level (no ruff step there), but the `check` job will, so keep it clean.

## File-level changes

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | **Verify / add** — `release-gate` job (ubuntu-latest, timeout 10 min, full workspace sync, `pytest … -v -m 'not slow' --no-cov -n0`) |
| `packages/agent-core-qa/src/agent_core_qa/fixtures.py` | **Verify** — `source_daemon` session-scoped fixture; fix if absent or incorrect |
| `packages/agent-core-qa/tests/conftest.py` | **Verify** — `pytest_plugins`, session-scoped `auto_daemon` autouse, no `daemon_liveness_required`, session-scoped `daemon_url`; fix if gap found |
| `packages/agent-core-qa/tests/test_install_identity_dynamic_keystone.py` | **Verify** — `@pytest.mark.slow` present; add if absent |
| `packages/core/src/agent_core/daemon/config_template.py` | **Verify** — TEST block has `scheduler` and `discord` entries; add if absent |

All five files are expected to already satisfy the criteria based on the current working-tree investigation. The Worker should treat each sub-request as a verification step and implement a targeted fix only if a gap is discovered.

## Alternatives considered

1. **Restrict the `release-gate` job to `pull_request` events only via `if: github.event_name == 'pull_request'`.** The issue uses the phrase "per-PR" but restricting to PRs means post-merge regressions on `main` go undetected until the next PR. Running on `pull_request` + `push:branches:[main]` + `workflow_dispatch` (inherited from the top-level `on:` block, no `if:` condition needed) is strictly better at zero extra cost.

2. **Add `packages/agent-core-qa/tests` to root `testpaths` and run QA scenarios inside the existing `check` job.** The `check` job uses `-n auto` (pytest-xdist), which conflicts with the session-scoped daemon fixture: each xdist worker spawns its own Python session, causing multiple fixture invocations that race to bind port 8787. A separate `release-gate` job with `-n0` is the correct isolation boundary. It also avoids forcing QA scenarios through the coverage gate (`--cov-fail-under=85`), which is unrelated to release validation.

3. **Use `needs: [check]` to gate `release-gate` on the main suite passing first.** B3 is listed as a root ticket with no dependencies in the design spec. Adding `needs:` introduces unnecessary coupling — a flaky unit test in `check` would block the daemon smoke gate from even running, making failures harder to diagnose.

## Open questions

None. The implementation appears complete based on the current codebase investigation. All five components (daemon config template, `source_daemon` fixture, `conftest.py` wiring, slow marker, CI job) are in place.

## Out of scope

- Nightly / `workflow_dispatch`-only lane for scenario 3 (the slow install-identity keystone): `@pytest.mark.slow` excludes it from the per-PR lane; a separate nightly trigger is a future concern, not F-B3b.
- Track A #394 published-install fixture: a separate ticket writes its own autouse wrapper that calls `agent-core daemon install` before delegating to `source_daemon`.
- Adding `packages/agent-core-qa/src` to `[tool.mypy] files` at `--strict`: that is B5's responsibility; F-B3b only requires the code to be annotated correctly in anticipation of B5.
- Wiring briefs orchestrator or voice endpoint into the TEST daemon config (scenarios 4 and 7 gracefully skip when their endpoints are absent; full wiring is out of scope for B3).
