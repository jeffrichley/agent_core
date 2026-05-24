# Phase 3.5 — Three-instance daemon (`prod` / `source` / `test`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec:** `docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md` (commit `5adf770`).
>
> **Branch:** `feat/phase35-three-instance-test` (worktree at `.worktrees/phase35-three-instance-test/`).

**Goal:** Extend Phase 3's `Instance` enum from `{prod, dev}` to `{prod, source, test}` via hard-cutover rename + new `test` instance that installs from release wheels via the SAME `release.py` code path as `prod`.

**Architecture:** Three changes inside Phase 3's existing design surface. (1) Rename `Instance.DEV` to `Instance.SOURCE` and add `Instance.TEST`, with `home_for` / `default_port` mappings. (2) CLI accepts the new choice list and routes `--instance test` through prod's install path (with the test home substituted). (3) Autostart (PR #110) rejection list expands to `{source, test}`. `release.py` is unchanged — instance-agnostic by design — and a keystone enforcer test asserts the install code path is identical between prod and test modulo the home path.

**Tech Stack:** Python 3.12, uv workspace, pytest, pytest-asyncio, ruff, mypy.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `packages/core/src/agent_core/daemon/instance.py` | `Instance` enum + `home_for` + `default_port` + `resolve_instance` | Modify (rename DEV→SOURCE, add TEST) |
| `packages/core/tests/test_daemon_instance.py` | Enum / home / port / resolve unit tests | Modify (rename + add TEST cases + parse-error test) |
| `packages/core/src/agent_core/daemon/cli.py` | `--instance` flag, install/refresh/init/start/stop routing | Modify (choice list + new test handling) |
| `packages/core/tests/test_daemon_cli.py` | CLI behavior tests | Modify (rename + add test-instance + parse-error tests) |
| `packages/core/src/agent_core/daemon/config_template.py` | `build_default_config(instance)` | Modify (add TEST branch) |
| `packages/core/tests/test_daemon_config_template.py` | Config-template unit tests | Modify (rename + add TEST scaffold test) |
| `packages/core/src/agent_core/daemon/release.py` | Pure-function install orchestrator | NO CHANGES (keystone anchor) |
| `packages/core/tests/test_daemon_release.py` | Release-install unit tests + NEW keystone enforcer | Modify (add `test_install_code_path_identity_between_prod_and_test`) |
| `packages/core/tests/test_dynamic_versioning.py` | Dynamic-versioning tests | Modify (rename any dev → source) |
| `packages/core/tests/test_daemon_three_instance_coexistence.py` | NEW coexistence integration test | Create |
| `docs/setup/daemon.md` | User-facing three-instance docs | Modify (rewrite the dev/prod section as prod/source/test) |
| `docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md` | Spec doc | Modify (line 213 typo fix: "dev daemon" → "test daemon") |

---

## Phase 1 — `Instance` enum, `home_for`, `default_port`

### Task 1: Rename `Instance.DEV` to `Instance.SOURCE`; add `Instance.TEST`

**Files:**
- Modify: `packages/core/src/agent_core/daemon/instance.py`
- Modify: `packages/core/tests/test_daemon_instance.py`

- [ ] **Step 0 (preflight): read the current `instance.py`**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
cat packages/core/src/agent_core/daemon/instance.py
cat packages/core/tests/test_daemon_instance.py
```

This confirms the existing enum / function signatures so the rename is mechanical, not a re-invention. Note the existing tests; you'll rename `dev` references to `source` and add new tests for `TEST`.

- [ ] **Step 1: Write the failing tests**

Edit `packages/core/tests/test_daemon_instance.py`. Replace every existing `DEV` / `dev` reference with `SOURCE` / `source`, and append the following new tests:

```python
def test_instance_test_value():
    """TEST instance is the third value, used for sandboxed deploy validation."""
    assert Instance.TEST == "test"
    assert Instance.TEST.value == "test"


def test_home_for_test_instance():
    """TEST home is ~/.agent-core-test/ to keep state disjoint from prod/source."""
    expected = Path.home() / ".agent-core-test"
    assert home_for(Instance.TEST) == expected


def test_default_port_test_instance():
    """TEST port is 8787 (decrementing from prod=8789, source=8788)."""
    assert default_port(Instance.TEST) == 8787


def test_resolve_instance_rejects_dev_after_rename():
    """Hard cutover (Phase 3.5): --instance dev is no longer accepted; the
    parse error directs callers to the new {prod, source, test} choice set."""
    with pytest.raises(ValueError) as exc:
        Instance("dev")
    # The StrEnum's built-in error names the bad value; the actual error
    # message format depends on the Python version, but "dev" should appear.
    assert "dev" in str(exc.value)
```

The renamed source tests should look like (mirror of any existing `dev` tests):

```python
def test_instance_source_value():
    """SOURCE (renamed from DEV in Phase 3.5) — runs editable from the workspace .venv."""
    assert Instance.SOURCE == "source"
    assert Instance.SOURCE.value == "source"


def test_home_for_source_instance():
    """SOURCE home is ~/.agent-core-source/ (renamed from -dev/)."""
    expected = Path.home() / ".agent-core-source"
    assert home_for(Instance.SOURCE) == expected


def test_default_port_source_instance():
    """SOURCE port is 8788 (unchanged from Phase 3's dev port)."""
    assert default_port(Instance.SOURCE) == 8788
```

Keep / preserve every other existing test in the file that doesn't reference `dev`. The full file's test count after edits: prior-count + 4 new TEST tests + 1 parse-error test, with the existing dev tests renamed to source.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_instance.py -v
```

Expected: the renamed tests FAIL (looking for `Instance.SOURCE`, `home_for(Instance.SOURCE)` etc. which don't exist). The new TEST tests FAIL (looking for `Instance.TEST` which doesn't exist). The parse-error test PASSES (the existing enum already rejects unknown values).

- [ ] **Step 3: Update `instance.py`**

In `packages/core/src/agent_core/daemon/instance.py`:

```python
class Instance(StrEnum):
    PROD = "prod"
    SOURCE = "source"  # renamed from DEV in Phase 3.5
    TEST = "test"      # Phase 3.5 NEW
```

Update `home_for`:

```python
def home_for(instance: Instance) -> Path:
    return {
        Instance.PROD: Path.home() / ".agent-core",
        Instance.SOURCE: Path.home() / ".agent-core-source",
        Instance.TEST: Path.home() / ".agent-core-test",
    }[instance]
```

Update `default_port`:

```python
def default_port(instance: Instance) -> int:
    return {
        Instance.PROD: 8789,
        Instance.SOURCE: 8788,
        Instance.TEST: 8787,
    }[instance]
```

If `instance.py` has any other reference to the literal string `"dev"` (e.g., in `resolve_instance`'s env-var precedence logic), update it. Search:

```bash
grep -n "dev" packages/core/src/agent_core/daemon/instance.py
```

If hits exist, replace `dev` with `source` (or remove if it was a vestigial reference).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_instance.py -v
```

Expected: all tests PASS (renamed source tests + new TEST tests + parse-error test).

- [ ] **Step 5: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add packages/core/src/agent_core/daemon/instance.py packages/core/tests/test_daemon_instance.py
git commit -m "feat(daemon): rename Instance.DEV to SOURCE + add TEST (Phase 3.5)"
```

---

## Phase 2 — CLI surface

### Task 2: Update `cli.py` `--instance` choice list + add test-instance handling

**Files:**
- Modify: `packages/core/src/agent_core/daemon/cli.py`
- Modify: `packages/core/tests/test_daemon_cli.py`

- [ ] **Step 0 (preflight): read the current `cli.py`**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
cat packages/core/src/agent_core/daemon/cli.py | head -150
grep -n "dev\|DEV\|Instance" packages/core/src/agent_core/daemon/cli.py
```

Note: the `--instance` flag is defined via Click / Typer. Find the choice-list / type-validator. Find the `install` command's source-rejection error message. Find any `install-autostart` command (if PR #110 hasn't merged, it may not exist — that's fine; the rejection-list expansion lives in PR #110's rebase, not Phase 3.5's PR).

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_daemon_cli.py` (also rename any existing `dev` references to `source` first):

```python
def test_install_test_instance_succeeds_via_mock(monkeypatch, tmp_path):
    """daemon install --instance test routes through the prod install path
    against the test home. Mock release.py to verify the install was attempted
    against the test home, not prod's."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path / "test_home"))
    captured: dict = {}

    def fake_install(home, release_tag):
        captured["home"] = home
        captured["release"] = release_tag
        return {"status": "installed"}

    monkeypatch.setattr(
        "agent_core.daemon.cli._do_install", fake_install
    )

    result = runner.invoke(
        daemon_app,
        ["install", "--instance", "test", "--release", "v0.2.0"],
    )
    assert result.exit_code == 0, result.output
    assert captured["home"] == tmp_path / "test_home"
    assert captured["release"] == "v0.2.0"


def test_install_source_instance_errors():
    """daemon install --instance source remains a deliberate error (renamed
    from --instance dev). Source runs editable from workspace .venv; there's
    nothing to install."""
    result = runner.invoke(
        daemon_app, ["install", "--instance", "source", "--release", "v0.2.0"]
    )
    assert result.exit_code != 0
    assert "source" in result.output.lower()
    # Should NOT name "dev" in the error message anymore.
    assert "--instance dev" not in result.output


def test_unknown_instance_dev_parse_error():
    """Hard cutover: --instance dev parses as an unknown value, with a clear
    error message naming the new {prod, source, test} choice set."""
    result = runner.invoke(daemon_app, ["start", "--instance", "dev"])
    assert result.exit_code != 0
    msg = result.output.lower()
    # The CLI parser's error format depends on Click; the key point is that
    # "dev" is rejected and the user sees the valid choices.
    assert "dev" in msg
    # At least one of the valid choices should appear in the error / help.
    assert "prod" in msg or "source" in msg or "test" in msg
```

If the existing test file has fixtures like `runner`, `daemon_app`, reuse them. If not, add minimal ones at the top of the file matching what's already in adjacent test files.

The `_do_install` symbol name in the mock is a placeholder — the actual function name in `cli.py` may differ. After preflight Step 0, replace with the actual function the CLI calls into for the install routing.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: the new tests FAIL. `test_install_test_instance_succeeds_via_mock` fails because `--instance test` is not yet in the choice list. `test_install_source_instance_errors` fails because `--instance source` is not in the choice list. `test_unknown_instance_dev_parse_error` should already PASS structurally (the CLI rejects unknown values), but the rename-error message check may fail until the choices are updated.

- [ ] **Step 3: Update `cli.py`**

In `packages/core/src/agent_core/daemon/cli.py`:

1. Find the `--instance` flag's choice list / Click `click.Choice([...])`. Replace `["prod", "dev"]` with `["prod", "source", "test"]`.

2. Find the `install` command's source-rejection error path (it currently triggers on `instance is Instance.DEV` or string comparison `instance == "dev"`). Update:

```python
# In the install command body, where it currently rejects dev:
if instance is Instance.SOURCE:
    raise typer.BadParameter(
        "install on source is not supported — source runs editable "
        "from the workspace .venv; use `daemon start --instance source` directly."
    )
```

3. Find the `_do_install` (or equivalent) routing function. Ensure it uses `home_for(instance)` (already does, per the keystone — the function is instance-agnostic and just needs the right home passed in). Confirm no instance-specific branch is added; the function should work for both PROD and TEST identically.

4. Find the `init` command. If there's a TEST branch needed (e.g., to call `build_default_config(Instance.TEST)`), wire it. Otherwise the existing instance-aware logic should route correctly.

5. If `install-autostart` exists in this file (Phase 4 — PR #110 territory): it stays out of scope for Phase 3.5's PR. PR #110's rebase handles the rejection-list expansion.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_cli.py -v
```

Expected: all tests PASS.

Then run the broader daemon-test suite to catch any regression from the rename:

```bash
uv run pytest packages/core/tests/ -v -k daemon 2>&1 | tail -10
```

Expected: all daemon tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add packages/core/src/agent_core/daemon/cli.py packages/core/tests/test_daemon_cli.py
git commit -m "feat(daemon): CLI accepts {prod,source,test}; install --instance test routes via prod's install code"
```

---

## Phase 3 — Config template

### Task 3: Add TEST scaffold to `config_template.py`

**Files:**
- Modify: `packages/core/src/agent_core/daemon/config_template.py`
- Modify: `packages/core/tests/test_daemon_config_template.py`

- [ ] **Step 0 (preflight): read the current `config_template.py`**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
cat packages/core/src/agent_core/daemon/config_template.py
cat packages/core/tests/test_daemon_config_template.py
```

Note the existing `build_default_config(instance)` signature and the prod/dev branches.

- [ ] **Step 1: Write the failing tests**

Edit `packages/core/tests/test_daemon_config_template.py`. Rename any `dev` references to `source`, and append:

```python
def test_build_default_config_for_test_instance():
    """TEST scaffold uses port 8787 and the minimal builtin.stub endpoint
    default. Same structural shape as prod's scaffold, just rooted at the
    test home and on the test port."""
    config = build_default_config(Instance.TEST)
    assert config["http"]["bind_port"] == 8787
    assert config["bus"]["storage_path"].endswith(".agent-core-test/bus.sqlite") \
        or "agent-core-test" in str(config["bus"]["storage_path"])
    # One default endpoint of type builtin.stub
    endpoints = config.get("endpoints", [])
    assert any(e.get("type") == "builtin.stub" for e in endpoints)
```

The exact key paths (`http.bind_port`, `bus.storage_path`) may differ — adjust based on preflight Step 0's read of the actual config shape.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_config_template.py -v
```

Expected: new TEST test FAILS with `KeyError: Instance.TEST` or a similar branch-missing error.

- [ ] **Step 3: Add TEST branch to `build_default_config`**

In `config_template.py`, update the function to handle TEST. If the function uses a dict-dispatch pattern like `home_for`, add the TEST entry:

```python
def build_default_config(instance: Instance) -> dict:
    home = home_for(instance)
    port = default_port(instance)
    return {
        "http": {"bind_port": port},
        "bus": {"storage_path": str(home / "bus.sqlite")},
        "endpoints": [
            {"type": "builtin.stub", "name": "stub"},
        ],
    }
```

If the existing function has an if/elif chain on instance, add an `elif instance is Instance.TEST:` branch returning the same structural shape with the test home + test port substituted.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_config_template.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add packages/core/src/agent_core/daemon/config_template.py packages/core/tests/test_daemon_config_template.py
git commit -m "feat(daemon): build_default_config supports TEST instance"
```

---

## Phase 4 — Keystone enforcer test

### Task 4: `test_install_code_path_identity_between_prod_and_test`

**Files:**
- Modify: `packages/core/tests/test_daemon_release.py`
- NOT modified: `packages/core/src/agent_core/daemon/release.py` (this is the keystone anchor; non-change is load-bearing)

- [ ] **Step 0 (preflight): read the current `test_daemon_release.py`**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
cat packages/core/tests/test_daemon_release.py | head -80
grep -n "def " packages/core/tests/test_daemon_release.py
```

This shows the existing test patterns + helpers, so the new keystone test matches conventions.

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_daemon_release.py`:

```python
def test_install_code_path_identity_between_prod_and_test(monkeypatch, tmp_path):
    """KEYSTONE ENFORCER (Phase 3.5 spec §Testing).

    The single test that proves test-instance actually validates prod's
    deploy path: assert that `daemon install --instance test --release vX.Y.Z`
    invokes the SAME release.py functions with the SAME structural arguments
    as `daemon install --instance prod --release vX.Y.Z`, modulo the home path.

    Any future change that adds a function call to one path but not the other,
    or passes a structurally different argument shape, fails this test
    immediately. The keystone is enforced by code-equality, not docs.
    """
    calls: dict[str, list[tuple[str, tuple, dict]]] = {"prod": [], "test": []}

    def capture(name: str, instance_label: str):
        def _wrap(*args, **kwargs):
            calls[instance_label].append((name, args, kwargs))
            # Return a structurally-valid stub for each function
            return _stub_return_for(name)
        return _wrap

    def _stub_return_for(name: str):
        # Each release.py function gets a stub return that satisfies its caller.
        # The structural shape of the return matters only insofar as the call
        # sequence depends on it; for keystone identity, the stubs can be
        # symmetric across both runs.
        if name == "resolve_version":
            return "v0.2.0"
        if name == "list_release_wheels":
            return [{"name": f"agent_core_{i}-0.2.0-py3-none-any.whl",
                     "url": f"https://example/wheel-{i}"} for i in range(10)]
        if name == "download_wheels":
            return [tmp_path / f"w{i}.whl" for i in range(10)]
        if name == "download_requirements":
            return tmp_path / "requirements.txt"
        if name in ("ensure_venv", "install_requirements", "install_wheels"):
            return None
        if name == "write_stamp":
            return None
        return None

    # The 8 functions whose call sequence we capture.
    function_names = [
        "resolve_version",
        "list_release_wheels",
        "download_wheels",
        "download_requirements",
        "ensure_venv",
        "install_requirements",
        "install_wheels",
        "write_stamp",
    ]

    def patch_release_for(instance_label: str):
        for fn in function_names:
            monkeypatch.setattr(
                f"agent_core.daemon.release.{fn}",
                capture(fn, instance_label),
            )

    # --- Run 1: prod install against tmp_path/prod ---
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path / "prod"))
    patch_release_for("prod")
    _invoke_install(instance="prod", release_tag="v0.2.0")

    # --- Run 2: test install against tmp_path/test ---
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path / "test"))
    patch_release_for("test")
    _invoke_install(instance="test", release_tag="v0.2.0")

    # --- Normalize home-path components and assert identity ---
    prod_norm = _normalize_calls(calls["prod"], tmp_path / "prod")
    test_norm = _normalize_calls(calls["test"], tmp_path / "test")

    assert prod_norm == test_norm, (
        f"KEYSTONE BROKEN — install code path diverged between prod and test:\n"
        f"PROD: {prod_norm}\n"
        f"TEST: {test_norm}"
    )


def _invoke_install(instance: str, release_tag: str) -> None:
    """Invoke the CLI install command for the given instance + release tag.

    Helper around the runner / click invocation matching the rest of this
    test module's pattern. Replace the body with whatever pattern
    test_daemon_release.py uses for CLI invocation."""
    from typer.testing import CliRunner
    from agent_core.daemon.cli import daemon_app
    runner = CliRunner()
    result = runner.invoke(
        daemon_app, ["install", "--instance", instance, "--release", release_tag]
    )
    assert result.exit_code == 0, f"install --instance {instance} failed: {result.output}"


def _normalize_calls(
    calls: list[tuple[str, tuple, dict]],
    home: Path,
) -> list[tuple[str, tuple, dict]]:
    """Replace home-path components in args/kwargs with a <HOME> placeholder.

    Walks args and kwargs recursively; any value that is a Path or str
    containing the home path is rewritten with <HOME> in place of the
    home. This lets us compare prod's and test's call sequences for
    structural identity ignoring the home difference."""
    placeholder = "<HOME>"
    home_str = str(home)

    def norm(value):
        if isinstance(value, Path):
            s = str(value)
            if home_str in s:
                return s.replace(home_str, placeholder)
            return s
        if isinstance(value, str):
            return value.replace(home_str, placeholder) if home_str in value else value
        if isinstance(value, (list, tuple)):
            return type(value)(norm(v) for v in value)
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items()}
        return value

    return [
        (name, norm(args), norm(kwargs))
        for name, args, kwargs in calls
    ]
```

If `release.py`'s function names differ from the list above (the spec § Components named these 8, but the actual file may have a different decomposition), adjust `function_names` to match what's actually in `release.py` and what the CLI install command invokes.

The `_invoke_install` and `_normalize_calls` helpers are private to this test; they may be inlined or extracted as fixtures depending on the file's existing patterns. Match what's already there.

- [ ] **Step 2: Run test to verify it fails or passes**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_release.py::test_install_code_path_identity_between_prod_and_test -v
```

Expected outcomes:
- If Task 2 (`cli.py` test-instance handling) is correctly routing test through prod's install code, the test should PASS — the call sequences should match by construction.
- If the test FAILS, the failure diagnostic will show which call diverged. Common diagnoses:
  - Test's install path takes a different branch in `cli.py` than prod's (regression in Task 2).
  - The test's install path passes an extra/missing argument (e.g., an instance-specific flag).
  - Some function in `release.py` actually IS instance-aware in a way that breaks the abstraction (would be a bug to fix in `release.py`, NOT a reason to weaken the test).

If the test fails with a real divergence, the fix lands in the code that diverged (cli.py or release.py), not in the test.

- [ ] **Step 3: (only if Step 2 failed) Fix the divergence**

Investigate the diagnostic; fix the source-of-truth that diverged from instance-agnostic. The keystone test should be enforcing reality, not the other way around. If a real bug surfaces here, fix it in `cli.py` or wherever the abstraction broke. Do NOT relax the test's assertion.

- [ ] **Step 4: Re-run to confirm pass**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_release.py -v
```

Expected: all tests PASS, including the keystone enforcer.

- [ ] **Step 5: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add packages/core/tests/test_daemon_release.py
# If you also fixed code in Step 3, stage those files too.
git commit -m "test(daemon): keystone enforcer — install code path identity between prod and test"
```

---

## Phase 5 — Coexistence test + dynamic-versioning rename + docs + spec typo

### Task 5: Coexistence integration test

**Files:**
- Create: `packages/core/tests/test_daemon_three_instance_coexistence.py`

- [ ] **Step 0 (preflight): see how existing integration tests are marked**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
grep -rn "pytest.mark.integration\|@pytest.mark" packages/core/tests/ | head -10
cat .github/workflows/ci.yml | grep -A 5 "integration"
```

The Phase 3 CI gate runs `integration`-marked tests on a windows-only job. Use the same marker.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_daemon_three_instance_coexistence.py`:

```python
"""Phase 3.5 coexistence test: prod / source / test all run simultaneously
without conflict.

INTEGRATION-MARKED. Spinning up three real daemons is integration-shape, not
unit-shape. Runs on the windows-only `integration` job per Phase 3's pattern.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("CI") and os.name != "nt",
    reason="integration tests run on windows in CI",
)
def test_three_instances_run_simultaneously_without_conflict(tmp_path):
    """Spin up prod / source / test daemons against tmp homes + non-default
    ports; verify all three reach ready state; verify PID files are written
    to disjoint homes; verify no port-bind error; tear down cleanly.

    Uses AGENT_CORE_HOME overrides to keep the test self-contained
    (doesn't touch real prod state)."""
    homes = {
        "prod": tmp_path / "prod_home",
        "source": tmp_path / "source_home",
        "test": tmp_path / "test_home",
    }
    ports = {"prod": 18789, "source": 18788, "test": 18787}

    procs: dict[str, subprocess.Popen] = {}

    try:
        for instance, home in homes.items():
            env = os.environ.copy()
            env["AGENT_CORE_HOME"] = str(home)
            # Write a minimal config with the test port
            home.mkdir(parents=True, exist_ok=True)
            (home / "agent_core.yaml").write_text(
                f"http:\n  bind_port: {ports[instance]}\n"
                f"bus:\n  storage_path: {home}/bus.sqlite\n"
                f"endpoints:\n  - type: builtin.stub\n    name: stub\n"
            )
            # Start the daemon
            procs[instance] = subprocess.Popen(
                ["agent-core", "daemon", "start", "--instance", instance,
                 "--foreground"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # Wait up to 10 seconds for all three to write their PID files
        deadline = time.time() + 10
        while time.time() < deadline:
            if all((home / "daemon.pid").exists() for home in homes.values()):
                break
            time.sleep(0.2)

        for instance, home in homes.items():
            pid_file = home / "daemon.pid"
            assert pid_file.exists(), (
                f"daemon --instance {instance} did not write PID at {pid_file}"
            )

        # PIDs are unique
        pids = {
            instance: int((home / "daemon.pid").read_text().strip())
            for instance, home in homes.items()
        }
        assert len(set(pids.values())) == 3, f"PIDs collided: {pids}"

    finally:
        # Tear down: stop each daemon
        for proc in procs.values():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
```

The exact `agent-core` CLI invocation may differ (e.g., the command might be `python -m agent_core.daemon`). After preflight Step 0, adjust the subprocess command to match what works in this repo.

The `--foreground` flag may or may not exist; if the daemon always daemonizes, the test pattern needs adjustment. Use whatever pattern Phase 3's existing daemon-start tests use.

- [ ] **Step 2: Run the test (locally, not in CI)**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_daemon_three_instance_coexistence.py -v -m integration
```

Expected: test PASSES on Windows. If on non-Windows, the skipif marker should skip cleanly.

If the test fails with port-conflict or PID-collision, that's a real bug in Phase 3's isolation model that Phase 3.5 needs to fix first. Investigate before patching the test.

- [ ] **Step 3: (only if Step 2 failed for non-environment reasons) Fix the isolation bug**

If a real coexistence bug surfaces, fix it in `instance.py` / `cli.py` and re-run. Do NOT relax the test.

- [ ] **Step 4: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add packages/core/tests/test_daemon_three_instance_coexistence.py
git commit -m "test(daemon): three-instance coexistence integration test (Phase 3.5)"
```

---

### Task 6: Rename `dev` → `source` in `test_dynamic_versioning.py`

**Files:**
- Modify: `packages/core/tests/test_dynamic_versioning.py`

- [ ] **Step 1: Grep + rename**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
grep -n "dev\|DEV" packages/core/tests/test_dynamic_versioning.py
```

Replace every `dev` / `DEV` reference that refers to the Phase 3 instance name with `source` / `SOURCE`. Leave alone any unrelated `dev` (e.g., a package name like `dev-deps` if such exists; check each hit by hand).

- [ ] **Step 2: Run the tests**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/test_dynamic_versioning.py -v
```

Expected: all tests PASS after rename.

- [ ] **Step 3: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add packages/core/tests/test_dynamic_versioning.py
git commit -m "test(daemon): rename dev to source in dynamic-versioning tests"
```

---

### Task 7: Update `docs/setup/daemon.md` for the three-instance model + fix spec typo

**Files:**
- Modify: `docs/setup/daemon.md`
- Modify: `docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md` (line 213 typo)

- [ ] **Step 1: Update `docs/setup/daemon.md`**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
cat docs/setup/daemon.md | head -80
```

Find the Phase 3 dev/prod section. Rewrite to reflect the three-instance model. Key updates:
- `--instance dev` → `--instance source` everywhere.
- Add `--instance test` documentation (the new instance, what it's for, when to use it).
- Update the home-dir map (`~/.agent-core` / `~/.agent-core-source` / `~/.agent-core-test`).
- Update the port map (8789 / 8788 / 8787).
- Add a brief workflow example for using `test`: `daemon install --instance test --release vX.Y.Z` → `daemon start --instance test` → exercise → `daemon stop --instance test`.

Keep the structure of the existing doc; do minimal rewriting beyond what the rename + addition requires.

- [ ] **Step 2: Fix the spec doc line-213 typo**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
grep -n "dev daemon" docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md
```

The hit should be on the tear-down footnote. Replace "the dev daemon" with "the test daemon" — the footnote describes `rm -rf ~/.agent-core-test/`, so the noun should match.

- [ ] **Step 3: Commit**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git add docs/setup/daemon.md docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md
git commit -m "docs(daemon): update for three-instance model + fix spec typo"
```

---

## Phase 6 — Verification + PR

### Task 8: ruff + mypy + full repo test sweep + open PR + ping Pepper

**Files:** No code changes — verification only.

- [ ] **Step 1: Run the full daemon test suite**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest packages/core/tests/ -v -k daemon 2>&1 | tail -10
```

Expected: all daemon tests PASS, including the keystone enforcer.

- [ ] **Step 2: Run ruff**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run ruff check packages/core/
```

Expected: clean. If errors, run `uv run ruff check --fix packages/core/`; fix any remainder by hand; verify tests still pass; commit as `style(phase35): ruff cleanup`.

- [ ] **Step 3: Run mypy**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run mypy packages/core/
```

Expected: no NEW errors versus main's baseline. Pre-existing errors not introduced by this branch are not in scope. If new errors, fix them; commit as `chore(phase35): mypy cleanup`.

- [ ] **Step 4: Full repo sweep**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
uv run pytest -q 2>&1 | tail -10
```

Expected: all tests PASS. Cross-cutting damage is structurally unlikely (no bus / endpoint / channel changes) but worth a belt-and-suspenders pass.

- [ ] **Step 5: Push the branch**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
git push -u origin feat/phase35-three-instance-test
```

- [ ] **Step 6: Open the PR**

```bash
cd /e/workspaces/ai/agents/agent_core/.worktrees/phase35-three-instance-test
gh pr create \
  --base main \
  --head feat/phase35-three-instance-test \
  --title "feat(daemon): three-instance model — prod / source / test (Phase 3.5)" \
  --body "$(cat <<'EOF'
## Summary

Closes the three-environment gap in the agent_core daemon. Extends Phase 3's two-instance model (`prod`, `dev`) to a three-instance model (`prod`, `source`, `test`). Hard-cutover rename of `dev` to `source` so the name reflects what the instance actually is (the daemon running from unbuilt repo source). Adds `test` as a sandboxed instance that installs from release wheels via the SAME `release.py` code path as `prod` — enabling end-to-end deploy-path validation before prod refreshes.

## Design

See `docs/superpowers/specs/2026-05-23-phase35-three-instance-test-daemon-design.md` (commit `5adf770`).

## What ships

- `Instance` enum extended to `{PROD, SOURCE, TEST}`. `DEV` renamed to `SOURCE`. `TEST` added.
- `home_for(TEST)` → `~/.agent-core-test/`. `default_port(TEST)` → 8787.
- CLI `--instance` choice list expands to `{prod, source, test}`. `--instance dev` parse-errors with a clear message naming the new choices.
- `daemon install --instance test --release vX.Y.Z` routes through prod's install code (same `release.py` functions, just with test home).
- `daemon install --instance source` keeps the deliberate-error semantic (renamed message).
- `daemon init --instance test` scaffolds `~/.agent-core-test/agent_core.yaml` with port 8787 and the minimal `builtin.stub` endpoint default.
- Coexistence: all three instances run simultaneously without conflict (disjoint homes, ports, PID files, SQLite, configs).

## Out of scope

- `--from-local` flag for `daemon install --instance test` (deferred to next-ticket-when-symptom-named).
- Autostart support for `source` or `test` (Phase 4 / PR #110 territory; rejection list expands on rebase).
- A `dev` → `source` deprecation alias (hard cutover instead — per spec rationale).

## Test plan

- [x] `uv run pytest packages/core/tests/ -v -k daemon` — all daemon tests pass.
- [x] **Keystone enforcer** (`test_install_code_path_identity_between_prod_and_test`) passes — proves prod and test invoke the same `release.py` functions with identical structural arguments, modulo the home path.
- [x] **Coexistence integration test** (`test_three_instances_run_simultaneously_without_conflict`) passes on Windows.
- [x] `uv run pytest -q` (full repo) passes.
- [x] `uv run ruff check packages/core/` clean.
- [x] `uv run mypy packages/core/` no new errors.

## Sequencing

Phase 3.5 lands first; PR #110 rebases on top. The rebase touches ~5 lines (the `--instance dev` references in autostart code + tests + adding `test` to the prod-only rejection list).

## Migration

Pepper will update any send-paths or docs that reference the old `--instance dev` after this lands. No external `.mcp.json` files reference dev (all point at prod port 8789).
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 7: Ping Pepper with the PR URL**

The controller (not this subagent) handles the Pepper ping after the PR opens. Subagent's job is done at PR-up.

## Status report

Subagent: report `STATUS: DONE` with the PR URL, final test count, ruff result, mypy result, and any production-code fixes that landed during keystone-enforcement (Task 4 Step 3) or coexistence (Task 5 Step 3) if applicable.
