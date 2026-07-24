# Spec: session-scoped auto-start daemon fixture, replace skip-unless-live autouse (issue #509)

## Goal

Replace the skip-unless-live autouse fixture in `packages/agent-core-qa/tests/conftest.py:45-71` with a session-scoped fixture that auto-starts the `test` instance daemon from source, health-polls until ready, and kills the process tree on teardown. Wire the fast-6 qa scenarios into CI as a `release-gate` job that runs per-PR with no hand-started daemon. This is ticket B3 of the Track B testing / CI tech-debt programme; the design authority is `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (§B3, §D3).

---

## Acceptance criteria

- `pytest packages/agent-core-qa/tests/ -m 'not slow' --no-cov -n0` passes on a clean checkout with **no** manually started daemon; the fixture starts and stops the daemon automatically.
- After the session exits, no `agent-core bus run` or child process belonging to the test instance remains alive (verified by checking `~/.agent-core-test/daemon.pid` is absent or PID is dead).
- `test_install_identity_dynamic_keystone` is decorated `@pytest.mark.slow` and is excluded from the default `pytest -m 'not slow'` lane.
- The `daemon_liveness_required` autouse fixture at `packages/agent-core-qa/tests/conftest.py:45-71` is removed.
- A new `source_daemon` session-scoped fixture is defined in `packages/agent-core-qa/src/agent_core_qa/fixtures.py` and is importable as `pytest_plugins = ["agent_core_qa.fixtures"]`.
- `packages/agent-core-qa/tests/conftest.py` uses `pytest_plugins = ["agent_core_qa.fixtures"]` and replaces `daemon_liveness_required` with a session-scoped `auto_daemon(source_daemon)` autouse fixture.
- `packages/core/src/agent_core/daemon/config_template.py` TEST-instance config includes a `builtin.scheduler` endpoint named `scheduler` and a `builtin.stub` endpoint named `discord` (in addition to existing `stub` and `qa`).
- `.github/workflows/ci.yml` has a `release-gate` job (ubuntu-latest, `timeout-minutes: 10`) that runs `uv run pytest packages/agent-core-qa/tests/ -v -m 'not slow' --no-cov -n0`.
- All changed Python files are ruff-clean and carry correct type annotations (ready for the B5 `mypy --strict` pass on `agent-core-qa`).

---

## Approach

No GoF pattern applies. This is SRP: one fixture owns daemon lifecycle, one autouse fixture opts the whole session into that lifecycle, and the CI job owns the release gate.

**Replacing the skip-with-autouse pattern.** `daemon_liveness_required` (conftest.py:45-71) is a function-scoped autouse fixture that skips tests when the daemon is not reachable. The replacement is a session-scoped fixture (`source_daemon`) that *starts* the daemon, so by the time any test function runs the daemon is guaranteed alive. Function-scoped auto-skip logic is removed entirely because it is no longer needed.

**Fixture placement for Track A reuse.** The fixture lives in the installable package (`packages/agent-core-qa/src/agent_core_qa/fixtures.py`) rather than in `tests/conftest.py`. Track A's published-install gate (`#394`) can load it with `pytest_plugins = ["agent_core_qa.fixtures"]` in its own conftest and wire it with a different autouse wrapper, without coupling the two test suites. This follows the pytest [plugin module pattern](https://docs.pytest.org/en/stable/how-to/writing_plugins.html).

**Idempotent start.** Before spawning `agent-core daemon start --instance test`, the fixture TCP-probes port 8787. If the port is already accepting connections (developer already running the daemon), the fixture skips the start and, critically, skips the teardown kill — so it never pulls the rug from under a developer's manually-started daemon. In CI the port is always cold; the idempotent check is a no-op and costs one fast TCP attempt. When the port is cold, the fixture always runs `agent-core daemon init --force --instance test` before starting the daemon; the `--force` flag overwrites any existing `~/.agent-core-test/agent_core.yaml`, ensuring the config is always fresh with all required endpoints (including the `scheduler` and `discord` entries added in sub-request 1). Without `--force`, a second local `pytest` invocation would find an existing config file and `daemon init` would exit 1, breaking the fixture on every run after the first.

**Health-poll.** `agent-core daemon start --instance test` is fire-and-forget (it writes the PID file and returns; the daemon process runs detached). The fixture polls TCP-connect on `127.0.0.1:8787` with 0.2 s interval up to 30 s, then fails with a diagnostic message if the port never becomes ready.

**Teardown.** The fixture calls `agent-core daemon stop --instance test` in the `finally` block, which internally calls `kill_tree(pid)` (psutil recursive kill + `wait_procs` timeout=5 s). This is the same path used by the manual `daemon stop` command and guarantees no orphaned child processes.

**Config template extension.** `daemon init --instance test` scaffolds `~/.agent-core-test/agent_core.yaml` from `config_template.py`. Currently the TEST template only has `stub` and `qa`. Scenario 5 (scheduler CRUD) requires a `builtin.scheduler` endpoint named `scheduler`; scenario 6 (discord routing) requires a `builtin.stub` endpoint named `discord`. Both are added to the TEST template so `daemon init` produces a complete QA config without manual post-install editing.

**Scenario 3 slow-marking.** `test_install_identity_dynamic_keystone` already explains it does a real `daemon install` (10-minute torch download) in a sandbox. It should carry `@pytest.mark.slow` so it is excluded from the per-PR `-m 'not slow'` filter and runs only in the nightly/release lane.

**CI job.** The `release-gate` job runs on ubuntu-latest only (no Windows needed; the test daemon and QA scenarios are cross-platform by design but Linux is the canonical gate OS). It installs the full workspace, then runs `uv run pytest packages/agent-core-qa/tests/ -v -m 'not slow' --no-cov -n0`. The `-n0` flag is required: the session-scoped daemon fixture cannot be shared across xdist workers (each worker gets its own session), so parallel execution would start multiple daemons on the same port 8787, causing port conflicts.

---

## Sub-requests (topologically sorted)

1. **Extend `packages/core/src/agent_core/daemon/config_template.py`** — add `scheduler` and `discord` entries to the TEST-instance block so `daemon init --instance test` produces a complete QA config.

2. **Create `packages/agent-core-qa/src/agent_core_qa/fixtures.py`** — define the `source_daemon` session-scoped fixture (TCP-idempotency check, `daemon init --force --instance test`, `daemon start --instance test`, health-poll, yield URL, teardown via `daemon stop --instance test`). The `--force` flag ensures `daemon init` always regenerates `~/.agent-core-test/agent_core.yaml` from the current template when the port is cold — preventing failures on second-and-later invocations where the config file already exists. Annotate correctly for eventual `mypy --strict` coverage.

3. **Update `packages/agent-core-qa/tests/conftest.py`**:
   - Add `pytest_plugins = ["agent_core_qa.fixtures"]` at the top.
   - Remove `daemon_liveness_required` autouse fixture (lines 45-71).
   - Add `auto_daemon(source_daemon: str) -> None` session-scoped `autouse=True` fixture that simply depends on `source_daemon` to opt the whole conftest scope in.
   - Promote `daemon_url` to `scope="session"` (it returns a constant string; session scope avoids one redundant call per test).

4. **Update `packages/agent-core-qa/tests/test_install_identity_dynamic_keystone.py`** — add `@pytest.mark.slow` to `test_install_identity_dynamic_keystone` so it is excluded from the `-m 'not slow'` per-PR lane.

5. **Update `.github/workflows/ci.yml`** — add the `release-gate` job after the existing `slow-tests` job.

---

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/daemon/config_template.py` | **Modify** — add `builtin.scheduler` (name: scheduler) and `builtin.stub` (name: discord) to the TEST-instance config block |
| `packages/agent-core-qa/src/agent_core_qa/fixtures.py` | **Create** — `source_daemon` session-scoped pytest fixture |
| `packages/agent-core-qa/tests/conftest.py` | **Modify** — add `pytest_plugins`, remove `daemon_liveness_required`, add `auto_daemon` autouse session fixture, promote `daemon_url` to session scope |
| `packages/agent-core-qa/tests/test_install_identity_dynamic_keystone.py` | **Modify** — add `@pytest.mark.slow` decorator to the test function |
| `.github/workflows/ci.yml` | **Modify** — add `release-gate` job |

---

## Alternatives considered

1. **Keep `daemon_liveness_required` and layer an autouse start fixture on top.** The skip-then-start combination would race: if the start fixture runs after `daemon_liveness_required`, tests that run between them might skip before the daemon is ready. Two autouse fixtures with different scopes create fixture ordering ambiguity. Replacing entirely is cleaner.

2. **Define `source_daemon` directly in `tests/conftest.py` rather than in the installable package.** Easier but prevents Track A from reusing it via `pytest_plugins`. Track B's design doc explicitly requires shared placement (D3: "Fixture lives where both B3 and #394 can import it"). The installable-module approach is the idiomatic pytest solution.

3. **Run QA tests in the existing `check` job by adding `packages/agent-core-qa/tests` to `testpaths`.** This would force QA tests through the coverage gate (`--cov-fail-under=75`) and through the `-n auto` xdist run. The session-scoped daemon fixture conflicts with xdist multi-worker mode (each worker gets a separate session and would try to start a daemon on the same port). A separate job with `-n0` is the correct isolation boundary.

4. **Use `--instance source` instead of `--instance test`.** The source instance binds port 8788 and requires the workspace root to be on the `PATH` (it uses `_workspace_venv_python()`). The QA client hardcodes port 8787 as `DEFAULT_DAEMON_URL` and the `test` instance is specifically designed for QA validation (config template already provisions `qa` + `stub`). Using `--instance test` is the correct choice per D3 and the existing client default.

---

## Open questions

None. The approach is fully grounded in the codebase.

---

## Out of scope

- Track A #394 published-install fixture (that ticket writes its own autouse wrapper that calls `agent-core daemon install --release <tag>` before start; it reuses `source_daemon` as a building block).
- Adding `packages/agent-core-qa/tests` to root `testpaths` (out of scope; QA scenarios are not part of the main suite coverage gate).
- Adding `packages/agent-core-qa/src` to `[tool.mypy] files` (B5's job; B3 only writes annotated code that will be ready for B5).
- Briefs orchestrator wiring in the TEST daemon config (scenario 4 gracefully skips when the playbook is not found; configuring a full briefs setup is out of scope for B3).
- Voice endpoint wiring in the TEST daemon config (scenario 7 gracefully skips when `synthesize_speech` is not mounted; GPU setup is operator-side).
- Nightly / workflow_dispatch lane for scenario 3 (the keystone test is `@pytest.mark.slow` after this ticket; a separate nightly trigger job is out of scope for B3 and may land in the release workflow).
