# Spec: mark two subprocess tests slow and add Linux slow lane (issue #401)

## Goal

Mark `test_start_writes_pid_file_and_stop_kills` and `test_kill_tree_terminates_subprocess` with `@pytest.mark.slow` so they leave the default `just check` lane, then extend the `slow-tests-windows` CI job to run on both `ubuntu-latest` and `windows-latest`. Without the Linux lane, these daemon/process-tree tests would run on Windows only after the marking — Linux-specific process-kill behavior would never be exercised in CI. See issue #401 and design spec `docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (Decision D6).

## Acceptance criteria

- `uv run pytest --collect-only -m 'not slow' packages/core/tests/test_daemon_cli.py packages/core/tests/test_daemon_supervisor.py` does **not** list `test_start_writes_pid_file_and_stop_kills` or `test_kill_tree_terminates_subprocess`.
- `uv run pytest --collect-only -m slow packages/core/tests/test_daemon_cli.py packages/core/tests/test_daemon_supervisor.py` **does** list both tests.
- `.github/workflows/ci.yml` contains a slow-tests job (or renamed equivalent) whose `runs-on` is a matrix over `[ubuntu-latest, windows-latest]` — no longer Windows-only.
- `just check` (the default CI gate) passes with the same coverage floor as before.
- No unregistered `PytestUnknownMarkWarning` for the new decorator usages (the `slow` marker is already registered in `[tool.pytest.ini_options] markers` in `pyproject.toml`).

## Approach

No GoF pattern applies — these are two independent point changes with one responsibility each (mark a test; extend a CI matrix). The guiding principle is "make the right thing easy": after marking, both tests must actually run in CI, which requires the Linux slow lane.

**Test marking.** `test_start_writes_pid_file_and_stop_kills` (`test_daemon_cli.py:71`) already has `import pytest` at the module level (line 10) and an existing `@pytest.mark.slow` precedent on `test_prod_and_source_daemons_coexist` (line 563). Add only the decorator on line 71. `test_kill_tree_terminates_subprocess` (`test_daemon_supervisor.py:55`) has **no** `import pytest` import — both `import pytest` and the decorator must be added.

**CI change.** The existing `slow-tests-windows` job (`.github/workflows/ci.yml:56–79`) is a verbatim copy of the `check` job's checkout/setup steps, restricted to `windows-latest`. Converting it to a matrix over `[ubuntu-latest, windows-latest]` is the minimal change: add a `strategy.matrix.os` block, change `runs-on` from the literal to `${{ matrix.os }}`, and rename the job to `slow-tests` (removing the `-windows` suffix so the name matches its new scope). All other steps are identical across OSes — the same `uv python install 3.12`, `uv sync`, and `pytest` invocation work on both runners.

**Branch-protection note.** If `slow-tests-windows` is a required status check in GitHub branch-protection rules, the rename to `slow-tests` will cause a new check name to appear and the old required name to go missing. The Worker should verify branch-protection settings and update the required checks if needed, or keep the job name as `slow-tests-windows` and add a parallel `slow-tests-linux` job instead. The matrix approach is preferred for DRY; the branch-protection caveat is the one reason to fall back to a second parallel job.

## Sub-requests (topologically sorted)

1. **Add `@pytest.mark.slow` to `test_kill_tree_terminates_subprocess`** in `packages/core/tests/test_daemon_supervisor.py`

   The file has no `import pytest`. Add it to the imports block, then decorate the function:
   - Insert `import pytest` into the import block (after the stdlib imports, before the local imports — following the existing import order of the file).
   - Add `@pytest.mark.slow` on the line immediately before `def test_kill_tree_terminates_subprocess(tmp_path: Path):` (currently line 55).

2. **Add `@pytest.mark.slow` to `test_start_writes_pid_file_and_stop_kills`** in `packages/core/tests/test_daemon_cli.py`

   `import pytest` is already present (line 10). Add `@pytest.mark.slow` on the line immediately before `def test_start_writes_pid_file_and_stop_kills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):` (currently line 71). Match the style of the existing `@pytest.mark.slow` on `test_prod_and_source_daemons_coexist` (line 563).

3. **Convert `slow-tests-windows` to a matrix CI job** in `.github/workflows/ci.yml`

   Current job (lines 56–79):
   ```yaml
   slow-tests-windows:
     runs-on: windows-latest
     timeout-minutes: 20
     steps:
       ...
   ```

   Target shape:
   ```yaml
   slow-tests:
     strategy:
       fail-fast: false
       matrix:
         os: [ubuntu-latest, windows-latest]
     runs-on: ${{ matrix.os }}
     timeout-minutes: 20
     steps:
       - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0  # v7.0.0
         with:
           fetch-depth: 0
           fetch-tags: true
       - uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39  # v8.2.0
         with:
           enable-cache: true
           python-version: "3.12"
       - uses: extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3  # v4
       - run: uv python install 3.12
       - run: uv sync --locked --package agent-core --package agent-core-busproxy
       - run: uv run --no-sync pytest packages/core/tests packages/agent-core-busproxy/tests -m slow -v --no-cov
   ```

   Keep all pinned action hashes unchanged (copy from the existing job). Preserve the inline comments explaining why `--no-cov` is used and why voice/qwen-tts is excluded.

## File-level changes

| File | Change |
|---|---|
| `packages/core/tests/test_daemon_supervisor.py` | **Modify** — add `import pytest` to imports; add `@pytest.mark.slow` decorator to `test_kill_tree_terminates_subprocess` |
| `packages/core/tests/test_daemon_cli.py` | **Modify** — add `@pytest.mark.slow` decorator to `test_start_writes_pid_file_and_stop_kills` (line 71) |
| `.github/workflows/ci.yml` | **Modify** — rename `slow-tests-windows` to `slow-tests`; add `strategy.matrix.os: [ubuntu-latest, windows-latest]`; change `runs-on` to `${{ matrix.os }}` |

## Alternatives considered

1. **Add a separate `slow-tests-linux` job alongside the existing `slow-tests-windows` job** — works without renaming, avoids any branch-protection impact. Downside: duplicates ~20 lines of YAML, two job names to maintain. Preferred only if the branch-protection caveat applies; otherwise the matrix approach is strictly cleaner.

2. **Do nothing about the Linux slow lane — only mark the tests** — eliminates the flake but leaves daemon process-tree tests (`kill_tree`, full daemon start/stop) unexercised on Linux in CI. The design spec (D6) explicitly calls this out as unacceptable: process-tree behavior differs between Linux (SIGKILL + psutil) and Windows, so both OSes must be covered. Ruled out.

## Open questions

None. Both test files and the CI workflow were read directly. The only contingency (branch-protection requiring the `slow-tests-windows` job name) is a deploy-time check the Worker should handle by falling back to the parallel-job shape; it does not require clarification before writing the spec.

## Out of scope

- Fixing the lack of a `try/finally` teardown in `test_start_writes_pid_file_and_stop_kills` — the test's stop call is the cleanup, and CI runner teardown handles any leaked process after a timeout. Improving teardown robustness is a separate hardening concern.
- Any other slow-marked tests or new slow-lane tests beyond the two named in the issue.
- B2–B8 from the Track B design (notify, release gate, audit hoist, mypy, discord split, etc.) — all out of scope for this ticket.
