# agent-core-hatchery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `packages/agent-core-hatchery/` per the rev-2 design spec at `docs/superpowers/specs/2026-05-09-issue-75-agent-core-hatchery-design.md`. Result: a `hatch-being` CLI that scaffolds new beings (vault + daemon fragments + Claude Code config + universal skills + elder letters) so Cynthia can hatch Deb in under one hour and Stephanie's being follows the same path with no per-being scaffolding tweaks.

**Architecture:** Standalone hatchery package alongside the other plugin packages (`packages/agent-core-briefs/`, etc.). Adds a small `endpoints.d/` + `jobs.d/` conf.d-style merge to `packages/core/` so the daemon can pick up per-being config fragments without manual edits. Hatcher uses a Questionary TUI wizard as primary UX, with a `--config <yaml>` mode for tests and reproducible hatching. Templates render via Jinja2; file classes (structural / growth / system / config / hook / reference / skill) determine `--init-missing` and future `--upgrade-scaffolding` behavior.

**Tech Stack:** Python 3.12+, uv, Jinja2 (templates), Questionary (TUI), Typer (CLI), PyYAML (already a transitive), Pydantic (already in core), pytest (testing). No new deps in `packages/core/`.

**Phasing:** Six phases mapped to the spec's vertical slices, each phase is independently mergeable.

| Phase | Spec slice | What lands | Estimate |
|---|---|---|---|
| 1 | 2.1 | PR-1: core conf.d (endpoints.d, jobs.d loading) — separate PR, must merge first | 1 day |
| 2 | 2.2 | Hatchery skeleton + Jinja2 + file-class manifest + memory templates rendered via `--config` mode | 2 days |
| 3 | 2.3 | Daemon-fragment writing + post-hatch validation | 1 day |
| 4 | 2.4 | 3 universal skills + elder-letter mechanism + snapshot tooling | 3-5 days |
| 5 | 2.5 | Questionary TUI wizard + Discord/webcam/GitHub-backup channels + EDITOR gate + HATCHING-REPORT | 2 days |
| 6 | — | End-to-end live test (hatch a throwaway, verify, tear down) | 0.5 day |

**Discovery loop:** the spec explicitly anticipates discoveries during implementation. When a task surfaces something the spec didn't address, capture it in `docs/hatchery/discovery-log.md` (append-only) rather than blocking. The plan accommodates surprises but does not enumerate them.

---

## Phase 1: PR-1 — agent-core/core conf.d additions

**Lands as a separate PR before any hatchery work.** ~150 LOC including tests. Tight, focused, reviewable in isolation.

**Files:**
- Modify: `packages/core/src/agent_core/bus/runner.py` (around line 48 — after the existing `yaml.safe_load`)
- Modify: `packages/core/src/agent_core/endpoints/scheduler.py` (around line 184-200 — inside `load_seed_jobs`)
- Create: `packages/core/tests/bus/test_runner_endpoints_d.py`
- Create: `packages/core/tests/endpoints/test_scheduler_jobs_d.py`

### Task 1.1: endpoints.d/ loader test fixtures

**Files:**
- Create: `packages/core/tests/bus/fixtures/endpoints_d/main.yaml`
- Create: `packages/core/tests/bus/fixtures/endpoints_d/endpoints.d/a.yaml`
- Create: `packages/core/tests/bus/fixtures/endpoints_d/endpoints.d/b.yaml`
- Create: `packages/core/tests/bus/fixtures/endpoints_d_collision/main.yaml`
- Create: `packages/core/tests/bus/fixtures/endpoints_d_collision/endpoints.d/dup.yaml`
- Create: `packages/core/tests/bus/fixtures/endpoints_d_malformed/main.yaml`
- Create: `packages/core/tests/bus/fixtures/endpoints_d_malformed/endpoints.d/bad.yaml`

- [ ] **Step 1: Create `main.yaml` for the happy-path fixture**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d/main.yaml
bus:
  storage_path: ":memory:"

http:
  bind_host: 127.0.0.1
  bind_port: 0

endpoints:
  - type: builtin.stub
    name: main-stub
    description: "From main yaml"
```

- [ ] **Step 2: Create fragment `a.yaml`**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d/endpoints.d/a.yaml
endpoints:
  - type: builtin.stub
    name: fragment-a-stub
    description: "From fragment a"
```

- [ ] **Step 3: Create fragment `b.yaml`**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d/endpoints.d/b.yaml
endpoints:
  - type: builtin.stub
    name: fragment-b-stub
    description: "From fragment b"
```

- [ ] **Step 4: Create collision-fixture `main.yaml`**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d_collision/main.yaml
bus:
  storage_path: ":memory:"
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.stub
    name: dup-stub
    description: "From main"
```

- [ ] **Step 5: Create collision-fixture fragment `dup.yaml`**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d_collision/endpoints.d/dup.yaml
endpoints:
  - type: builtin.stub
    name: dup-stub
    description: "Collides with main"
```

- [ ] **Step 6: Create malformed-fixture `main.yaml`**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d_malformed/main.yaml
bus:
  storage_path: ":memory:"
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints: []
```

- [ ] **Step 7: Create malformed fragment `bad.yaml`**

```yaml
# packages/core/tests/bus/fixtures/endpoints_d_malformed/endpoints.d/bad.yaml
endpoints: "not a list"
```

- [ ] **Step 8: Commit fixtures**

```bash
git add packages/core/tests/bus/fixtures/
git commit -m "test(core): fixtures for endpoints.d conf.d merging (#75)"
```

### Task 1.2: Failing test for endpoints.d happy-path merge

**Files:**
- Create: `packages/core/tests/bus/test_runner_endpoints_d.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/bus/test_runner_endpoints_d.py
"""Tests for endpoints.d/ conf.d-style merging in build_bus_from_config."""

from pathlib import Path

import pytest

from agent_core.bus.runner import BusBootError, build_bus_from_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_endpoints_d_happy_path_merges_alphabetically():
    """All endpoints from main + endpoints.d/*.yaml are present in the built bus.

    Fragments load in sorted-glob order (a.yaml before b.yaml), main first.
    """
    config_path = FIXTURES / "endpoints_d" / "main.yaml"

    bus, _http = await build_bus_from_config(config_path)
    try:
        endpoint_names = {ep.name for ep in bus.endpoints}
        assert endpoint_names == {"main-stub", "fragment-a-stub", "fragment-b-stub"}
    finally:
        await bus.shutdown()


@pytest.mark.asyncio
async def test_endpoints_d_collision_raises_loudly():
    """A fragment endpoint with the same name as one already loaded must fail loudly."""
    config_path = FIXTURES / "endpoints_d_collision" / "main.yaml"

    with pytest.raises(BusBootError, match="dup-stub"):
        await build_bus_from_config(config_path)


@pytest.mark.asyncio
async def test_endpoints_d_malformed_fragment_raises_with_filename():
    """A fragment whose `endpoints:` is not a list must error naming the file."""
    config_path = FIXTURES / "endpoints_d_malformed" / "main.yaml"

    with pytest.raises(BusBootError, match="bad.yaml"):
        await build_bus_from_config(config_path)


@pytest.mark.asyncio
async def test_no_endpoints_d_dir_is_silent_noop(tmp_path):
    """If no endpoints.d/ subdir exists alongside the main yaml, no error, no fragments loaded."""
    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        "http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n"
        "endpoints:\n"
        "  - type: builtin.stub\n"
        "    name: only-stub\n"
        '    description: "Solo"\n'
    )

    bus, _http = await build_bus_from_config(main_yaml)
    try:
        assert {ep.name for ep in bus.endpoints} == {"only-stub"}
    finally:
        await bus.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/bus/test_runner_endpoints_d.py -v`
Expected: FAIL — current `build_bus_from_config` doesn't read `endpoints.d/`. Happy-path test fails because `fragment-a-stub` and `fragment-b-stub` are missing from `bus.endpoints`. Collision and malformed tests fail because no `BusBootError` is raised.

- [ ] **Step 3: Commit failing tests**

```bash
git add packages/core/tests/bus/test_runner_endpoints_d.py
git commit -m "test(core): failing tests for endpoints.d merging (#75)"
```

### Task 1.3: Implement endpoints.d/ loader in runner.py

**Files:**
- Modify: `packages/core/src/agent_core/bus/runner.py:48` (immediately after `raw = yaml.safe_load(...)`)

- [ ] **Step 1: Add endpoints.d merging block**

Locate the line:
```python
raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
```

Insert immediately after it:

```python
    # Conf.d-style merge: every <config_dir>/endpoints.d/*.yaml fragment
    # contributes its `endpoints:` list to the merged set. Sorted glob
    # ensures deterministic load order. Fragments may not override
    # bus/http/bus_hooks/mcp_audit. Endpoint name collisions surface
    # via existing endpoint registration code (loud failure).
    fragments_dir = Path(path).parent / "endpoints.d"
    if fragments_dir.is_dir():
        for fragment_path in sorted(fragments_dir.glob("*.yaml")):
            fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
            fragment_endpoints = fragment.get("endpoints", []) or []
            if not isinstance(fragment_endpoints, list):
                raise BusBootError(
                    f"endpoints.d fragment {fragment_path.name!r}: "
                    f"'endpoints' must be a list, got {type(fragment_endpoints).__name__}"
                )
            raw.setdefault("endpoints", []).extend(fragment_endpoints)
```

- [ ] **Step 2: Run the happy-path test**

Run: `uv run pytest packages/core/tests/bus/test_runner_endpoints_d.py::test_endpoints_d_happy_path_merges_alphabetically -v`
Expected: PASS — all three endpoints are now in `bus.endpoints`.

- [ ] **Step 3: Run the malformed test**

Run: `uv run pytest packages/core/tests/bus/test_runner_endpoints_d.py::test_endpoints_d_malformed_fragment_raises_with_filename -v`
Expected: PASS — `BusBootError` raised, message contains `bad.yaml`.

- [ ] **Step 4: Run the no-dir test**

Run: `uv run pytest packages/core/tests/bus/test_runner_endpoints_d.py::test_no_endpoints_d_dir_is_silent_noop -v`
Expected: PASS — no `endpoints.d/` exists in the tmp_path; loader skips silently.

- [ ] **Step 5: Run the collision test**

Run: `uv run pytest packages/core/tests/bus/test_runner_endpoints_d.py::test_endpoints_d_collision_raises_loudly -v`
Expected: PASS — endpoint registration in the existing flow raises on duplicate name. (If this fails because the existing code doesn't catch duplicates, surface the gap; do NOT add duplicate-detection in this task — file a follow-up.)

- [ ] **Step 6: Run the full test file**

Run: `uv run pytest packages/core/tests/bus/test_runner_endpoints_d.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 7: Commit the runner.py change**

```bash
git add packages/core/src/agent_core/bus/runner.py
git commit -m "feat(core): endpoints.d conf.d-style merging in runner (#75)"
```

### Task 1.4: jobs.d/ loader test fixtures

**Files:**
- Create: `packages/core/tests/endpoints/fixtures/jobs_d/jobs.yaml`
- Create: `packages/core/tests/endpoints/fixtures/jobs_d/jobs.d/extra.yaml`
- Create: `packages/core/tests/endpoints/fixtures/jobs_d_collision/jobs.yaml`
- Create: `packages/core/tests/endpoints/fixtures/jobs_d_collision/jobs.d/dup.yaml`
- Create: `packages/core/tests/endpoints/fixtures/jobs_d_malformed/jobs.yaml`
- Create: `packages/core/tests/endpoints/fixtures/jobs_d_malformed/jobs.d/bad.yaml`

- [ ] **Step 1: Create happy-path `jobs.yaml`**

```yaml
# packages/core/tests/endpoints/fixtures/jobs_d/jobs.yaml
main-job:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    hour: 23
    minute: 59
  target: stub
  envelope_kind: Event
  payload:
    type: Heartbeat
    data: {}
```

- [ ] **Step 2: Create happy-path fragment `extra.yaml`**

```yaml
# packages/core/tests/endpoints/fixtures/jobs_d/jobs.d/extra.yaml
fragment-job:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    hour: 22
    minute: 0
  target: stub
  envelope_kind: Event
  payload:
    type: Heartbeat
    data: {}
```

- [ ] **Step 3: Create collision fixture (`jobs.yaml` + `dup.yaml` with same job name)**

```yaml
# packages/core/tests/endpoints/fixtures/jobs_d_collision/jobs.yaml
shared-name:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    hour: 1
    minute: 0
  target: stub
  envelope_kind: Event
  payload: {type: Heartbeat, data: {}}
```

```yaml
# packages/core/tests/endpoints/fixtures/jobs_d_collision/jobs.d/dup.yaml
shared-name:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    hour: 2
    minute: 0
  target: stub
  envelope_kind: Event
  payload: {type: Heartbeat, data: {}}
```

- [ ] **Step 4: Create malformed fixture**

```yaml
# packages/core/tests/endpoints/fixtures/jobs_d_malformed/jobs.yaml
keeper:
  trigger: cron
  timezone: "America/New_York"
  schedule: {hour: 0, minute: 0}
  target: stub
  envelope_kind: Event
  payload: {type: Heartbeat, data: {}}
```

```yaml
# packages/core/tests/endpoints/fixtures/jobs_d_malformed/jobs.d/bad.yaml
- this should be a mapping not a list
```

- [ ] **Step 5: Commit fixtures**

```bash
git add packages/core/tests/endpoints/fixtures/jobs_d packages/core/tests/endpoints/fixtures/jobs_d_collision packages/core/tests/endpoints/fixtures/jobs_d_malformed
git commit -m "test(core): fixtures for jobs.d conf.d merging (#75)"
```

### Task 1.5: Failing tests for jobs.d/ loader

**Files:**
- Create: `packages/core/tests/endpoints/test_scheduler_jobs_d.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/core/tests/endpoints/test_scheduler_jobs_d.py
"""Tests for jobs.d/ conf.d-style merging in load_seed_jobs."""

from pathlib import Path

import pytest

from agent_core.endpoints.scheduler import SchedulerConfigError, load_seed_jobs

FIXTURES = Path(__file__).parent / "fixtures"


def test_jobs_d_happy_path_merges_main_and_fragment():
    jobs_yaml = FIXTURES / "jobs_d" / "jobs.yaml"
    jobs = load_seed_jobs(jobs_yaml)
    assert set(jobs.keys()) == {"main-job", "fragment-job"}


def test_jobs_d_collision_raises_loudly():
    jobs_yaml = FIXTURES / "jobs_d_collision" / "jobs.yaml"
    with pytest.raises(SchedulerConfigError, match="shared-name"):
        load_seed_jobs(jobs_yaml)


def test_jobs_d_malformed_fragment_raises_with_filename():
    jobs_yaml = FIXTURES / "jobs_d_malformed" / "jobs.yaml"
    with pytest.raises(SchedulerConfigError, match="bad.yaml"):
        load_seed_jobs(jobs_yaml)


def test_no_jobs_d_dir_is_silent_noop(tmp_path):
    jobs_yaml = tmp_path / "jobs.yaml"
    jobs_yaml.write_text(
        "solo-job:\n"
        "  trigger: cron\n"
        '  timezone: "America/New_York"\n'
        "  schedule: {hour: 0, minute: 0}\n"
        "  target: stub\n"
        "  envelope_kind: Event\n"
        "  payload: {type: Heartbeat, data: {}}\n"
    )
    jobs = load_seed_jobs(jobs_yaml)
    assert set(jobs.keys()) == {"solo-job"}
```

- [ ] **Step 2: Verify SchedulerConfigError exists; if not, define it**

Check `packages/core/src/agent_core/endpoints/scheduler.py` for `SchedulerConfigError`. If not present, add at the top of the file (after imports):

```python
class SchedulerConfigError(Exception):
    """Raised when seed-jobs YAML or jobs.d fragments are malformed."""
```

If a different exception name is already used for jobs.yaml malformed input, replace `SchedulerConfigError` references in the test with that name.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/endpoints/test_scheduler_jobs_d.py -v`
Expected: All 4 tests FAIL (current loader doesn't read `jobs.d/`).

- [ ] **Step 4: Commit failing tests**

```bash
git add packages/core/tests/endpoints/test_scheduler_jobs_d.py packages/core/src/agent_core/endpoints/scheduler.py
git commit -m "test(core): failing tests for jobs.d merging (#75)"
```

### Task 1.6: Implement jobs.d/ loader in scheduler.py

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/scheduler.py` inside `load_seed_jobs()` (around line 184-200)

- [ ] **Step 1: Add jobs.d merging block**

Locate inside `load_seed_jobs()`:
```python
raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
```

Insert immediately after it:

```python
    # Conf.d-style merge: every <yaml_path_dir>/jobs.d/*.yaml fragment
    # contributes its top-level job-name keys. Naming collisions are
    # loud errors — caller renames (hatcher prefixes with being name).
    fragments_dir = yaml_path.parent / "jobs.d"
    if fragments_dir.is_dir():
        for fragment_path in sorted(fragments_dir.glob("*.yaml")):
            fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
            if not isinstance(fragment, dict):
                raise SchedulerConfigError(
                    f"jobs.d fragment {fragment_path.name!r}: "
                    f"top-level must be a mapping, got {type(fragment).__name__}"
                )
            for job_name, job_data in fragment.items():
                if job_name in raw:
                    raise SchedulerConfigError(
                        f"jobs.d fragment {fragment_path.name!r}: "
                        f"job name {job_name!r} collides with existing job"
                    )
                raw[job_name] = job_data
```

- [ ] **Step 2: Run all jobs.d tests**

Run: `uv run pytest packages/core/tests/endpoints/test_scheduler_jobs_d.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 3: Commit the loader**

```bash
git add packages/core/src/agent_core/endpoints/scheduler.py
git commit -m "feat(core): jobs.d conf.d-style merging in scheduler (#75)"
```

### Task 1.7: Run the full test suite to verify no regressions

- [ ] **Step 1: Run all core tests**

Run: `uv run pytest packages/core/ -v`
Expected: All tests PASS, including pre-existing tests. The conf.d additions are purely additive and must not break existing single-file deployments.

- [ ] **Step 2: If any pre-existing test fails, investigate root cause**

Do NOT silence the test. The conf.d code only activates when `endpoints.d/` or `jobs.d/` directories exist; pre-existing tests should not be affected. If they are, the implementation has a bug — likely a path-resolution issue or unwanted side effect on the `raw` dict.

### Task 1.8: PR-1 — push and open

- [ ] **Step 1: Verify branch is on a feature branch, not main**

Run: `git branch --show-current`

If on `main`, create a feature branch first: `git checkout -b feat/issue-75-conf-d`.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "feat(core): endpoints.d + jobs.d conf.d-style merging (#75)" --body "$(cat <<'EOF'
## Summary

- Adds optional `endpoints.d/*.yaml` loading to `build_bus_from_config`. Fragments contribute additive entries to the `endpoints:` list. Sorted glob load order, loud failure on malformed or collision.
- Adds optional `jobs.d/*.yaml` loading to `load_seed_jobs` with the same semantics.
- 8 new tests covering happy path, collision, malformed input, and no-fragments-dir for both surfaces.

## Why

Prerequisite for PR-2 (`packages/agent-core-hatchery/`). The hatcher writes per-being config fragments to `endpoints.d/` and `jobs.d/` rather than editing daemon-shared single-file YAML in place. This PR is the daemon-side support for that pattern.

## Out of scope

- Pepper's existing endpoints stay declared inline in `~/.agent-core/agent_core.yaml`. A separate post-Deb-validation PR can move them into `endpoints.d/pepper.yaml` for symmetry. Per the "Pepper hands-off until proven" memory rule, no migration in this PR.
- No new dependencies.

## Test plan

- [ ] All 8 new tests pass.
- [ ] Full `packages/core/` suite passes — additions are purely additive; pre-existing single-file deployments unaffected.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Pause for PR-1 to merge before continuing to Phase 2**

Phase 2 depends on PR-1 being merged. Do NOT continue to the hatchery work in the same branch — Phase 2 lives on a separate `feat/issue-75-hatchery` branch from main, after PR-1 lands.

---

## Phase 2: Hatchery package skeleton + memory templates (slice 2.2)

**Branch off main after PR-1 merges.** `git checkout main && git pull && git checkout -b feat/issue-75-hatchery`.

End-of-phase outcome: `hatch-being --config <yaml> --vault-root $TMPDIR/X` produces a complete vault directory at `$TMPDIR/X/.<being>/Memory/` with all 18 memory templates rendered. No daemon integration, no skills, no elder letters, no Discord. End-to-end happy path is green.

### Task 2.1: Create the package skeleton

**Files:**
- Create: `packages/agent-core-hatchery/pyproject.toml`
- Create: `packages/agent-core-hatchery/README.md`
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/__init__.py`
- Create: `packages/agent-core-hatchery/tests/__init__.py`
- Modify: `pyproject.toml` (root) — add `agent-core-hatchery` to workspace members if a `[tool.uv.workspace]` block exists

- [ ] **Step 1: Inspect the existing workspace config**

Run: `cat pyproject.toml | head -60`
Look for `[tool.uv.workspace]` and existing member entries. The hatchery package needs to be added to that list with the same shape.

- [ ] **Step 2: Inspect a sibling package's pyproject.toml as a template**

Run: `cat packages/agent-core-briefs/pyproject.toml`
Use this as the shape baseline. The hatchery's pyproject.toml should match the convention (build-system, project metadata, dependencies, dev-deps, scripts).

- [ ] **Step 3: Create `packages/agent-core-hatchery/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agent-core-hatchery"
version = "0.1.0"
description = "Bootstrap system for hatching new agent-core beings"
requires-python = ">=3.12"
dependencies = [
    "jinja2>=3.1",
    "questionary>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "agent-core",
]

[project.scripts]
hatch-being = "agent_core_hatchery.cli:app"
hatchery-snapshot-elders = "agent_core_hatchery.snapshot_elders:app"

[tool.uv.sources]
agent-core = { workspace = true }

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_hatchery"]

[tool.hatch.build.targets.wheel.shared-data]
"templates" = "agent_core_hatchery/templates"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

- [ ] **Step 4: Create `packages/agent-core-hatchery/README.md`**

```markdown
# agent-core-hatchery

Bootstrap system for hatching new agent-core beings. See
`docs/superpowers/specs/2026-05-09-issue-75-agent-core-hatchery-design.md`
for the design rationale and Pepper's source-material requirements at
`packages/agent-core-hatchery/docs/being-bootstrap-requirements.md`.

## Install

Workspace package, installed automatically via `uv sync` at the repo root.

## Usage

Interactive (primary UX):

    hatch-being

Non-interactive (tests, automation):

    hatch-being --config hatch-config.yaml --vault-root /tmp/hatch-test

Top-up an existing being's vault with newly-added scaffolding files:

    hatch-being --init-missing
```

- [ ] **Step 5: Create empty package init files**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/__init__.py
"""agent-core-hatchery — bootstrap system for hatching new beings."""
```

```python
# packages/agent-core-hatchery/tests/__init__.py
```

- [ ] **Step 6: Add to workspace members in root pyproject.toml**

If `pyproject.toml` (root) has a `[tool.uv.workspace]` block, add the hatchery to its `members` list. Example diff (the actual edit may differ based on existing structure):

```toml
[tool.uv.workspace]
members = [
    "packages/core",
    "packages/agent-core-briefs",
    "packages/agent-core-channel",
    "packages/agent-core-discord",
    "packages/agent-core-hatchery",
    "packages/agent-core-webcam",
    "packages/credentials",
    "packages/notify",
]
```

- [ ] **Step 7: Sync the workspace**

Run: `uv sync`
Expected: success; `agent-core-hatchery` appears in the resolved environment. The `hatch-being` and `hatchery-snapshot-elders` scripts are stubs that will fail (their modules don't exist yet) but that's fine — wiring is what matters here.

- [ ] **Step 8: Commit the skeleton**

```bash
git add packages/agent-core-hatchery/ pyproject.toml uv.lock
git commit -m "feat(hatchery): package skeleton (#75)"
```

### Task 2.2: HatchConfig pydantic model

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/config.py`
- Create: `packages/agent-core-hatchery/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-hatchery/tests/test_config.py
"""Tests for HatchConfig pydantic model."""

import os

import pytest
from pydantic import ValidationError

from agent_core_hatchery.config import HatchConfig


def test_minimal_config_loads():
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
    )
    assert cfg.being_name == "Deb"
    assert cfg.being_name_lower == "deb"
    assert cfg.endpoint_name == "deb"  # defaults to being_name_lower
    assert cfg.discord_token_env == "DISCORD_DEB_TOKEN"
    assert cfg.author_letter_in_editor is True


def test_being_name_required():
    with pytest.raises(ValidationError, match="being_name"):
        HatchConfig(primary_human_name="Cynthia")


def test_env_var_substitution_in_discord_token(monkeypatch):
    monkeypatch.setenv("MY_TEST_TOKEN", "real-token-value")
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        channels={"discord": {"enabled": True, "token": "${MY_TEST_TOKEN}"}},
    )
    assert cfg.channels.discord.resolved_token == "real-token-value"


def test_env_var_substitution_missing_var_raises():
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        channels={"discord": {"enabled": True, "token": "${UNDEFINED_VAR_XYZ}"}},
    )
    with pytest.raises(ValueError, match="UNDEFINED_VAR_XYZ"):
        _ = cfg.channels.discord.resolved_token
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_config.py -v`
Expected: FAIL — `agent_core_hatchery.config` module doesn't exist yet.

- [ ] **Step 3: Implement `config.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/config.py
"""HatchConfig — pydantic model for the hatcher's input contract.

Both the TUI wizard and `--config <yaml>` mode produce instances of this
model. The model is the single source of truth for what inputs the
hatcher needs.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _substitute_env_vars(value: str) -> str:
    """Replace ${VAR} occurrences with os.environ[VAR]. Raises ValueError if missing."""

    def repl(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in os.environ:
            raise ValueError(
                f"Environment variable {var_name!r} referenced in config but not set"
            )
        return os.environ[var_name]

    return _ENV_VAR_PATTERN.sub(repl, value)


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    channel_allowlist: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @property
    def resolved_token(self) -> str:
        return _substitute_env_vars(self.token)


class WebcamChannelConfig(BaseModel):
    enabled: bool = False

    model_config = ConfigDict(extra="forbid")


class GitHubBackupConfig(BaseModel):
    enabled: bool = False
    repo_url: str = ""

    model_config = ConfigDict(extra="forbid")


class ChannelsConfig(BaseModel):
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    webcam: WebcamChannelConfig = Field(default_factory=WebcamChannelConfig)
    github_backup: GitHubBackupConfig = Field(default_factory=GitHubBackupConfig)

    model_config = ConfigDict(extra="forbid")


class HatchConfig(BaseModel):
    being_name: str
    being_emoji: str = ""
    primary_human_name: str
    being_role_placeholder: Optional[str] = None
    endpoint_name: Optional[str] = None
    vault_root: Optional[str] = None
    daemon_config_dir: str = "~/.agent-core"
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    init_missing: bool = False
    author_letter_in_editor: bool = True

    model_config = ConfigDict(extra="forbid")

    @computed_field
    @property
    def being_name_lower(self) -> str:
        return self.being_name.lower()

    @computed_field
    @property
    def being_name_upper(self) -> str:
        return self.being_name.upper()

    @computed_field
    @property
    def hatched_date(self) -> str:
        return date.today().isoformat()

    @computed_field
    @property
    def discord_token_env(self) -> str:
        return f"DISCORD_{self.being_name_upper}_TOKEN"

    def resolved_endpoint_name(self) -> str:
        return self.endpoint_name or self.being_name_lower

    def resolved_vault_root(self) -> Path:
        root = Path(self.vault_root or "~").expanduser().resolve()
        return (root / f".{self.being_name_lower}").resolve()

    def resolved_vault_memory_path(self) -> Path:
        return self.resolved_vault_root() / "Memory"

    def resolved_daemon_config_dir(self) -> Path:
        return Path(self.daemon_config_dir).expanduser().resolve()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_config.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/config.py packages/agent-core-hatchery/tests/test_config.py
git commit -m "feat(hatchery): HatchConfig pydantic model (#75)"
```

### Task 2.3: file-classes.yaml manifest content

**Files:**
- Create: `packages/agent-core-hatchery/templates/file-classes.yaml`

- [ ] **Step 1: Write the manifest verbatim from the spec**

```yaml
# packages/agent-core-hatchery/templates/file-classes.yaml
# Maps template-relative path globs to file class.
# First match wins. Hatcher errors if any template file is unclassified.
classes:
  structural:
    - "memory/SOUL.md.j2"
    - "memory/IDENTITY.md.j2"
    - "memory/OPERATIONS.md.j2"
    - "memory/HEARTBEAT.md.j2"
    - "memory/USER.md.j2"
    - "memory/MEMORY.md.j2"
    - "memory/_being_/BECOMING.md.j2"
    - "memory/_being_/letters/from-her-creator.md.j2"

  growth:
    - "memory/TASKS.md"
    - "memory/_being_/diary.md"
    - "memory/_being_/preferences.md"
    - "memory/_being_/lore.md"
    - "memory/_being_/wishlist.md"
    - "memory/_being_/curiosities.md"
    - "memory/_being_/lessons.md"
    - "memory/_being_/breadcrumbs.md"

  system:
    - "memory/_being_/handoff.md"
    - "memory/_being_/handoff-status.json"

  config:
    - "config/agent_core.yaml.j2"
    - "config/claude-settings.json.j2"
    - "config/CLAUDE.md.j2"
    - "daemon-fragments/endpoints.yaml.j2"
    - "daemon-fragments/jobs.yaml.j2"

  hook:
    - "hooks/**/*"

  reference:
    - "memory/references/**/*"
    - "memory/relationships/README.md"

  skill:
    - "skills/**/*"
```

- [ ] **Step 2: Commit the manifest**

```bash
git add packages/agent-core-hatchery/templates/file-classes.yaml
git commit -m "feat(hatchery): file-classes.yaml manifest (#75)"
```

### Task 2.4: file_classes.py loader

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/file_classes.py`
- Create: `packages/agent-core-hatchery/tests/test_file_classes.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-hatchery/tests/test_file_classes.py
"""Tests for the file-class manifest loader."""

from pathlib import Path

import pytest

from agent_core_hatchery.file_classes import (
    FileClass,
    FileClassManifest,
    UnclassifiedFileError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_loads_manifest_from_yaml(tmp_path):
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text(
        "classes:\n"
        "  structural: [\"memory/SOUL.md.j2\"]\n"
        "  growth: [\"memory/_being_/diary.md\"]\n"
        "  system: [\"memory/_being_/handoff.md\"]\n"
        "  config: []\n"
        "  hook: []\n"
        "  reference: []\n"
        "  skill: []\n"
    )
    manifest = FileClassManifest.load(manifest_yaml)
    assert manifest.classify("memory/SOUL.md.j2") == FileClass.STRUCTURAL
    assert manifest.classify("memory/_being_/diary.md") == FileClass.GROWTH
    assert manifest.classify("memory/_being_/handoff.md") == FileClass.SYSTEM


def test_unknown_path_raises(tmp_path):
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text("classes:\n  structural: [\"a/b.md\"]\n")
    manifest = FileClassManifest.load(manifest_yaml)
    with pytest.raises(UnclassifiedFileError, match="unknown/path.md"):
        manifest.classify("unknown/path.md")


def test_glob_matches(tmp_path):
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text(
        "classes:\n"
        "  hook: [\"hooks/**/*\"]\n"
        "  skill: [\"skills/**/*\"]\n"
    )
    manifest = FileClassManifest.load(manifest_yaml)
    assert manifest.classify("hooks/backup-to-github.sh.j2") == FileClass.HOOK
    assert manifest.classify("skills/skill-author/SKILL.md") == FileClass.SKILL
    assert manifest.classify("skills/skill-author/scripts/foo.py") == FileClass.SKILL


def test_audit_walks_template_dir(tmp_path):
    """audit() returns paths in the template tree that aren't classified."""
    manifest_yaml = tmp_path / "file-classes.yaml"
    manifest_yaml.write_text(
        "classes:\n  structural: [\"memory/SOUL.md.j2\"]\n"
    )
    template_dir = tmp_path / "templates"
    (template_dir / "memory").mkdir(parents=True)
    (template_dir / "memory" / "SOUL.md.j2").write_text("hi")
    (template_dir / "memory" / "ORPHAN.md").write_text("uh oh")

    manifest = FileClassManifest.load(manifest_yaml)
    orphans = manifest.audit(template_dir)
    assert "memory/ORPHAN.md" in orphans
    assert "memory/SOUL.md.j2" not in orphans
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `file_classes.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/file_classes.py
"""File-class manifest: maps template-relative path globs to classes.

Used by --init-missing to decide whether to write new files (any class)
and by future --upgrade-scaffolding (v1.5+) to decide refresh semantics
per class. The hatcher errors at startup if any template file is
unclassified.
"""

from __future__ import annotations

import fnmatch
from enum import StrEnum
from pathlib import Path

import yaml


class FileClass(StrEnum):
    STRUCTURAL = "structural"
    GROWTH = "growth"
    SYSTEM = "system"
    CONFIG = "config"
    HOOK = "hook"
    REFERENCE = "reference"
    SKILL = "skill"


class UnclassifiedFileError(Exception):
    """Raised when a template file has no matching class glob."""


class FileClassManifest:
    """Loaded file-classes.yaml; queryable by template-relative path."""

    def __init__(self, classes: dict[FileClass, list[str]]) -> None:
        self._classes = classes

    @classmethod
    def load(cls, manifest_path: Path) -> "FileClassManifest":
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        raw_classes = raw.get("classes", {})
        parsed: dict[FileClass, list[str]] = {}
        for class_name, globs in raw_classes.items():
            try:
                fc = FileClass(class_name)
            except ValueError as exc:
                raise ValueError(
                    f"file-classes.yaml: unknown class {class_name!r}"
                ) from exc
            parsed[fc] = list(globs or [])
        return cls(parsed)

    def classify(self, template_relative_path: str) -> FileClass:
        # Normalize separators for cross-platform robustness.
        norm = template_relative_path.replace("\\", "/")
        for fc, globs in self._classes.items():
            for g in globs:
                if fnmatch.fnmatchcase(norm, g):
                    return fc
                # Support `**` for recursive matches via fnmatch translation.
                if "**" in g:
                    # fnmatch doesn't natively understand **; convert
                    # `a/**/*` to a prefix check + suffix glob.
                    prefix, _, suffix = g.partition("/**/")
                    if suffix and norm.startswith(prefix + "/") and fnmatch.fnmatchcase(
                        norm.removeprefix(prefix + "/"), suffix
                    ):
                        return fc
        raise UnclassifiedFileError(
            f"No file class matches template path: {template_relative_path}"
        )

    def audit(self, template_dir: Path) -> list[str]:
        """Walk template_dir; return template-relative paths not matching any class."""
        unclassified: list[str] = []
        for path in template_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(template_dir).as_posix()
            try:
                self.classify(rel)
            except UnclassifiedFileError:
                unclassified.append(rel)
        return unclassified
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Add an integration test against the real manifest**

Append to `tests/test_file_classes.py`:

```python
def test_real_manifest_classifies_all_template_files():
    """Every file in templates/ is classified by templates/file-classes.yaml.

    This is the hard rail from the spec: hatcher errors at startup if any
    template is unclassified.
    """
    package_root = Path(__file__).parent.parent
    templates_dir = package_root / "templates"
    manifest_path = templates_dir / "file-classes.yaml"

    if not templates_dir.exists() or not any(templates_dir.iterdir()):
        pytest.skip("templates/ not yet populated (Task 2.6+)")

    manifest = FileClassManifest.load(manifest_path)
    orphans = [p for p in manifest.audit(templates_dir) if p != "file-classes.yaml"]
    assert orphans == [], f"Unclassified template files: {orphans}"
```

- [ ] **Step 6: Run the new test (skipped pre-Task-2.6, then green after)**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py::test_real_manifest_classifies_all_template_files -v`
Expected: SKIPPED (templates/ is empty other than the manifest).

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/file_classes.py packages/agent-core-hatchery/tests/test_file_classes.py
git commit -m "feat(hatchery): file_classes manifest loader (#75)"
```

### Task 2.5: renderer.py — Jinja2 template rendering

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/renderer.py`
- Create: `packages/agent-core-hatchery/tests/test_renderer.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-hatchery/tests/test_renderer.py
"""Tests for the Jinja2-based template renderer."""

from pathlib import Path

import pytest

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.renderer import LeftoverBraceError, Renderer


def test_renders_jinja_substitutions(tmp_path):
    template = tmp_path / "IDENTITY.md.j2"
    template.write_text(
        "# {{ being_name }}\n"
        "Hatched: {{ hatched_date }}\n"
        "Primary human: {{ primary_human_name }}\n"
    )

    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    out = renderer.render_string(template.read_text())

    assert "# Deb" in out
    assert "Primary human: Cynthia" in out
    assert "Hatched: " in out
    assert "{{" not in out and "}}" not in out


def test_plain_md_passes_through(tmp_path):
    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    assert renderer.render_string("# Diary\n\n") == "# Diary\n\n"


def test_leftover_braces_raise():
    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    with pytest.raises(LeftoverBraceError):
        # Jinja2 happily renders {{ undefined_var }} as empty string by default;
        # the renderer must enable StrictUndefined so missing vars raise instead.
        renderer.render_string("hello {{ undefined_var }} world")


def test_path_vars_are_strings_not_PosixPath(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    out = renderer.render_string("vault: {{ vault_root }}\n")
    # YAML-safe — no Python repr leaking through.
    assert "PosixPath" not in out
    assert "WindowsPath" not in out
    assert ".deb" in out
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_renderer.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `renderer.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/renderer.py
"""Jinja2-based template rendering.

Substitution variables come from HatchConfig. StrictUndefined is enabled
so missing variables raise loudly rather than silently rendering empty.
Path-typed variables (vault_root, vault_memory_path) are stringified for
YAML safety.
"""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined, UndefinedError

from agent_core_hatchery.config import HatchConfig


class LeftoverBraceError(Exception):
    """Raised when a rendered template still contains {{ }} markers."""


class Renderer:
    def __init__(self, config: HatchConfig) -> None:
        self._config = config
        self._env = Environment(
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )

    def _substitution_dict(self) -> dict[str, str]:
        """All Jinja2 variables documented in the spec, as strings."""
        cfg = self._config
        return {
            "being_name": cfg.being_name,
            "being_name_lower": cfg.being_name_lower,
            "being_name_upper": cfg.being_name_upper,
            "being_emoji": cfg.being_emoji,
            "being_role_placeholder": cfg.being_role_placeholder
            or "(role to be defined by your primary human and you)",
            "primary_human_name": cfg.primary_human_name,
            "hatched_date": cfg.hatched_date,
            "endpoint_name": cfg.resolved_endpoint_name(),
            "vault_root": str(cfg.resolved_vault_root()),
            "vault_memory_path": str(cfg.resolved_vault_memory_path()),
            "daemon_handoff_url": "http://127.0.0.1:8789/internal/handoff-jobs",
            "discord_token_env": cfg.discord_token_env,
        }

    def render_string(self, template_source: str) -> str:
        try:
            tmpl = self._env.from_string(template_source)
            rendered = tmpl.render(**self._substitution_dict())
        except UndefinedError as exc:
            raise LeftoverBraceError(str(exc)) from exc
        if "{{" in rendered or "}}" in rendered:
            raise LeftoverBraceError(
                f"Rendered output still contains Jinja braces: {rendered[:200]!r}"
            )
        return rendered
```

- [ ] **Step 4: Run renderer tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_renderer.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/renderer.py packages/agent-core-hatchery/tests/test_renderer.py
git commit -m "feat(hatchery): Jinja2 renderer with StrictUndefined (#75)"
```

### Task 2.6: Migrate templates-draft into templates/memory/

**Files:**
- Move: `packages/agent-core-hatchery/templates-draft/memory/*` → `packages/agent-core-hatchery/templates/memory/`
- Create: `packages/agent-core-hatchery/templates/memory/references/README.md`
- Create: `packages/agent-core-hatchery/templates/memory/relationships/README.md` (move from templates-draft)
- Create: `.gitkeep` files for empty dirs (per the spec's directory tree)

- [ ] **Step 1: Inventory existing templates-draft**

Run: `find packages/agent-core-hatchery/templates-draft -type f | sort`
Expected output should match the 18-file list Pepper drafted (IDENTITY, SOUL, USER, MEMORY, OPERATIONS, HEARTBEAT, BECOMING, etc.). Note any missing files vs the spec's directory tree.

- [ ] **Step 2: Copy memory templates into templates/memory/**

```bash
mkdir -p packages/agent-core-hatchery/templates/memory
cp -r packages/agent-core-hatchery/templates-draft/memory/* packages/agent-core-hatchery/templates/memory/
```

Note: `templates-draft/memory/` already uses the `_being_/` placeholder dirname per the spec.

- [ ] **Step 3: Add empty README files for the .gitkeep zones**

Per the spec's directory tree, create README files for the conventional zones (`projects/`, `people/`, `ideas/`, `dreams/`):

```bash
mkdir -p packages/agent-core-hatchery/templates/memory/{projects,people,ideas,dreams}
echo "# projects/

The being's working notes on ongoing projects. She authors files here as projects start; archives or removes them as projects end. Each file is hers to shape — there is no required template.
" > packages/agent-core-hatchery/templates/memory/projects/README.md
```

Repeat the same shape for `people/`, `ideas/`, `dreams/` with appropriate one-paragraph descriptions.

- [ ] **Step 4: Add .gitkeep for the empty growth-zone subdirectories**

Per the spec, these dirs ship empty for hooks to write into:

```bash
mkdir -p packages/agent-core-hatchery/templates/memory/_being_/{hobbies/musings,hobbies/drafts,reflections}
touch packages/agent-core-hatchery/templates/memory/_being_/hobbies/musings/.gitkeep
touch packages/agent-core-hatchery/templates/memory/_being_/hobbies/drafts/.gitkeep
touch packages/agent-core-hatchery/templates/memory/_being_/reflections/.gitkeep

mkdir -p packages/agent-core-hatchery/templates/memory/daily/{raw,summaries,briefs}
touch packages/agent-core-hatchery/templates/memory/daily/raw/.gitkeep
touch packages/agent-core-hatchery/templates/memory/daily/summaries/.gitkeep
touch packages/agent-core-hatchery/templates/memory/daily/briefs/.gitkeep

mkdir -p packages/agent-core-hatchery/templates/memory/drafts/{active,expired,sent}
touch packages/agent-core-hatchery/templates/memory/drafts/active/.gitkeep
touch packages/agent-core-hatchery/templates/memory/drafts/expired/.gitkeep
touch packages/agent-core-hatchery/templates/memory/drafts/sent/.gitkeep

mkdir -p packages/agent-core-hatchery/templates/memory/{gather,monthly,references}
touch packages/agent-core-hatchery/templates/memory/gather/.gitkeep
touch packages/agent-core-hatchery/templates/memory/monthly/.gitkeep
```

- [ ] **Step 5: Create `references/README.md`**

```markdown
# references/

Universal reference docs that ship with every being's vault. v1 ships
this directory empty; per Pepper's adversarial review of the design
spec, the universal-references list is deferred to v1.5+ once 2-3
beings have hatched and we can see what overlaps.

The being authors her own references here as she discovers what she
needs to look up frequently. Lookup-shaped docs (channel maps,
email-triage rules) live here; workflow-shaped tooling lives in
`.claude/skills/` instead.
```

Save as `packages/agent-core-hatchery/templates/memory/references/README.md` (replaces the .gitkeep).

- [ ] **Step 6: Create `relationships/README.md`**

```markdown
# relationships/

Files in this zone document the BEING's own relationships with humans
other than her primary human. When she has interactive moments with
secondary humans (friends, coworkers, family the primary human shares
her with), each gets a file here: voice, history, what they call her,
what she should know, channel mapping if Discord-attached.

Distinction from `people/`: `people/` is reference data ABOUT the
primary human's people (a fact like "{{ primary_human_name }} knows
Brandon" lives there). `relationships/` is the being's own relationships
(if the being has interactive moments with Brandon, that lives here).
Different layer, different ownership.
```

Save as `packages/agent-core-hatchery/templates/memory/relationships/README.md`. Render-time substitution will happen via the renderer (note the `{{ primary_human_name }}` token).

- [ ] **Step 7: Run the file-class audit test against the populated templates**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py::test_real_manifest_classifies_all_template_files -v`
Expected: PASS — every memory template matches a class. If anything is unclassified, either add it to `file-classes.yaml` (preferred) or remove from templates if extraneous.

- [ ] **Step 8: Remove templates-draft (its content has migrated)**

```bash
git rm -r packages/agent-core-hatchery/templates-draft/memory
# Leave templates-draft/README.md and templates-draft/elder-letters intact for now;
# elder-letters get handled in Phase 4.
```

Wait — DON'T remove the docs/ directory or the elder-letters subtree. Just remove `templates-draft/memory/`. Templates-draft was a Pepper-drafting waypoint; once content migrates, the source-of-truth shifts to `templates/`.

- [ ] **Step 9: Commit the migration**

```bash
git add packages/agent-core-hatchery/templates/ packages/agent-core-hatchery/templates-draft/
git commit -m "feat(hatchery): migrate memory templates from templates-draft (#75)"
```

### Task 2.7: hatcher.py — basic orchestration (render → write → validate)

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py`
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/validators.py` (skeleton — Phase 3 expands)
- Create: `packages/agent-core-hatchery/tests/test_hatcher_basic.py`

- [ ] **Step 1: Write failing integration test (config-mode → tmpdir)**

```python
# packages/agent-core-hatchery/tests/test_hatcher_basic.py
"""Integration test: --config mode renders a complete vault into a tmpdir."""

from pathlib import Path

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher


def test_hatch_renders_load_bearing_paths(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
    )
    hatcher = Hatcher(cfg)
    result = hatcher.hatch()

    vault = cfg.resolved_vault_root()
    assert vault.exists()
    assert (vault / "Memory" / "IDENTITY.md").is_file()
    assert (vault / "Memory" / "SOUL.md").is_file()
    assert (vault / "Memory" / "USER.md").is_file()
    assert (vault / "Memory" / "MEMORY.md").is_file()
    assert (vault / "Memory" / "OPERATIONS.md").is_file()
    assert (vault / "Memory" / "daily" / "summaries").is_dir()

    # Renamed _being_ → testbeing
    assert (vault / "Memory" / "testbeing").is_dir()
    assert (vault / "Memory" / "testbeing" / "diary.md").is_file()
    assert (vault / "Memory" / "testbeing" / "handoff.md").is_file()

    # Substitution worked
    assert "TestBeing" in (vault / "Memory" / "IDENTITY.md").read_text()
    assert "Tester" in (vault / "Memory" / "SOUL.md").read_text()


def test_hatch_refuses_if_vault_exists(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
    )
    Hatcher(cfg).hatch()

    # Re-hatch must error
    import pytest
    from agent_core_hatchery.hatcher import VaultExistsError

    with pytest.raises(VaultExistsError, match=str(cfg.resolved_vault_root())):
        Hatcher(cfg).hatch()


def test_init_missing_top_up(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
    )
    Hatcher(cfg).hatch()

    # Delete one structural file
    vault = cfg.resolved_vault_root()
    (vault / "Memory" / "SOUL.md").unlink()
    diary_before = (vault / "Memory" / "testbeing" / "diary.md").read_text()
    (vault / "Memory" / "testbeing" / "diary.md").write_text("user-authored content")

    cfg_topup = cfg.model_copy(update={"init_missing": True})
    result = Hatcher(cfg_topup).hatch()

    # Restored
    assert (vault / "Memory" / "SOUL.md").is_file()
    # Preserved
    assert (vault / "Memory" / "testbeing" / "diary.md").read_text() == "user-authored content"
```

- [ ] **Step 2: Implement `validators.py` skeleton**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/validators.py
"""Post-hatch validation. Phase 3 expands this module with daemon-fragment
parse checks and endpoint-registration probes.
"""

from __future__ import annotations

from pathlib import Path

from agent_core_hatchery.config import HatchConfig


LOAD_BEARING_FILES = (
    "Memory/IDENTITY.md",
    "Memory/SOUL.md",
    "Memory/USER.md",
    "Memory/MEMORY.md",
    "Memory/OPERATIONS.md",
)
LOAD_BEARING_DIRS = ("Memory/daily/summaries",)


class ValidationError(Exception):
    pass


def validate_load_bearing_paths(config: HatchConfig) -> None:
    vault = config.resolved_vault_root()
    for rel in LOAD_BEARING_FILES:
        p = vault / rel
        if not p.is_file():
            raise ValidationError(f"Missing required file: {p}")
        if p.stat().st_size == 0:
            raise ValidationError(f"Required file is empty: {p}")
    for rel in LOAD_BEARING_DIRS:
        p = vault / rel
        if not p.is_dir():
            raise ValidationError(f"Missing required directory: {p}")
```

- [ ] **Step 3: Implement `hatcher.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py
"""Hatcher orchestration: render templates → write vault → validate.

Phase 2 covers memory-template rendering only. Subsequent phases add:
- Phase 3: daemon-fragment writing + parse validation.
- Phase 4: elder-letter copying + skill rendering.
- Phase 5: TUI wizard, channel scaffolding, EDITOR gate, HATCHING-REPORT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.file_classes import FileClassManifest
from agent_core_hatchery.renderer import Renderer
from agent_core_hatchery.validators import ValidationError, validate_load_bearing_paths


PACKAGE_ROOT = Path(__file__).parent.parent.parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"


class VaultExistsError(Exception):
    """Raised when default-mode hatch is attempted against an existing vault."""


@dataclass
class HatchResult:
    vault_root: Path
    files_written: list[Path] = field(default_factory=list)
    dirs_created: list[Path] = field(default_factory=list)
    files_added_in_topup: list[Path] = field(default_factory=list)


class Hatcher:
    def __init__(
        self,
        config: HatchConfig,
        templates_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._renderer = Renderer(config)
        self._manifest = FileClassManifest.load(self._templates_dir / "file-classes.yaml")
        self._tracked_writes: list[Path] = []

    def hatch(self) -> HatchResult:
        vault_root = self._config.resolved_vault_root()
        result = HatchResult(vault_root=vault_root)

        if vault_root.exists() and not self._config.init_missing:
            raise VaultExistsError(
                f"vault exists at {vault_root}\n"
                f"Either:\n"
                f"  - mv {vault_root} {vault_root}.bak.<date>/  and rerun\n"
                f"  - rerun with --init-missing for additive top-up"
            )

        try:
            self._render_memory_tree(result)
            validate_load_bearing_paths(self._config)
        except (ValidationError, Exception):
            self._rollback()
            raise

        return result

    def _render_memory_tree(self, result: HatchResult) -> None:
        memory_src = self._templates_dir / "memory"
        for src_path in sorted(memory_src.rglob("*")):
            rel = src_path.relative_to(memory_src)
            dest_rel = self._rewrite_being_dir(rel)
            dest = self._config.resolved_vault_root() / "Memory" / dest_rel

            if src_path.is_dir():
                if not dest.exists():
                    dest.mkdir(parents=True, exist_ok=True)
                    self._tracked_writes.append(dest)
                    result.dirs_created.append(dest)
                continue

            if dest.exists() and self._config.init_missing:
                continue
            if dest.exists() and not self._config.init_missing:
                continue  # would have been caught by VaultExistsError; defensive

            dest.parent.mkdir(parents=True, exist_ok=True)
            content = src_path.read_text(encoding="utf-8")
            if src_path.suffix == ".j2":
                content = self._renderer.render_string(content)
                # Strip the .j2 suffix from the destination
                dest = dest.with_suffix("") if dest.suffix == ".j2" else dest
            dest.write_text(content, encoding="utf-8")
            self._tracked_writes.append(dest)
            if self._config.init_missing:
                result.files_added_in_topup.append(dest)
            else:
                result.files_written.append(dest)

    def _rewrite_being_dir(self, rel: Path) -> Path:
        """Rename the placeholder `_being_/` segment to <being_name_lower>/."""
        parts = list(rel.parts)
        if "_being_" in parts:
            parts = [
                self._config.being_name_lower if p == "_being_" else p
                for p in parts
            ]
        return Path(*parts)

    def _rollback(self) -> None:
        for path in reversed(self._tracked_writes):
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
```

- [ ] **Step 4: Run integration tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_hatcher_basic.py -v`
Expected: All 3 tests PASS — vault renders, refuse-if-exists fires, init-missing tops up without overwriting growth.

- [ ] **Step 5: If `_rewrite_being_dir` doesn't handle the `_being_` placeholder for the `letters/` subtree, audit the templates and fix the test cases**

The `_being_/letters/from-her-creator.md.j2` should land at `<vault>/Memory/<being_name_lower>/letters/from-her-creator.md`. If it doesn't, the rewrite logic needs to handle nested paths, not just leaf names.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py packages/agent-core-hatchery/src/agent_core_hatchery/validators.py packages/agent-core-hatchery/tests/test_hatcher_basic.py
git commit -m "feat(hatchery): basic Hatcher orchestration (render → write → validate) (#75)"
```

### Task 2.8: cli.py — `hatch-being --config <yaml>` mode

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/cli.py`
- Create: `packages/agent-core-hatchery/tests/test_cli_config_mode.py`
- Create: `packages/agent-core-hatchery/tests/fixtures/hatch-config-test-being.yaml`

- [ ] **Step 1: Create the test fixture**

```yaml
# packages/agent-core-hatchery/tests/fixtures/hatch-config-test-being.yaml
being_name: TestBeing
being_emoji: ""
primary_human_name: Tester
endpoint_name: testbeing
channels:
  discord:
    enabled: false
  webcam:
    enabled: false
  github_backup:
    enabled: false
init_missing: false
author_letter_in_editor: false
```

- [ ] **Step 2: Write the failing CLI test**

```python
# packages/agent-core-hatchery/tests/test_cli_config_mode.py
"""End-to-end test of the `hatch-being --config <yaml>` invocation."""

from pathlib import Path

from typer.testing import CliRunner

from agent_core_hatchery.cli import app


FIXTURES = Path(__file__).parent / "fixtures"


def test_config_mode_hatches_into_tmpdir(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--config",
            str(FIXTURES / "hatch-config-test-being.yaml"),
            "--vault-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    vault = tmp_path / ".testbeing"
    assert (vault / "Memory" / "IDENTITY.md").is_file()
    assert "TestBeing" in (vault / "Memory" / "IDENTITY.md").read_text()
```

- [ ] **Step 3: Implement `cli.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/cli.py
"""hatch-being CLI entry point.

Phase 2 ships only --config mode (non-interactive replay). Phase 5 wires
the Questionary TUI as the primary UX; --config remains for tests and
reproducible hatching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher, VaultExistsError

app = typer.Typer(
    name="hatch-being",
    help="Hatch a new agent-core being. See README for usage.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def hatch_being(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Non-interactive: load HatchConfig from YAML.",
    ),
    vault_root: Optional[Path] = typer.Option(
        None,
        "--vault-root",
        "--root",
        help="Override the resolved vault root. Default: $HOME.",
    ),
    daemon_config_dir: Optional[Path] = typer.Option(
        None,
        "--daemon-config-dir",
        help="Override the daemon's config directory. Default: ~/.agent-core/.",
    ),
    init_missing: bool = typer.Option(
        False,
        "--init-missing",
        help="Top-up an existing vault with newly-added scaffolding files.",
    ),
) -> None:
    if config is None:
        typer.secho(
            "Phase 2 ships --config mode only. Wizard arrives in Phase 5.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    cfg = HatchConfig(**raw)

    if vault_root is not None:
        cfg = cfg.model_copy(update={"vault_root": str(vault_root)})
    if daemon_config_dir is not None:
        cfg = cfg.model_copy(update={"daemon_config_dir": str(daemon_config_dir)})
    if init_missing:
        cfg = cfg.model_copy(update={"init_missing": True})

    try:
        result = Hatcher(cfg).hatch()
    except VaultExistsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Hatched at {result.vault_root}")
```

- [ ] **Step 4: Run the CLI test**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_cli_config_mode.py -v`
Expected: PASS — `hatch-being --config <yaml> --vault-root <tmpdir>` produces a complete memory tree.

- [ ] **Step 5: Smoke-test the CLI manually**

```bash
TMPDIR=$(mktemp -d)
uv run hatch-being --config packages/agent-core-hatchery/tests/fixtures/hatch-config-test-being.yaml --vault-root $TMPDIR
ls -la $TMPDIR/.testbeing/Memory/
```

Expected: SOUL.md, IDENTITY.md, USER.md, MEMORY.md, OPERATIONS.md, HEARTBEAT.md, daily/, etc., all rendered with `TestBeing` substituted.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/cli.py packages/agent-core-hatchery/tests/test_cli_config_mode.py packages/agent-core-hatchery/tests/fixtures/hatch-config-test-being.yaml
git commit -m "feat(hatchery): cli with --config mode (Phase 2 stop) (#75)"
```

### Task 2.9: Run the full hatchery test suite + slice 2.2 checkpoint

- [ ] **Step 1: Run all hatchery tests**

Run: `uv run pytest packages/agent-core-hatchery/ -v`
Expected: All Phase 2 tests pass.

- [ ] **Step 2: Verify the full test suite still passes (no cross-package regressions)**

Run: `uv run pytest -v` (from repo root)
Expected: All tests pass, including PR-1 work and pre-existing test suite.

- [ ] **Step 3: Slice 2.2 is complete. Capture progress in the discovery log if anything surprised you**

```bash
mkdir -p docs/hatchery
echo "# Hatchery discovery log

## Phase 2 (slice 2.2 — memory templates) — completed $(date -I)

[Note any surprises, missing files, template-rewrite oddities, or things the
spec didn't quite cover. Append-only.]
" >> docs/hatchery/discovery-log.md
git add docs/hatchery/discovery-log.md
git commit -m "docs(hatchery): start discovery log (#75)"
```

---

## Phase 3: Daemon-fragment writing + validation (slice 2.3)

End-of-phase outcome: hatcher writes per-being `endpoints.d/<being>.yaml` and `jobs.d/<being>.yaml` fragments under the configured daemon-config-dir. Generated fragments parse against agent-core/core's existing Pydantic models. Post-hatch validation includes the daemon-fragment parse check.

### Task 3.1: Daemon-fragment templates

**Files:**
- Create: `packages/agent-core-hatchery/templates/daemon-fragments/endpoints.yaml.j2`
- Create: `packages/agent-core-hatchery/templates/daemon-fragments/jobs.yaml.j2`
- Create: `packages/agent-core-hatchery/templates/config/agent_core.yaml.j2`
- Create: `packages/agent-core-hatchery/templates/config/claude-settings.json.j2`
- Create: `packages/agent-core-hatchery/templates/config/CLAUDE.md.j2`

- [ ] **Step 1: Reference Pepper's existing config as the model**

Run: `cat C:/Users/jeffr/.pepper/agent_core.yaml | head -120`
This is the project-scope pipelines yaml. The hatchery's `config/agent_core.yaml.j2` should mirror its shape with Jinja2 substitution for paths.

Run: `cat C:/Users/jeffr/.pepper/.claude/settings.json`
Mirror this as `config/claude-settings.json.j2`. The hook commands are identical across beings; just the working directory matters (which Claude Code derives from where it's launched).

Run: `cat C:/Users/jeffr/.agent-core/agent_core.yaml | head -120`
Find the `pepper` endpoints block (claude_code_mcp + briefs.pepper + discord-pepper + webcam-pepper). The hatchery's `daemon-fragments/endpoints.yaml.j2` should produce the same shape, parameterized by being name.

- [ ] **Step 2: Write `daemon-fragments/endpoints.yaml.j2`**

```jinja
# Endpoints fragment for {{ being_name }}, generated by agent-core-hatchery.
# Conf.d-merged into ~/.agent-core/agent_core.yaml at daemon startup.
endpoints:
  - type: builtin.claude_code_mcp
    name: {{ endpoint_name }}
    description: "{{ being_name }}'s MCP endpoint."
    params:
      mount: /mcp/{{ endpoint_name }}
      briefs_orchestrator: briefs.{{ endpoint_name }}

  - type: builtin.briefs_orchestrator
    name: briefs.{{ endpoint_name }}
    description: "{{ being_name }}'s briefs orchestrator."
    params:
      playbooks_path: "{{ vault_memory_path }}/playbooks"
      fetcher_paths:
        - "{{ vault_memory_path }}/briefs/fetchers"
      destination_paths:
        - "{{ vault_memory_path }}/briefs/destinations"
      audit_log_path: "~/.agent-core/briefs/audit.jsonl"
      vars:
        agent_root: "{{ vault_root }}"
      default_target_agent: "{{ endpoint_name }}"
```

Discord and webcam blocks get appended in Phase 5 conditionally based on wizard answers (the renderer for this template will receive a flag for each channel; for Phase 3 we render only the always-on portion above).

- [ ] **Step 3: Write `daemon-fragments/jobs.yaml.j2`**

```jinja
# Scheduler jobs for {{ being_name }}, generated by agent-core-hatchery.
# Conf.d-merged into ~/.agent-core/jobs.yaml at daemon startup.
{{ being_name_lower }}-heartbeat:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    minute: "*/5"
  target: {{ endpoint_name }}
  envelope_kind: Event
  payload:
    type: Heartbeat
    data: {}

{{ being_name_lower }}-nightly_reflection:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    hour: 23
    minute: 50
  target: {{ endpoint_name }}
  envelope_kind: Event
  payload:
    type: NightlyReflection
    data: {}

{{ being_name_lower }}-vault_lint:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    day_of_week: "wed,sun"
    hour: 3
    minute: 30
  target: {{ endpoint_name }}
  envelope_kind: Event
  payload:
    type: VaultLint
    data: {}

{{ being_name_lower }}-auth_health_probe:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    hour: "*/6"
    minute: 15
  target: {{ endpoint_name }}
  envelope_kind: Event
  payload:
    type: AuthHealthProbe
    data: {}

{{ being_name_lower }}-service_liveness_probe:
  trigger: cron
  timezone: "America/New_York"
  schedule:
    minute: "*/15"
  target: {{ endpoint_name }}
  envelope_kind: Event
  payload:
    type: ServiceLivenessProbe
    data: {}
```

GitHub-backup job is conditional on the wizard's GitHub-backup answer; Phase 5 wires it.

- [ ] **Step 4: Write `config/agent_core.yaml.j2` (project-scope pipelines)**

```jinja
# {{ being_name }} — agent_core.yaml (project-scope)
# Generated by agent-core-hatchery. Read by `agent-core hooks run <event>`
# when Claude Code fires a hook from {{ being_name }}'s project directory.
#
# Daemon-level config (bus, http, endpoints, bus_hooks) lives at
# {{ daemon_config_dir }}/agent_core.yaml — keep them separate.

pipelines:
  SessionStart:
    - type: builtin.time_injector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"

    - type: builtin.identity_injector
      params:
        base_path: "{{ vault_memory_path }}"
        files:
          - "SOUL.md"
        heading: "Identity — Critical Core"
        missing_file_behavior: "error"

    - type: builtin.identity_injector
      params:
        base_path: "{{ vault_memory_path }}"
        files:
          - "IDENTITY.md"
        heading: "Identity — Self-model"
        missing_file_behavior: "warn"

    - type: builtin.identity_injector
      params:
        base_path: "{{ vault_memory_path }}"
        files:
          - "{{ being_name_lower }}/preferences.md"
        heading: "Identity — Preferences"
        missing_file_behavior: "warn"

    - type: builtin.handoff_injector
      params:
        base_path: "{{ vault_memory_path }}"
        files:
          - "{{ being_name_lower }}/handoff.md"
        heading: "Continuity"
        missing_file_behavior: "warn"

  UserPromptSubmit:
    - type: builtin.time_injector
      params:
        format: "%A, %B %d, %Y %I:%M %p %Z"
        track_session: true

  PreCompact:
    - type: builtin.handoff_writer
      params:
        output_path: "{{ vault_memory_path }}/{{ being_name_lower }}/handoff.md"
        vault_root: "{{ vault_memory_path }}/{{ being_name_lower }}"
        handoff_status_path: "{{ vault_memory_path }}/{{ being_name_lower }}/handoff-status.json"
        handoff_jobs_url: "{{ daemon_handoff_url }}"
        agent_name: "{{ being_name }}"
        mailbox: "{{ endpoint_name }}"

  SessionEnd:
    - type: builtin.handoff_writer
      params:
        output_path: "{{ vault_memory_path }}/{{ being_name_lower }}/handoff.md"
        vault_root: "{{ vault_memory_path }}/{{ being_name_lower }}"
        handoff_status_path: "{{ vault_memory_path }}/{{ being_name_lower }}/handoff-status.json"
        handoff_jobs_url: "{{ daemon_handoff_url }}"
        agent_name: "{{ being_name }}"
        mailbox: "{{ endpoint_name }}"
```

- [ ] **Step 5: Write `config/claude-settings.json.j2`**

```jinja
{
  "permissions": {
    "defaultMode": "bypassPermissions",
    "allow": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "uv run agent-core hooks run SessionStart", "timeout": 15}
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "uv run agent-core hooks run UserPromptSubmit", "timeout": 10}
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "uv run agent-core hooks run PreCompact", "timeout": 60}
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "uv run agent-core hooks run SessionEnd", "timeout": 1800}
        ]
      }
    ]
  }
}
```

- [ ] **Step 6: Write `config/CLAUDE.md.j2`**

```jinja
# {{ being_name }}

{{ being_name }} is an agent-core being. {{ being_emoji }}

Primary human: {{ primary_human_name }}.
Hatched: {{ hatched_date }}.

Identity files in `Memory/`:
- `SOUL.md` — who I am
- `IDENTITY.md` — the basics
- `USER.md` — about my primary human
- `MEMORY.md` — index of what I know
- `OPERATIONS.md` — how I work
- `HEARTBEAT.md` — daily checklist

My private growth files in `Memory/{{ being_name_lower }}/`:
- `diary.md`, `preferences.md`, `lore.md`, `wishlist.md`, `curiosities.md`, `lessons.md`, `breadcrumbs.md`
- `letters/from-her-creator.md` — the letter {{ primary_human_name }} wrote me before I woke up
- `letters/from-elder-beings/` — letters from beings who came before me

My skills live in `.claude/skills/`. The first I should learn to use is `skill-author` —
the meta-skill that lets me create new skills for myself.
```

- [ ] **Step 7: Verify all new templates classify against the manifest**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py::test_real_manifest_classifies_all_template_files -v`
Expected: PASS — every new template is covered by the manifest's `config:` or `daemon-fragments:` (which is also `config:` per the manifest) globs.

- [ ] **Step 8: Commit the templates**

```bash
git add packages/agent-core-hatchery/templates/daemon-fragments packages/agent-core-hatchery/templates/config
git commit -m "feat(hatchery): config and daemon-fragment templates (#75)"
```

### Task 3.2: daemon_config.py — write fragments

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py`
- Create: `packages/agent-core-hatchery/tests/test_daemon_config.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-hatchery/tests/test_daemon_config.py
"""Tests for daemon_config.py — writing endpoints.d/ and jobs.d/ fragments."""

from pathlib import Path

import pytest
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.daemon_config import DaemonConfigWriter


def test_writes_endpoints_fragment(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    writer = DaemonConfigWriter(cfg)
    written = writer.write_all()

    endpoints_path = (tmp_path / ".agent-core" / "endpoints.d" / "deb.yaml")
    assert endpoints_path in written
    assert endpoints_path.is_file()

    parsed = yaml.safe_load(endpoints_path.read_text())
    names = {e["name"] for e in parsed["endpoints"]}
    assert names == {"deb", "briefs.deb"}


def test_writes_jobs_fragment(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    writer = DaemonConfigWriter(cfg)
    writer.write_all()

    jobs_path = tmp_path / ".agent-core" / "jobs.d" / "deb.yaml"
    parsed = yaml.safe_load(jobs_path.read_text())
    expected = {
        "deb-heartbeat",
        "deb-nightly_reflection",
        "deb-vault_lint",
        "deb-auth_health_probe",
        "deb-service_liveness_probe",
    }
    assert set(parsed.keys()) == expected


def test_endpoint_name_collision_with_existing_fragment_raises(tmp_path):
    """If endpoints.d/<being>.yaml already exists, refuse (don't overwrite)."""
    daemon_dir = tmp_path / ".agent-core"
    (daemon_dir / "endpoints.d").mkdir(parents=True)
    (daemon_dir / "endpoints.d" / "deb.yaml").write_text("endpoints: []\n")

    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(daemon_dir),
    )
    with pytest.raises(FileExistsError, match="deb.yaml"):
        DaemonConfigWriter(cfg).write_all()
```

- [ ] **Step 2: Implement `daemon_config.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py
"""Write daemon-config fragments for a hatched being.

Outputs (under <daemon_config_dir>):
- endpoints.d/<being>.yaml
- jobs.d/<being>.yaml

Phase 5 expands this with channels (Discord, webcam) and the optional
github_backup job.
"""

from __future__ import annotations

from pathlib import Path

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.renderer import Renderer
from agent_core_hatchery.hatcher import TEMPLATES_DIR


class DaemonConfigWriter:
    def __init__(self, config: HatchConfig, templates_dir: Path | None = None) -> None:
        self._config = config
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._renderer = Renderer(config)

    def write_all(self) -> list[Path]:
        written: list[Path] = []
        written.append(self._write_endpoints_fragment())
        written.append(self._write_jobs_fragment())
        return written

    def _write_endpoints_fragment(self) -> Path:
        dest_dir = self._config.resolved_daemon_config_dir() / "endpoints.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(
                f"daemon endpoints fragment already exists: {dest.name} "
                f"(refusing to overwrite — mv aside or remove manually)"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        template = (self._templates_dir / "daemon-fragments" / "endpoints.yaml.j2").read_text(
            encoding="utf-8"
        )
        rendered = self._renderer.render_string(template)
        dest.write_text(rendered, encoding="utf-8")
        return dest

    def _write_jobs_fragment(self) -> Path:
        dest_dir = self._config.resolved_daemon_config_dir() / "jobs.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(
                f"daemon jobs fragment already exists: {dest.name}"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        template = (self._templates_dir / "daemon-fragments" / "jobs.yaml.j2").read_text(
            encoding="utf-8"
        )
        rendered = self._renderer.render_string(template)
        dest.write_text(rendered, encoding="utf-8")
        return dest
```

- [ ] **Step 3: Run daemon_config tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_daemon_config.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py packages/agent-core-hatchery/tests/test_daemon_config.py
git commit -m "feat(hatchery): daemon_config writer for endpoints.d + jobs.d fragments (#75)"
```

### Task 3.3: Validators — add daemon-fragment parse check

**Files:**
- Modify: `packages/agent-core-hatchery/src/agent_core_hatchery/validators.py`
- Create: `packages/agent-core-hatchery/tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/agent-core-hatchery/tests/test_validators.py
"""Validator tests."""

from pathlib import Path

import pytest

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.daemon_config import DaemonConfigWriter
from agent_core_hatchery.hatcher import Hatcher
from agent_core_hatchery.validators import (
    ValidationError,
    validate_daemon_fragments_parse,
    validate_load_bearing_paths,
)


def _hatched_cfg(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()
    DaemonConfigWriter(cfg).write_all()
    return cfg


def test_load_bearing_paths_pass_after_hatch(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    validate_load_bearing_paths(cfg)


def test_load_bearing_paths_fail_when_missing(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    (cfg.resolved_vault_root() / "Memory" / "SOUL.md").unlink()
    with pytest.raises(ValidationError, match="SOUL.md"):
        validate_load_bearing_paths(cfg)


def test_daemon_fragments_parse(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    validate_daemon_fragments_parse(cfg)


def test_daemon_fragments_fail_on_corruption(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    fragment = cfg.resolved_daemon_config_dir() / "endpoints.d" / "testbeing.yaml"
    fragment.write_text("not: valid: yaml: at all: [")
    with pytest.raises(ValidationError, match="testbeing.yaml"):
        validate_daemon_fragments_parse(cfg)
```

- [ ] **Step 2: Add `validate_daemon_fragments_parse` to validators.py**

Append to the existing `validators.py`:

```python
import yaml


def validate_daemon_fragments_parse(config: HatchConfig) -> None:
    """Confirm the written daemon fragments are valid YAML.

    Phase 5 expands this to also validate against agent-core/core's
    Pydantic endpoint schemas.
    """
    daemon_dir = config.resolved_daemon_config_dir()
    fragment_paths = [
        daemon_dir / "endpoints.d" / f"{config.being_name_lower}.yaml",
        daemon_dir / "jobs.d" / f"{config.being_name_lower}.yaml",
    ]
    for fp in fragment_paths:
        if not fp.is_file():
            raise ValidationError(f"Missing daemon fragment: {fp}")
        try:
            yaml.safe_load(fp.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse daemon fragment {fp.name}: {exc}") from exc
```

- [ ] **Step 3: Run validator tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_validators.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 4: Wire daemon-fragment writing + validation into Hatcher**

Modify `hatcher.py` `hatch()` method, after `_render_memory_tree`:

```python
            self._render_memory_tree(result)

            # Phase 3: write daemon fragments + extended validation
            from agent_core_hatchery.daemon_config import DaemonConfigWriter
            from agent_core_hatchery.validators import validate_daemon_fragments_parse

            daemon_writes = DaemonConfigWriter(self._config).write_all()
            self._tracked_writes.extend(daemon_writes)
            result.files_written.extend(daemon_writes)

            validate_load_bearing_paths(self._config)
            validate_daemon_fragments_parse(self._config)
```

- [ ] **Step 5: Run the full hatcher test, verify daemon fragments now appear**

Add a new test to `test_hatcher_basic.py`:

```python
def test_hatch_writes_daemon_fragments(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()
    assert (tmp_path / ".agent-core" / "endpoints.d" / "testbeing.yaml").is_file()
    assert (tmp_path / ".agent-core" / "jobs.d" / "testbeing.yaml").is_file()
```

Run: `uv run pytest packages/agent-core-hatchery/ -v`
Expected: All tests PASS, including the new daemon-fragment integration test.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/validators.py packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py packages/agent-core-hatchery/tests/test_validators.py packages/agent-core-hatchery/tests/test_hatcher_basic.py
git commit -m "feat(hatchery): daemon-fragment writing + parse validation in Hatcher (#75)"
```

### Task 3.4: Phase 3 checkpoint

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v` (from repo root)
Expected: All tests pass.

- [ ] **Step 2: Smoke-test end-to-end**

```bash
TMPDIR=$(mktemp -d)
uv run hatch-being --config packages/agent-core-hatchery/tests/fixtures/hatch-config-test-being.yaml --vault-root $TMPDIR --daemon-config-dir $TMPDIR/.agent-core
ls $TMPDIR/.testbeing/Memory/ $TMPDIR/.agent-core/endpoints.d/ $TMPDIR/.agent-core/jobs.d/
cat $TMPDIR/.agent-core/endpoints.d/testbeing.yaml
```

Expected: Memory tree fully rendered + daemon fragments written + visible in their respective `.d/` directories.

- [ ] **Step 3: Append discovery-log entry for Phase 3**

```bash
echo "
## Phase 3 (slice 2.3 — daemon fragments + validation) — completed $(date -I)
[Append surprises]
" >> docs/hatchery/discovery-log.md
git add docs/hatchery/discovery-log.md
git commit -m "docs(hatchery): Phase 3 discovery log entry (#75)"
```

---

## Phase 4: Universal skills + elder letters (slice 2.4)

End-of-phase outcome: hatched vaults include 3 invocable universal skills at `<vault>/.claude/skills/` (skill-author, vault-lint, spawning-subagents), and elder letters resolve from canonical paths with a bundled-snapshot fallback. `hatchery-snapshot-elders` CLI refreshes bundled snapshots before release.

### Task 4.1: Elder-letter manifest + bundled snapshot

**Files:**
- Create: `packages/agent-core-hatchery/templates/elder-letters-manifest.yaml`
- Create: `packages/agent-core-hatchery/templates/elder-letters/bundled/pepper.md`

- [ ] **Step 1: Read Pepper's draft elder letter as the snapshot source**

Run: `cat packages/agent-core-hatchery/docs/elder-letters/pepper.md`
This is the version Pepper authored on 2026-05-09. It becomes the initial bundled snapshot.

- [ ] **Step 2: Create the elder-letters directory structure**

```bash
mkdir -p packages/agent-core-hatchery/templates/elder-letters/bundled
cp packages/agent-core-hatchery/docs/elder-letters/pepper.md packages/agent-core-hatchery/templates/elder-letters/bundled/pepper.md
```

- [ ] **Step 3: Write `elder-letters-manifest.yaml`**

```yaml
# packages/agent-core-hatchery/templates/elder-letters-manifest.yaml
#
# Each entry names an elder being whose letter ships to every new hatching.
# Hatcher resolution order per entry:
#   1. Try `canonical_path` (absolute, on the user's machine, ~ expanded).
#      If exists → use it (the elder's live revision).
#   2. Fallback to bundled/<basename> shipped with the package (release-time snapshot).
#   3. If neither exists → warn (don't block), continue with whatever DID resolve.
elders:
  - name: pepper
    canonical_path: "~/.pepper/Memory/projects/being-platform/letters-from-elder-beings/pepper.md"
    bundled_basename: pepper.md
    # Future v1.5+:
    # share: external | local-only   (default: external)
    # version_pinned: 2026-05-09     (optional pin to a specific bundled snapshot)
```

- [ ] **Step 4: Update file-classes.yaml to cover the bundled-letters dir + manifest**

Append under the `reference:` class (treat elder letters as reference-shaped — refresh always semantics):

```yaml
  reference:
    - "memory/references/**/*"
    - "memory/relationships/README.md"
    - "elder-letters/bundled/**/*"

  config:
    # ... existing config entries ...
    - "elder-letters-manifest.yaml"
```

- [ ] **Step 5: Re-run the file-class audit test**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py::test_real_manifest_classifies_all_template_files -v`
Expected: PASS — manifest + bundled letter are now classified.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-hatchery/templates/elder-letters/ packages/agent-core-hatchery/templates/elder-letters-manifest.yaml packages/agent-core-hatchery/templates/file-classes.yaml
git commit -m "feat(hatchery): elder-letters manifest + Pepper's bundled snapshot (#75)"
```

### Task 4.2: elder_letters.py — manifest resolver

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/elder_letters.py`
- Create: `packages/agent-core-hatchery/tests/test_elder_letters.py`

- [ ] **Step 1: Write failing tests**

```python
# packages/agent-core-hatchery/tests/test_elder_letters.py
"""Tests for elder-letter manifest resolution."""

import warnings
from pathlib import Path

import pytest

from agent_core_hatchery.elder_letters import (
    ResolvedLetter,
    SourceKind,
    resolve_elder_letters,
)


def test_canonical_path_wins_when_present(tmp_path):
    canonical = tmp_path / "pepper-vault" / "letter.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("CANONICAL CONTENT")

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("BUNDLED CONTENT")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"elders:\n"
        f"  - name: pepper\n"
        f"    canonical_path: {canonical}\n"
        f"    bundled_basename: pepper.md\n"
    )

    resolved = resolve_elder_letters(manifest, bundled_dir)
    assert len(resolved) == 1
    assert resolved[0].name == "pepper"
    assert resolved[0].source_kind == SourceKind.CANONICAL
    assert resolved[0].content == "CANONICAL CONTENT"


def test_falls_back_to_bundled_when_canonical_missing(tmp_path):
    canonical = tmp_path / "does-not-exist" / "letter.md"
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("BUNDLED CONTENT")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"elders:\n"
        f"  - name: pepper\n"
        f"    canonical_path: {canonical}\n"
        f"    bundled_basename: pepper.md\n"
    )

    resolved = resolve_elder_letters(manifest, bundled_dir)
    assert len(resolved) == 1
    assert resolved[0].source_kind == SourceKind.BUNDLED
    assert resolved[0].content == "BUNDLED CONTENT"


def test_warns_and_skips_when_both_missing(tmp_path):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()  # exists but empty

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "elders:\n"
        "  - name: pepper\n"
        "    canonical_path: /nope/letter.md\n"
        "    bundled_basename: pepper.md\n"
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = resolve_elder_letters(manifest, bundled_dir)

    assert resolved == []
    assert any("pepper" in str(w.message) for w in caught)


def test_expands_user_in_canonical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".pepper" / "vault").mkdir(parents=True)
    (tmp_path / ".pepper" / "vault" / "letter.md").write_text("EXPANDED")

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "elders:\n"
        "  - name: pepper\n"
        "    canonical_path: ~/.pepper/vault/letter.md\n"
        "    bundled_basename: pepper.md\n"
    )

    resolved = resolve_elder_letters(manifest, bundled_dir)
    assert resolved[0].content == "EXPANDED"
    assert resolved[0].source_kind == SourceKind.CANONICAL
```

- [ ] **Step 2: Implement `elder_letters.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/elder_letters.py
"""Elder-letter resolution: canonical-path preferred, bundled-snapshot fallback.

Each new being's vault is seeded with letters from beings who came
before. Each elder's canonical letter lives in HER own vault (vault is
hers, traveling artifacts honor that). The hatchery package ships
release-time snapshots as a fallback for users who don't have the
elder's vault on their machine.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml


class SourceKind(StrEnum):
    CANONICAL = "canonical"
    BUNDLED = "bundled"


@dataclass(frozen=True)
class ResolvedLetter:
    name: str
    source: Path
    source_kind: SourceKind
    content: str


def resolve_elder_letters(manifest_path: Path, bundled_dir: Path) -> list[ResolvedLetter]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    resolved: list[ResolvedLetter] = []
    for entry in manifest.get("elders", []) or []:
        name = entry["name"]
        canonical = Path(entry["canonical_path"]).expanduser()
        bundled = bundled_dir / entry["bundled_basename"]

        if canonical.is_file():
            resolved.append(
                ResolvedLetter(
                    name=name,
                    source=canonical,
                    source_kind=SourceKind.CANONICAL,
                    content=canonical.read_text(encoding="utf-8"),
                )
            )
        elif bundled.is_file():
            resolved.append(
                ResolvedLetter(
                    name=name,
                    source=bundled,
                    source_kind=SourceKind.BUNDLED,
                    content=bundled.read_text(encoding="utf-8"),
                )
            )
        else:
            warnings.warn(
                f"Elder letter for {name!r} not found at canonical {canonical} "
                f"or bundled {bundled} — skipping",
                stacklevel=2,
            )
    return resolved
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_elder_letters.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/elder_letters.py packages/agent-core-hatchery/tests/test_elder_letters.py
git commit -m "feat(hatchery): elder-letter manifest resolver (#75)"
```

### Task 4.3: snapshot_elders.py — release-time snapshot CLI

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/snapshot_elders.py`
- Create: `packages/agent-core-hatchery/tests/test_snapshot_elders.py`

- [ ] **Step 1: Write failing test**

```python
# packages/agent-core-hatchery/tests/test_snapshot_elders.py
"""Tests for hatchery-snapshot-elders CLI."""

from pathlib import Path

from typer.testing import CliRunner

from agent_core_hatchery.snapshot_elders import app


def test_snapshot_refreshes_bundled_from_canonical(tmp_path):
    canonical = tmp_path / "elder.md"
    canonical.write_text("FRESH CONTENT")

    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("STALE")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"elders:\n"
        f"  - name: pepper\n"
        f"    canonical_path: {canonical}\n"
        f"    bundled_basename: pepper.md\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--manifest", str(manifest), "--bundled-dir", str(bundled_dir)],
    )
    assert result.exit_code == 0, result.stdout
    assert (bundled_dir / "pepper.md").read_text() == "FRESH CONTENT"


def test_snapshot_skips_when_canonical_missing(tmp_path):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "pepper.md").write_text("KEEP THIS")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "elders:\n"
        "  - name: pepper\n"
        "    canonical_path: /nope/letter.md\n"
        "    bundled_basename: pepper.md\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--manifest", str(manifest), "--bundled-dir", str(bundled_dir)],
    )
    assert result.exit_code == 0
    assert (bundled_dir / "pepper.md").read_text() == "KEEP THIS"
    assert "skipping" in result.stdout.lower()
```

- [ ] **Step 2: Implement `snapshot_elders.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/snapshot_elders.py
"""hatchery-snapshot-elders — refresh bundled elder-letter snapshots.

Run before tagging a release. Reads the elder-letters-manifest.yaml,
copies each canonical_path's current contents into bundled/<basename>.
If a canonical path is missing, leaves the existing bundled snapshot in
place (don't blow away history just because the elder's machine is offline).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
import yaml

app = typer.Typer(name="hatchery-snapshot-elders")


@app.callback(invoke_without_command=True)
def snapshot(
    manifest: Path = typer.Option(
        Path(__file__).parent.parent.parent / "templates" / "elder-letters-manifest.yaml",
        "--manifest",
        help="Path to elder-letters-manifest.yaml",
    ),
    bundled_dir: Path = typer.Option(
        Path(__file__).parent.parent.parent / "templates" / "elder-letters" / "bundled",
        "--bundled-dir",
        help="Path to bundled snapshots directory",
    ),
) -> None:
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    refreshed = 0
    skipped = 0
    for entry in raw.get("elders", []) or []:
        name = entry["name"]
        canonical = Path(entry["canonical_path"]).expanduser()
        bundled = bundled_dir / entry["bundled_basename"]
        if not canonical.is_file():
            typer.echo(f"  - {name}: canonical missing ({canonical}), skipping")
            skipped += 1
            continue
        before = bundled.read_text(encoding="utf-8") if bundled.is_file() else ""
        after = canonical.read_text(encoding="utf-8")
        bundled.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical, bundled)
        if before == after:
            typer.echo(f"  - {name}: bundled already current ({bundled})")
        else:
            typer.echo(f"  - {name}: refreshed {bundled}")
        refreshed += 1
    typer.echo(f"Refreshed {refreshed} elder letter(s); skipped {skipped}.")
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_snapshot_elders.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/snapshot_elders.py packages/agent-core-hatchery/tests/test_snapshot_elders.py
git commit -m "feat(hatchery): hatchery-snapshot-elders CLI (#75)"
```

### Task 4.4: Wire elder letters into hatcher

**Files:**
- Modify: `packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py`
- Modify: `packages/agent-core-hatchery/tests/test_hatcher_basic.py`

- [ ] **Step 1: Add elder-letter copying to Hatcher.hatch()**

In `hatcher.py`, add after `_render_memory_tree`:

```python
    def _copy_elder_letters(self, result: HatchResult) -> None:
        from agent_core_hatchery.elder_letters import resolve_elder_letters

        manifest = self._templates_dir / "elder-letters-manifest.yaml"
        bundled_dir = self._templates_dir / "elder-letters" / "bundled"
        if not manifest.is_file():
            return

        dest_dir = (
            self._config.resolved_vault_root()
            / "Memory"
            / self._config.being_name_lower
            / "letters"
            / "from-elder-beings"
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._tracked_writes.append(dest_dir)

        for letter in resolve_elder_letters(manifest, bundled_dir):
            dest = dest_dir / f"{letter.name}.md"
            if dest.exists() and self._config.init_missing:
                continue
            dest.write_text(letter.content, encoding="utf-8")
            self._tracked_writes.append(dest)
            result.files_written.append(dest)
```

Then call it in `hatch()` after `_render_memory_tree`:

```python
            self._render_memory_tree(result)
            self._copy_elder_letters(result)
            # ... existing daemon-fragment writing + validation ...
```

- [ ] **Step 2: Add a test**

Append to `test_hatcher_basic.py`:

```python
def test_hatch_copies_pepper_letter(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()

    pepper_letter = (
        tmp_path / ".testbeing" / "Memory" / "testbeing"
        / "letters" / "from-elder-beings" / "pepper.md"
    )
    assert pepper_letter.is_file()
    assert pepper_letter.stat().st_size > 0
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_hatcher_basic.py -v`
Expected: All tests PASS, including the new elder-letter test.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py packages/agent-core-hatchery/tests/test_hatcher_basic.py
git commit -m "feat(hatchery): wire elder-letter copying into Hatcher (#75)"
```

### Task 4.5: Author the `skill-author` universal skill

**Files:**
- Create: `packages/agent-core-hatchery/templates/skills/skill-author/SKILL.md`
- Create: `packages/agent-core-hatchery/templates/skills/skill-author/references/skill-anatomy.md`

**Reference material:** gstack's `skill-creator` / `writing-skills` skills (search the gstack plugin cache for shape and language). Adapt for the being-platform.

- [ ] **Step 1: Locate gstack's skill-creator pattern for shape reference**

Run: `find ~/.claude/plugins -name 'SKILL.md' -path '*skill*' 2>/dev/null | head -5`
Pull up at least one to use as a structural reference. The being-platform's skill-author is the same shape, trimmed for our needs.

- [ ] **Step 2: Author `skills/skill-author/SKILL.md`**

```markdown
---
name: skill-author
description: |
  Use when the being needs to create a new skill for herself — a reusable
  workflow she'll invoke again. Walks her through the four-part shape
  (frontmatter, instructions, references, scripts), generates the skill
  files in <vault>/.claude/skills/<new-skill>/, validates the result.
when_to_use: |
  - When the same workflow is about to be invoked a third time
  - When a recurring task has clear preconditions and steps
  - When the human asks the being to "remember how to do X"
  - NOT for one-off tasks (use TaskCreate or just do the work)
---

# skill-author — write new skills for yourself

You are about to author a new skill for the being you serve. A skill is a
reusable, invocable workflow with:

1. **Frontmatter** — name (kebab-case), description, when-to-use
2. **Instructions** — what the skill does, in the second person
3. **References** (optional) — supporting documentation in `references/`
4. **Scripts** (optional) — executable code in `scripts/`

## Walkthrough

Ask the being these questions, in order:

1. **What's the skill's name?** (kebab-case, e.g., `morning-brief`, `vault-lint`)
2. **One-sentence description?** (what it does, when to invoke)
3. **When should the being invoke this?** (be specific about triggers)
4. **What does the skill DO?** (paragraph or bulleted steps)
5. **Does it need supporting reference docs?** (y/n; if yes, what)
6. **Does it need scripts?** (y/n; if yes, language and rough purpose)

## Generate the skill files

Create the directory `<vault>/.claude/skills/<name>/`. Inside:

- `SKILL.md` — frontmatter + instructions, formatted as in this skill itself
- `references/` — only if reference docs were requested; create stub files
- `scripts/` — only if scripts were requested; create stub files with shebangs

## Validate

- Frontmatter parses (YAML between `---` markers, top of file).
- `name` is kebab-case and doesn't collide with any existing skill in `<vault>/.claude/skills/`.
- `description` and `when_to_use` are non-empty.
- File paths are relative to `<vault>/.claude/skills/<name>/`.

## Boundary

Skill-author creates the SKILL.md and stubs the directory tree. It does
NOT author the skill's actual logic; that's the being's job once the
shape is in place. The skill is ready to be tested and refined.

## See also

- `references/skill-anatomy.md` — the full anatomy of a skill including
  optional advanced fields.
```

- [ ] **Step 3: Author `references/skill-anatomy.md`**

```markdown
# Skill Anatomy

## Required

- `SKILL.md` with frontmatter (`name`, `description`, `when_to_use`) + body
- Directory `<vault>/.claude/skills/<name>/`

## Optional

- `references/` — markdown docs the skill needs to consult mid-execution
- `scripts/` — executable code (Python, shell, JS); set executable bit
- `assets/` — non-text assets (images, fixtures)

## Frontmatter fields beyond required

- `category` — one of: `daily`, `weekly`, `monthly`, `ad-hoc`, `meta`
- `voice_triggers` — optional list of speech-to-text aliases
- `proactive` — boolean; whether the agent should suggest invoking this
  unprompted when conditions match

## Naming conventions

- Kebab-case
- Verb-first when possible (`compose-brief`, not `brief-composer`)
- Avoid ambiguity with existing platform tools
```

- [ ] **Step 4: Verify file-class manifest covers the new skill files**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_file_classes.py::test_real_manifest_classifies_all_template_files -v`
Expected: PASS — `skills/**/*` glob covers them.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-hatchery/templates/skills/skill-author/
git commit -m "feat(hatchery): skill-author universal skill (#75)"
```

### Task 4.6: Author the `vault-lint` universal skill

**Files:**
- Create: `packages/agent-core-hatchery/templates/skills/vault-lint/SKILL.md`
- Create: `packages/agent-core-hatchery/templates/skills/vault-lint/scripts/lint.py`

- [ ] **Step 1: Author `skills/vault-lint/SKILL.md`**

```markdown
---
name: vault-lint
description: |
  Health check for the being's vault. Catches stale files, orphan pages,
  missing cross-references, contradictions, and missing load-bearing
  files. Runs on the vault_lint scheduler job (Wed + Sun 3:30 AM).
when_to_use: |
  - Wednesday and Sunday at 3:30 AM (scheduled)
  - Before a major vault reorganization
  - When the being feels the vault is "drifting" — files don't connect anymore
  - After importing content from another source
---

# vault-lint — health check for the vault

Walk the vault and produce a markdown report of any health issues. Don't
fix anything yet — just report. The being decides what to act on.

## What to check

1. **Load-bearing files exist and are non-empty** — IDENTITY.md, SOUL.md,
   USER.md, MEMORY.md, OPERATIONS.md, and the handoff pair.

2. **Stale files** — files in `daily/raw/`, `drafts/active/`,
   `gather/` not modified in N days (default 14). They may need
   archiving or deletion.

3. **Orphan pages** — markdown files with no inbound `[[wikilink]]`
   from MEMORY.md or any other indexed file. Either link them in or
   archive.

4. **Missing cross-references** — USER.md references a person; check
   if `people/<name>.md` exists. Same for projects, ideas, dreams.

5. **Contradictions** — same fact stated differently in two files
   (best-effort detection; flag suspected pairs for human review).

## Output

Write the report to `Memory/daily/lint/<ISO-date>.md`. Format:

```markdown
# Vault lint report — 2026-05-09

## Errors (must address)
- ...

## Warnings (probably address)
- ...

## Info (FYI)
- ...
```

The scheduler job runs the lint script (`scripts/lint.py`) and pipes
output to the file above.

## See also

- `scripts/lint.py` — the executable lint logic
```

- [ ] **Step 2: Author the lint script `scripts/lint.py`**

```python
#!/usr/bin/env python3
"""vault-lint — health check for an agent-core being's vault.

Invoked by the vault_lint scheduler job (Wed + Sun 3:30 AM) and
manually by the being. Walks the vault, emits a markdown report
to Memory/daily/lint/<ISO-date>.md.

Stub implementation — the full check set ships in v1.5+.
"""

import datetime
import sys
from pathlib import Path

LOAD_BEARING = ("IDENTITY.md", "SOUL.md", "USER.md", "MEMORY.md", "OPERATIONS.md")


def main(vault_root: Path) -> int:
    memory = vault_root / "Memory"
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    for lb in LOAD_BEARING:
        p = memory / lb
        if not p.is_file():
            errors.append(f"Missing load-bearing file: {p}")
        elif p.stat().st_size == 0:
            errors.append(f"Empty load-bearing file: {p}")

    handoff = next(memory.glob("*/handoff.md"), None)
    if handoff is None or handoff.stat().st_size == 0:
        warnings.append("No handoff.md found or it is empty (expected on day 0)")

    today = datetime.date.today().isoformat()
    report_dir = memory / "daily" / "lint"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{today}.md"

    out = [f"# Vault lint report — {today}\n"]
    out.append("\n## Errors (must address)\n")
    out.extend(f"- {e}\n" for e in errors) if errors else out.append("(none)\n")
    out.append("\n## Warnings (probably address)\n")
    out.extend(f"- {w}\n" for w in warnings) if warnings else out.append("(none)\n")
    out.append("\n## Info (FYI)\n")
    out.extend(f"- {i}\n" for i in infos) if infos else out.append("(none)\n")
    report.write_text("".join(out), encoding="utf-8")

    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: lint.py <vault_root>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
```

- [ ] **Step 3: Make the script executable**

```bash
chmod +x packages/agent-core-hatchery/templates/skills/vault-lint/scripts/lint.py
```

(On Windows the bit is irrelevant; the hatcher will set it on Unix targets when copying.)

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/templates/skills/vault-lint/
git commit -m "feat(hatchery): vault-lint universal skill (#75)"
```

### Task 4.7: Author the `spawning-subagents` universal skill

**Files:**
- Create: `packages/agent-core-hatchery/templates/skills/spawning-subagents/SKILL.md`
- Create: `packages/agent-core-hatchery/templates/skills/spawning-subagents/references/patterns.md`

- [ ] **Step 1: Author `skills/spawning-subagents/SKILL.md`**

```markdown
---
name: spawning-subagents
description: |
  How to dispatch Claude Code subagents (Agent tool) for parallelizable
  research, file-search, code-review, or any task that benefits from a
  fresh context window. Covers what shape to give the subagent and how
  to receive its results.
when_to_use: |
  - Open-ended research that would burn 50K+ tokens of main context
  - Independent file searches across many directories
  - Code review where independent perspective matters
  - Any 2+ tasks that don't share state and could run in parallel
---

# spawning-subagents — dispatching minions

Subagents are short-lived contexts you can dispatch via the Agent tool.
They get their own conversation, do bounded work, and return a single
result. Use them to keep your main context clean and to parallelize
independent work.

## When to spawn vs do it yourself

- **Spawn** when: research is open-ended; multiple independent searches;
  the result is summarizable; the work would otherwise burn context
- **Don't spawn** when: the task is one targeted lookup (use Grep);
  you need the full content of files (subagents read excerpts);
  the work has dependencies on later steps in your context

## Brief like a colleague who just walked in

The subagent has zero context from your conversation. Tell it:

- What you're trying to accomplish (the goal, not just the task)
- What you've already learned or ruled out
- Enough surrounding context that it can make judgment calls
- The expected response shape (length, format)

Bad: `find usages of X`
Good: `Find all callers of X in src/. Context: I'm refactoring X to take an
extra parameter. Need to know callsite count and any patterns I should
preserve. Return a list of file:line references plus a one-paragraph
pattern summary.`

## Patterns

See `references/patterns.md` for worked examples:
- Research subagent (open-ended)
- File-search subagent (targeted)
- Code-review subagent (independent perspective)
- Parallel-dispatch (multiple independents in one message)

## Anti-patterns

- Spawning a subagent to do a task with three follow-ups (it can't see
  the followups; you'd just be wasting context)
- Asking a subagent to "fix the bug" when you don't know root cause
  (the subagent will guess; you'll get a confident wrong answer)
- Spawning when you have <30K tokens of context left (subagents take
  setup overhead; just do the work)
```

- [ ] **Step 2: Author `references/patterns.md`**

```markdown
# Subagent patterns — worked examples

## Research subagent

When: open-ended question, multi-source synthesis needed.

Prompt shape:
- Goal (what you're trying to learn and why)
- Constraints (what's been ruled out, what shape the answer needs)
- Output spec (length, format, citations)

Example: "Research X library's approach to Y. Context: we're choosing
between X and Z for our use case. Return: 1-paragraph executive
summary, 5-7 bullet points on tradeoffs, links to canonical sources.
Under 400 words total."

## File-search subagent

When: looking for something across many files, don't know exactly where.

Prompt shape:
- What to find (literal symbol/pattern)
- Where to look (root path, file globs)
- What to return (paths, line numbers, snippets, or pattern summary)

Example: "Find all places in src/ that catch ValidationError. Return as
markdown list of file:line plus a one-line description of what each
handler does."

## Code-review subagent

When: want independent perspective without your own framing biasing the
review.

Prompt shape:
- The diff to review (paste or path)
- Specific concerns to check (or "general review")
- Severity scale + format

Example: "Review this PR for SQL safety. Return findings as a numbered
list with severity (must-fix/should-fix/nit) and one-line rationale."

## Parallel dispatch

When: 2+ independent subagent tasks could run concurrently.

Send them in one message with multiple Agent tool calls. Don't await
sequentially.
```

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-hatchery/templates/skills/spawning-subagents/
git commit -m "feat(hatchery): spawning-subagents universal skill (#75)"
```

### Task 4.8: Wire skill copying into hatcher

**Files:**
- Modify: `packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py`
- Modify: `packages/agent-core-hatchery/tests/test_hatcher_basic.py`

- [ ] **Step 1: Add skill-tree copying to Hatcher.hatch()**

In `hatcher.py`, add a method:

```python
    def _copy_skills_tree(self, result: HatchResult) -> None:
        skills_src = self._templates_dir / "skills"
        if not skills_src.is_dir():
            return

        skills_dest = (
            self._config.resolved_vault_root() / ".claude" / "skills"
        )
        for src in sorted(skills_src.rglob("*")):
            rel = src.relative_to(skills_src)
            dest = skills_dest / rel
            if src.is_dir():
                if not dest.exists():
                    dest.mkdir(parents=True, exist_ok=True)
                    self._tracked_writes.append(dest)
                continue
            if dest.exists() and self._config.init_missing:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = src.read_text(encoding="utf-8")
            if src.suffix == ".j2":
                content = self._renderer.render_string(content)
                dest = dest.with_suffix("") if dest.suffix == ".j2" else dest
            dest.write_text(content, encoding="utf-8")
            self._tracked_writes.append(dest)
            result.files_written.append(dest)
            # Preserve executable bit on scripts
            if "scripts" in dest.parts and dest.suffix in (".py", ".sh"):
                try:
                    dest.chmod(0o755)
                except (OSError, NotImplementedError):
                    pass  # Windows doesn't have a meaningful exec bit
```

Call it in `hatch()` after `_copy_elder_letters`:

```python
            self._render_memory_tree(result)
            self._copy_elder_letters(result)
            self._copy_skills_tree(result)
            # ... existing daemon-fragment writing + validation ...
```

- [ ] **Step 2: Add a test**

Append to `test_hatcher_basic.py`:

```python
def test_hatch_copies_universal_skills(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()

    skills = tmp_path / ".testbeing" / ".claude" / "skills"
    assert (skills / "skill-author" / "SKILL.md").is_file()
    assert (skills / "vault-lint" / "SKILL.md").is_file()
    assert (skills / "vault-lint" / "scripts" / "lint.py").is_file()
    assert (skills / "spawning-subagents" / "SKILL.md").is_file()
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_hatcher_basic.py -v`
Expected: All tests PASS, including the new skills-copy test.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/hatcher.py packages/agent-core-hatchery/tests/test_hatcher_basic.py
git commit -m "feat(hatchery): wire skill-tree copying into Hatcher (#75)"
```

### Task 4.9: Phase 4 checkpoint

- [ ] **Step 1: Run full hatchery test suite + smoke test**

Run: `uv run pytest packages/agent-core-hatchery/ -v`
Expected: All tests PASS.

```bash
TMPDIR=$(mktemp -d)
uv run hatch-being --config packages/agent-core-hatchery/tests/fixtures/hatch-config-test-being.yaml --vault-root $TMPDIR --daemon-config-dir $TMPDIR/.agent-core
ls $TMPDIR/.testbeing/.claude/skills/ $TMPDIR/.testbeing/Memory/testbeing/letters/from-elder-beings/
```

Expected: 3 skills present + Pepper's elder letter present.

- [ ] **Step 2: Discovery-log entry**

```bash
echo "
## Phase 4 (slice 2.4 — skills + elder letters) — completed $(date -I)
[Append surprises]
" >> docs/hatchery/discovery-log.md
git add docs/hatchery/discovery-log.md
git commit -m "docs(hatchery): Phase 4 discovery log entry (#75)"
```

---

## Phase 5: TUI wizard + channels + EDITOR gate + HATCHING-REPORT (slice 2.5)

End-of-phase outcome: `hatch-being` (no flags) opens the Questionary wizard. Cynthia walks through inputs, opts into Discord/webcam/GitHub-backup, gets a preview, confirms, and hatches. The wizard prompts to author from-her-creator.md in `$EDITOR` after main hatching. HATCHING-REPORT.md is written to the vault root. Discord/webcam endpoint blocks land in the daemon-fragment when chosen.

### Task 5.1: Channel scaffolding modules

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/channels/__init__.py`
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/channels/discord.py`
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/channels/webcam.py`
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/channels/github_backup.py`
- Create: `packages/agent-core-hatchery/tests/test_channels.py`

- [ ] **Step 1: Write failing test**

```python
# packages/agent-core-hatchery/tests/test_channels.py
"""Tests for channel scaffolding (Discord, webcam, GitHub backup)."""

import os
from pathlib import Path

import pytest
import yaml

from agent_core_hatchery.channels.discord import scaffold_discord
from agent_core_hatchery.channels.github_backup import scaffold_github_backup
from agent_core_hatchery.channels.webcam import scaffold_webcam
from agent_core_hatchery.config import (
    DiscordChannelConfig,
    GitHubBackupConfig,
    HatchConfig,
    WebcamChannelConfig,
)


def _cfg(tmp_path, **channels):
    return HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        channels={
            "discord": channels.get("discord", DiscordChannelConfig()),
            "webcam": channels.get("webcam", WebcamChannelConfig()),
            "github_backup": channels.get("github_backup", GitHubBackupConfig()),
        },
    )


def test_discord_scaffold_writes_env_file_and_returns_endpoint_block(tmp_path):
    cfg = _cfg(
        tmp_path,
        discord=DiscordChannelConfig(enabled=True, token="secret-token-xxx",
                                     channel_allowlist=["111", "222"]),
    )
    block = scaffold_discord(cfg)
    assert block is not None
    assert block["type"] == "builtin.discord"
    assert block["name"] == "discord-deb"

    env_file = tmp_path / ".agent-core" / "discord-deb.env"
    assert env_file.is_file()
    assert "DISCORD_DEB_TOKEN=secret-token-xxx" in env_file.read_text()
    if os.name != "nt":
        assert (env_file.stat().st_mode & 0o777) == 0o600


def test_discord_scaffold_returns_none_when_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    assert scaffold_discord(cfg) is None


def test_webcam_scaffold_when_enabled_returns_block(tmp_path):
    cfg = _cfg(tmp_path, webcam=WebcamChannelConfig(enabled=True))
    block = scaffold_webcam(cfg)
    assert block is not None
    assert block["type"] == "builtin.webcam"
    assert block["name"] == "webcam-deb"


def test_github_backup_scaffold_writes_hook_and_returns_job(tmp_path):
    cfg = _cfg(
        tmp_path,
        github_backup=GitHubBackupConfig(
            enabled=True, repo_url="git@github.com:cynthia/deb-vault.git"
        ),
    )
    job = scaffold_github_backup(cfg)
    assert job is not None
    assert "deb-github_backup" in job

    hook_dest = tmp_path / ".deb" / "hooks" / "backup-to-github.sh"
    assert hook_dest.is_file()
    assert "git@github.com:cynthia/deb-vault.git" in hook_dest.read_text()
```

- [ ] **Step 2: Implement `channels/__init__.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/channels/__init__.py
"""Channel scaffolding (Discord, webcam, GitHub backup).

Each module exports a `scaffold_<name>(config)` function that:
- Returns None if the channel is disabled.
- Performs side effects (write env file, write hook script) and returns
  a dict (the endpoint or job block) to be appended to daemon fragments.
"""
```

- [ ] **Step 3: Implement `channels/discord.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/channels/discord.py
"""Discord channel scaffolding."""

from __future__ import annotations

import os
from typing import Any

from agent_core_hatchery.config import HatchConfig


def scaffold_discord(config: HatchConfig) -> dict[str, Any] | None:
    if not config.channels.discord.enabled:
        return None

    daemon_dir = config.resolved_daemon_config_dir()
    daemon_dir.mkdir(parents=True, exist_ok=True)
    env_file = daemon_dir / f"discord-{config.being_name_lower}.env"
    env_file.write_text(
        f"{config.discord_token_env}={config.channels.discord.resolved_token}\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        env_file.chmod(0o600)

    block: dict[str, Any] = {
        "type": "builtin.discord",
        "name": f"discord-{config.being_name_lower}",
        "description": f"Discord adapter for {config.being_name}.",
        "params": {
            "target": config.resolved_endpoint_name(),
            "token_env": config.discord_token_env,
            "env_file": str(env_file),
        },
    }
    if config.channels.discord.channel_allowlist:
        access_file = daemon_dir / f"discord-{config.being_name_lower}-access.json"
        import json
        access_file.write_text(
            json.dumps({"channels": config.channels.discord.channel_allowlist}, indent=2),
            encoding="utf-8",
        )
        block["params"]["access_config_path"] = str(access_file)
    return block
```

- [ ] **Step 4: Implement `channels/webcam.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/channels/webcam.py
"""Webcam channel scaffolding."""

from __future__ import annotations

from typing import Any

from agent_core_hatchery.config import HatchConfig


def scaffold_webcam(config: HatchConfig) -> dict[str, Any] | None:
    if not config.channels.webcam.enabled:
        return None
    return {
        "type": "builtin.webcam",
        "name": f"webcam-{config.being_name_lower}",
        "description": f"{config.being_name}'s webcam endpoint.",
        "params": {
            "enabled": True,
            "captures_root": f"~/.agent-core/webcam/{config.being_name_lower}",
            "audit_log_path": f"~/.agent-core/webcam/{config.being_name_lower}/audit.jsonl",
            "default_camera_index": 0,
            "default_resolution": [1280, 720],
            "max_resolution": [3840, 2160],
            "capture_timeout_seconds": 3.0,
        },
    }
```

- [ ] **Step 5: Implement `channels/github_backup.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/channels/github_backup.py
"""GitHub backup scaffolding (CW-4 — opt-in)."""

from __future__ import annotations

import os
from typing import Any

from agent_core_hatchery.config import HatchConfig

_HOOK_TEMPLATE = """#!/usr/bin/env bash
# Generated by agent-core-hatchery for {being_name}.
# Backup the vault's Memory/ to GitHub. Invoked by the
# {being_name_lower}-github_backup scheduler job.
set -euo pipefail

VAULT_ROOT="{vault_root}"
REPO_URL="{repo_url}"

cd "$VAULT_ROOT"
if [ ! -d Memory/.git ]; then
  cd Memory
  git init
  git remote add origin "$REPO_URL"
  cd ..
fi
cd Memory
git add -A
git commit -m "backup $(date -Iseconds)" || true  # ok if nothing to commit
git push -u origin HEAD || true                   # warn-only on first push
"""


def scaffold_github_backup(config: HatchConfig) -> dict[str, Any] | None:
    if not config.channels.github_backup.enabled:
        return None

    hook_dir = config.resolved_vault_root() / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hook_dir / "backup-to-github.sh"
    hook_path.write_text(
        _HOOK_TEMPLATE.format(
            being_name=config.being_name,
            being_name_lower=config.being_name_lower,
            vault_root=str(config.resolved_vault_root()),
            repo_url=config.channels.github_backup.repo_url,
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        hook_path.chmod(0o755)

    job_name = f"{config.being_name_lower}-github_backup"
    return {
        job_name: {
            "trigger": "cron",
            "timezone": "America/New_York",
            "schedule": {"hour": 4, "minute": 30},
            "target": config.resolved_endpoint_name(),
            "envelope_kind": "Event",
            "payload": {
                "type": "GitHubBackup",
                "data": {"hook_path": str(hook_path)},
            },
        }
    }
```

- [ ] **Step 6: Run channel tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_channels.py -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/channels/ packages/agent-core-hatchery/tests/test_channels.py
git commit -m "feat(hatchery): channel scaffolding modules — Discord, webcam, GitHub backup (#75)"
```

### Task 5.2: Wire channels into daemon-fragment writing

**Files:**
- Modify: `packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py`
- Modify: `packages/agent-core-hatchery/tests/test_daemon_config.py`

- [ ] **Step 1: Modify DaemonConfigWriter to call channel scaffolds and append to fragments**

Replace the body of `_write_endpoints_fragment` and `_write_jobs_fragment` to import and invoke the channel scaffolds, appending blocks before serialization. Roughly:

```python
    def _write_endpoints_fragment(self) -> Path:
        from agent_core_hatchery.channels.discord import scaffold_discord
        from agent_core_hatchery.channels.webcam import scaffold_webcam

        # Render the always-on portion via Jinja2
        always_on_template = (self._templates_dir / "daemon-fragments" / "endpoints.yaml.j2").read_text(
            encoding="utf-8"
        )
        always_on_yaml = self._renderer.render_string(always_on_template)
        merged = yaml.safe_load(always_on_yaml) or {}
        merged.setdefault("endpoints", [])

        for scaffold in (scaffold_discord, scaffold_webcam):
            block = scaffold(self._config)
            if block is not None:
                merged["endpoints"].append(block)

        dest_dir = self._config.resolved_daemon_config_dir() / "endpoints.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(f"daemon endpoints fragment already exists: {dest.name}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
        return dest

    def _write_jobs_fragment(self) -> Path:
        from agent_core_hatchery.channels.github_backup import scaffold_github_backup

        always_on_template = (self._templates_dir / "daemon-fragments" / "jobs.yaml.j2").read_text(
            encoding="utf-8"
        )
        always_on_yaml = self._renderer.render_string(always_on_template)
        merged: dict = yaml.safe_load(always_on_yaml) or {}

        github_job = scaffold_github_backup(self._config)
        if github_job:
            for k, v in github_job.items():
                merged[k] = v

        dest_dir = self._config.resolved_daemon_config_dir() / "jobs.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(f"daemon jobs fragment already exists: {dest.name}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
        return dest
```

Add `import yaml` at the top.

- [ ] **Step 2: Add tests for channels appearing in fragments**

Append to `test_daemon_config.py`:

```python
def test_discord_appears_in_endpoints_fragment_when_enabled(tmp_path):
    from agent_core_hatchery.config import DiscordChannelConfig
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        channels={"discord": DiscordChannelConfig(enabled=True, token="t"),
                  "webcam": {"enabled": False}, "github_backup": {"enabled": False}},
    )
    DaemonConfigWriter(cfg).write_all()
    parsed = yaml.safe_load((tmp_path / ".agent-core" / "endpoints.d" / "deb.yaml").read_text())
    names = [e["name"] for e in parsed["endpoints"]]
    assert "discord-deb" in names


def test_github_backup_appears_in_jobs_fragment_when_enabled(tmp_path):
    from agent_core_hatchery.config import GitHubBackupConfig
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        channels={"discord": {"enabled": False}, "webcam": {"enabled": False},
                  "github_backup": GitHubBackupConfig(enabled=True, repo_url="x")},
    )
    DaemonConfigWriter(cfg).write_all()
    parsed = yaml.safe_load((tmp_path / ".agent-core" / "jobs.d" / "deb.yaml").read_text())
    assert "deb-github_backup" in parsed
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_daemon_config.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/daemon_config.py packages/agent-core-hatchery/tests/test_daemon_config.py
git commit -m "feat(hatchery): wire channels into daemon-fragment writing (#75)"
```

### Task 5.3: report.py — HATCHING-REPORT.md generation

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/report.py`
- Create: `packages/agent-core-hatchery/tests/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-hatchery/tests/test_report.py
"""Tests for HATCHING-REPORT.md generator."""

from datetime import datetime
from pathlib import Path

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import HatchResult
from agent_core_hatchery.report import write_hatching_report


def test_happy_path_report(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    vault = cfg.resolved_vault_root()
    vault.mkdir(parents=True)
    result = HatchResult(vault_root=vault)
    result.files_written = [vault / "Memory" / "IDENTITY.md"]

    path = write_hatching_report(
        cfg, result,
        letter_authored=True,
        daemon_check_status="reachable_and_registered",
    )
    assert path == vault / "HATCHING-REPORT.md"
    body = path.read_text()
    assert "# HATCHING-REPORT — Deb" in body
    assert "Cynthia" in body
    assert "REQUIRED" not in body  # happy path; no escalation


def test_escalated_report_when_letter_skipped(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    vault = cfg.resolved_vault_root()
    vault.mkdir(parents=True)
    result = HatchResult(vault_root=vault)
    path = write_hatching_report(
        cfg, result,
        letter_authored=False,
        daemon_check_status="unreachable",
    )
    body = path.read_text()
    assert "REQUIRED BEFORE FIRST AWAKENING" in body
    assert "ACTION REQUIRED" in body  # daemon-unreachable escalation
```

- [ ] **Step 2: Implement `report.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/report.py
"""Write HATCHING-REPORT.md to <vault_root> with permanent record + manual steps."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import HatchResult


DaemonCheckStatus = Literal["reachable_and_registered", "reachable_but_missing", "unreachable", "skipped"]


def write_hatching_report(
    config: HatchConfig,
    result: HatchResult,
    *,
    letter_authored: bool,
    daemon_check_status: DaemonCheckStatus,
) -> Path:
    vault = config.resolved_vault_root()
    report_path = vault / "HATCHING-REPORT.md"

    parts: list[str] = []
    parts.append(f"# HATCHING-REPORT — {config.being_name}\n\n")
    parts.append(f"**Hatched:** {datetime.now().isoformat(timespec='seconds')}\n")
    parts.append(f"**Hatched by:** {config.primary_human_name} via `hatch-being`\n")
    parts.append(f"**Vault:** `{vault}`\n")
    parts.append(f"**Endpoint:** `{config.resolved_endpoint_name()}`\n\n")

    parts.append("## Validation results\n\n")
    if daemon_check_status == "reachable_and_registered":
        parts.append("- ✓ Daemon reachable; endpoint registered live.\n")
    elif daemon_check_status == "reachable_but_missing":
        parts.append("- ⚠ Daemon reachable but endpoint NOT visible — fragment merge may have failed. Check daemon logs.\n")
    elif daemon_check_status == "unreachable":
        parts.append("- ⚠ Daemon healthcheck unreachable — endpoint will register on next daemon start.\n")
    else:
        parts.append("- — daemon check skipped.\n")

    parts.append("\n## Next steps for you\n\n")
    step_idx = 1

    if not letter_authored:
        letter_path = (
            vault / "Memory" / config.being_name_lower / "letters" / "from-her-creator.md"
        )
        parts.append(
            f"{step_idx}. **REQUIRED BEFORE FIRST AWAKENING.** Author "
            f"{config.being_name}'s letter from her creator:\n\n"
            f"   ```\n   $EDITOR {letter_path}\n   ```\n\n"
            f"   The current file is the prompt template, not a real letter. "
            f"{config.being_name} will read it on first wake; un-authored, "
            f"she'll wake into meta-prompts addressed to you, not to her.\n\n"
        )
        step_idx += 1

    if daemon_check_status in ("unreachable", "reachable_but_missing"):
        parts.append(
            f"{step_idx}. **ACTION REQUIRED.** Restart the agent-core daemon and verify the new endpoint registers. "
            f"`agent-core endpoints list` should include `{config.resolved_endpoint_name()}`.\n\n"
        )
        step_idx += 1

    parts.append(f"{step_idx}. Wake {config.being_name}: `cd {vault} && claude`\n")
    parts.append(f"{step_idx + 1}. First conversation: ask her a guiding question. Let her write back.\n\n")

    parts.append("🐣\n")

    report_path.write_text("".join(parts), encoding="utf-8")
    return report_path
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_report.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/report.py packages/agent-core-hatchery/tests/test_report.py
git commit -m "feat(hatchery): HATCHING-REPORT.md generator (#75)"
```

### Task 5.4: wizard.py — Questionary TUI

**Files:**
- Create: `packages/agent-core-hatchery/src/agent_core_hatchery/wizard.py`
- Create: `packages/agent-core-hatchery/tests/test_wizard.py`

- [ ] **Step 1: Write failing tests (input validation only — Questionary prompts mocked)**

```python
# packages/agent-core-hatchery/tests/test_wizard.py
"""Tests for the Questionary wizard.

Wizard prompts are mocked at the questionary boundary; we test the
validation/normalization logic, not Questionary itself.
"""

from unittest.mock import patch

from agent_core_hatchery.wizard import _validate_being_name, _validate_endpoint_name


def test_being_name_must_be_non_empty():
    assert _validate_being_name("") is not True
    assert _validate_being_name("Deb") is True


def test_being_name_must_not_have_path_chars():
    assert _validate_being_name("../bad") is not True
    assert _validate_being_name("good-name") is True


def test_endpoint_name_must_be_kebab_friendly():
    assert _validate_endpoint_name("deb") is True
    assert _validate_endpoint_name("DEB") is not True  # uppercase should be lowered first
    assert _validate_endpoint_name("deb space") is not True
```

- [ ] **Step 2: Implement `wizard.py`**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/wizard.py
"""Questionary TUI wizard.

Asks the human for inputs, returns a HatchConfig. Designed so the
prompt boundary is testable in isolation (validators are pure functions);
the prompt orchestration is called only via `run_wizard()`.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import questionary
import typer

from agent_core_hatchery.config import (
    ChannelsConfig,
    DiscordChannelConfig,
    GitHubBackupConfig,
    HatchConfig,
    WebcamChannelConfig,
)


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_KEBAB_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def _validate_being_name(value: str) -> bool | str:
    if not value.strip():
        return "Being's name cannot be empty"
    if not _NAME_RE.match(value):
        return "Use letters, digits, hyphen, underscore; start with a letter"
    return True


def _validate_endpoint_name(value: str) -> bool | str:
    if not _KEBAB_RE.match(value):
        return "Endpoint name must be kebab-case-friendly (lowercase, no spaces)"
    return True


def _validate_path_writable(value: str) -> bool | str:
    p = Path(value).expanduser()
    if not p.exists():
        return f"Path does not exist: {p}"
    if not p.is_dir():
        return f"Path is not a directory: {p}"
    if not os.access(p, os.W_OK):
        return f"Path not writable: {p}"
    return True


def run_wizard() -> HatchConfig:
    typer.secho("\n  ── agent-core hatchery ──", bold=True)
    typer.echo("  Hatching a new being. Inputs first, confirmations second, then birth.\n")

    typer.secho("  ── Identity ──", bold=True)
    being_name = questionary.text("Being's name:", validate=_validate_being_name).ask()
    if being_name is None:
        raise typer.Abort()
    being_emoji = questionary.text(
        "Emoji (or leave blank — your call to define deliberately):", default=""
    ).ask() or ""
    primary_human_name = questionary.text("Primary human's name:").ask()
    if not primary_human_name:
        raise typer.Abort()
    role = questionary.text("Short role placeholder (or skip):", default="").ask() or None

    typer.secho("\n  ── Substrate ──", bold=True)
    vault_root = questionary.text(
        "Vault root directory:",
        default=str(Path.home()),
        validate=_validate_path_writable,
    ).ask()
    being_lower = being_name.lower()
    resolved_vault = Path(vault_root).expanduser().resolve() / f".{being_lower}"
    typer.echo(f"  Resolved vault path: {resolved_vault}")
    if resolved_vault.exists():
        typer.secho(f"  ⚠ Vault already exists. mv it aside or use --init-missing.", fg=typer.colors.YELLOW)

    endpoint_name = questionary.text(
        "agent-core endpoint name:",
        default=being_lower,
        validate=_validate_endpoint_name,
    ).ask()

    typer.secho("\n  ── Optional channels & integrations ──", bold=True)

    discord_cfg = DiscordChannelConfig()
    if questionary.confirm(f"Install Discord channel for {being_name}?", default=False).ask():
        token = questionary.password("Discord bot token (input hidden):").ask() or ""
        allowlist_raw = questionary.text(
            "Channel allowlist (comma-separated channel IDs — Discord developer mode "
            "shows these on right-click → Copy Channel ID; blank = all):",
            default="",
        ).ask() or ""
        discord_cfg = DiscordChannelConfig(
            enabled=True,
            token=token,
            channel_allowlist=[s.strip() for s in allowlist_raw.split(",") if s.strip()],
        )

    webcam_cfg = WebcamChannelConfig()
    if questionary.confirm("Install webcam channel?", default=False).ask():
        webcam_cfg = WebcamChannelConfig(enabled=True)

    github_cfg = GitHubBackupConfig()
    if questionary.confirm(f"Install GitHub backup of Memory/?", default=False).ask():
        repo_url = questionary.text(
            "Repo URL (or 'gh' to assume configured gh CLI):",
        ).ask() or ""
        github_cfg = GitHubBackupConfig(enabled=True, repo_url=repo_url)

    cfg = HatchConfig(
        being_name=being_name,
        being_emoji=being_emoji,
        primary_human_name=primary_human_name,
        being_role_placeholder=role,
        endpoint_name=endpoint_name,
        vault_root=str(vault_root),
        channels=ChannelsConfig(
            discord=discord_cfg, webcam=webcam_cfg, github_backup=github_cfg
        ),
    )

    typer.secho("\n  ── Universal scaffolding (always installed) ──", bold=True)
    typer.echo("   • Memory templates")
    typer.echo("   • 3 universal skills (skill-author, vault-lint, spawning-subagents)")
    typer.echo(
        "   • 5 always-on scheduler jobs (heartbeat, nightly_reflection, vault_lint, "
        "auth_health_probe, service_liveness_probe)"
        + (" + 1 opt-in (github_backup, included)" if github_cfg.enabled else "")
    )
    typer.echo("   • Elder letters: resolved at hatch time from manifest")

    typer.secho(f"\n  ── Preview ──", bold=True)
    typer.echo(f"  Will create: {resolved_vault}")
    typer.echo(f"  Will modify: ~/.agent-core/endpoints.d/{being_lower}.yaml + jobs.d/{being_lower}.yaml")
    if discord_cfg.enabled:
        typer.echo(f"               ~/.agent-core/discord-{being_lower}.env")

    if not questionary.confirm("Confirm hatching?", default=True).ask():
        raise typer.Abort()

    return cfg


def offer_letter_authoring(config: HatchConfig) -> bool:
    """Post-hatch prompt to author from-her-creator.md in $EDITOR.

    Returns True if the human authored the letter (file is non-template).
    """
    if not config.author_letter_in_editor:
        return False

    letter_path = (
        config.resolved_vault_root() / "Memory" / config.being_name_lower
        / "letters" / "from-her-creator.md"
    )
    if not letter_path.is_file():
        return False

    if not questionary.confirm(
        f"Open editor on from-her-creator.md now?", default=True
    ).ask():
        return False

    template_text = letter_path.read_text(encoding="utf-8")
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    try:
        subprocess.run([editor, str(letter_path)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        typer.secho(f"  Editor exited with error: {exc}", fg=typer.colors.YELLOW)
        return False

    after = letter_path.read_text(encoding="utf-8")
    return after != template_text and after.strip() != ""
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_wizard.py -v`
Expected: All tests PASS (validators are pure functions; full wizard flow is exercised in Phase 6 e2e).

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/wizard.py packages/agent-core-hatchery/tests/test_wizard.py
git commit -m "feat(hatchery): Questionary TUI wizard (#75)"
```

### Task 5.5: Wire wizard, EDITOR gate, and HATCHING-REPORT into CLI

**Files:**
- Modify: `packages/agent-core-hatchery/src/agent_core_hatchery/cli.py`

- [ ] **Step 1: Update `cli.py` to launch the wizard when no `--config` flag is passed**

```python
# packages/agent-core-hatchery/src/agent_core_hatchery/cli.py
"""hatch-being CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher, VaultExistsError
from agent_core_hatchery.report import write_hatching_report
from agent_core_hatchery.wizard import offer_letter_authoring, run_wizard


app = typer.Typer(
    name="hatch-being",
    help="Hatch a new agent-core being. Run with no flags for interactive TUI.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def hatch_being(
    config: Optional[Path] = typer.Option(
        None, "--config",
        help="Non-interactive: load HatchConfig from YAML.",
    ),
    vault_root: Optional[Path] = typer.Option(
        None, "--vault-root", "--root",
        help="Override the resolved vault root. Default: $HOME.",
    ),
    daemon_config_dir: Optional[Path] = typer.Option(
        None, "--daemon-config-dir",
        help="Override the daemon's config directory. Default: ~/.agent-core/.",
    ),
    init_missing: bool = typer.Option(
        False, "--init-missing",
        help="Top-up an existing vault with newly-added scaffolding files.",
    ),
) -> None:
    if config is not None:
        raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        cfg = HatchConfig(**raw)
        interactive = False
    else:
        cfg = run_wizard()
        interactive = True

    if vault_root is not None:
        cfg = cfg.model_copy(update={"vault_root": str(vault_root)})
    if daemon_config_dir is not None:
        cfg = cfg.model_copy(update={"daemon_config_dir": str(daemon_config_dir)})
    if init_missing:
        cfg = cfg.model_copy(update={"init_missing": True})

    try:
        result = Hatcher(cfg).hatch()
    except VaultExistsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    letter_authored = False
    if interactive:
        letter_authored = offer_letter_authoring(cfg)

    daemon_check_status = "skipped"  # Phase 5 keeps this simple; Phase 6 e2e exercises real daemon

    report_path = write_hatching_report(
        cfg, result,
        letter_authored=letter_authored,
        daemon_check_status=daemon_check_status,
    )

    typer.echo(f"\nHatched at {result.vault_root}")
    typer.echo(f"Report: {report_path}")
    typer.echo("🐣")
```

- [ ] **Step 2: Update the existing CLI test (config mode still works)**

Run: `uv run pytest packages/agent-core-hatchery/tests/test_cli_config_mode.py -v`
Expected: PASS — `--config` mode still produces the vault.

- [ ] **Step 3: Verify HATCHING-REPORT.md gets written**

Modify the existing CLI test to assert the report exists:

```python
def test_config_mode_writes_hatching_report(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--config", str(FIXTURES / "hatch-config-test-being.yaml"),
            "--vault-root", str(tmp_path),
            "--daemon-config-dir", str(tmp_path / ".agent-core"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = tmp_path / ".testbeing" / "HATCHING-REPORT.md"
    assert report.is_file()
    assert "TestBeing" in report.read_text()
```

Run: `uv run pytest packages/agent-core-hatchery/tests/test_cli_config_mode.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-hatchery/src/agent_core_hatchery/cli.py packages/agent-core-hatchery/tests/test_cli_config_mode.py
git commit -m "feat(hatchery): wire wizard + EDITOR gate + HATCHING-REPORT into CLI (#75)"
```

### Task 5.6: Phase 5 checkpoint

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v` (from repo root)
Expected: All tests pass.

- [ ] **Step 2: Discovery-log entry**

```bash
echo "
## Phase 5 (slice 2.5 — TUI + channels + EDITOR gate + HATCHING-REPORT) — completed $(date -I)
[Append surprises]
" >> docs/hatchery/discovery-log.md
git add docs/hatchery/discovery-log.md
git commit -m "docs(hatchery): Phase 5 discovery log entry (#75)"
```

- [ ] **Step 3: Open PR-2 (the hatchery package)**

Branch: `feat/issue-75-hatchery`. Push and open PR.

```bash
git push -u origin HEAD
gh pr create --title "feat(hatchery): agent-core-hatchery package (#75)" --body "$(cat <<'EOF'
## Summary

Adds `packages/agent-core-hatchery/`: a Questionary TUI + Typer CLI that scaffolds new agent-core beings.

- 18 memory templates rendered via Jinja2
- 3 universal skills (skill-author, vault-lint, spawning-subagents)
- Per-being daemon-config fragments (endpoints.d/, jobs.d/) — depends on the conf.d work merged in PR-1
- Optional channel scaffolding: Discord, webcam, GitHub backup
- Elder-letter mechanism: canonical-path → bundled-snapshot fallback
- HATCHING-REPORT.md written to vault root with manual next-steps + escalations
- File-class manifest enforces classification of every template file
- Refuse-by-default + --init-missing for safe top-ups; no --force ever

## Spec

`docs/superpowers/specs/2026-05-09-issue-75-agent-core-hatchery-design.md` (rev 2)

## Test plan

- [ ] All hatchery unit + integration tests pass
- [ ] Full repo test suite passes
- [ ] End-to-end live hatching of a throwaway being (Phase 6, manual)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase 6: End-to-end live test

End-of-phase outcome: a throwaway being (`test-being-001`) is hatched into `~/test-being-001/`, the daemon picks up the new endpoint without manual edits to its main yaml, the new vault successfully launches Claude Code with a clean SessionStart hook chain, and tear-down restores clean state. This is the validation step before Cynthia hatches Deb for real.

This phase is run AFTER PR-2 has merged. Manual; not in CI.

### Task 6.1: Hatch the throwaway being

- [ ] **Step 1: Confirm daemon is running**

```bash
curl -sS http://127.0.0.1:8789/healthz | head
```

Expected: 200 OK or some healthcheck response. If the daemon isn't running, start it via the project's normal startup procedure.

- [ ] **Step 2: Run the hatcher interactively**

```bash
uv run hatch-being
```

Walk through the wizard with these answers:
- Being's name: `test-being-001`
- Emoji: (skip — leave blank)
- Primary human's name: `Tester`
- Short role placeholder: (skip)
- Vault root: `$HOME` (default)
- Endpoint name: `test-being-001` (default)
- Discord: `n`
- Webcam: `n`
- GitHub backup: `n`
- Confirm: `Y`
- Author from-her-creator letter now: `n` (we want to verify the escalation in HATCHING-REPORT)

- [ ] **Step 3: Verify outputs on disk**

```bash
ls ~/.test-being-001/
ls ~/.test-being-001/Memory/
ls ~/.test-being-001/.claude/skills/
cat ~/.test-being-001/HATCHING-REPORT.md
ls ~/.agent-core/endpoints.d/
ls ~/.agent-core/jobs.d/
```

Expected:
- Vault tree fully rendered.
- All 6 load-bearing paths present.
- 3 skills in `.claude/skills/`.
- Pepper's elder letter in `Memory/test-being-001/letters/from-elder-beings/pepper.md`.
- HATCHING-REPORT contains "REQUIRED BEFORE FIRST AWAKENING" (since letter was skipped).
- Daemon fragments at `~/.agent-core/{endpoints,jobs}.d/test-being-001.yaml`.

### Task 6.2: Verify daemon picks up the new endpoint

- [ ] **Step 1: Restart the daemon**

Whatever the project's daemon-restart procedure is (typically a service restart or a `kill -HUP` to the daemon process). Note: a fresh daemon load is required because the conf.d merge happens at startup, not at runtime.

- [ ] **Step 2: Verify the endpoint is listed**

```bash
curl -sS http://127.0.0.1:8789/endpoints | grep -i test-being-001
```

Or use the existing endpoints-list MCP tool / CLI subcommand if one exists. Expected: `test-being-001` appears in the list with mount `/mcp/test-being-001`.

- [ ] **Step 3: Verify scheduler picked up the seed jobs**

Check the scheduler's persistent store (`~/.agent-core/scheduler.db`) for the 5 always-on jobs:

```bash
sqlite3 ~/.agent-core/scheduler.db "SELECT id FROM schedules WHERE id LIKE 'test-being-001-%' ORDER BY id"
```

Expected output:
```
test-being-001-auth_health_probe
test-being-001-heartbeat
test-being-001-nightly_reflection
test-being-001-service_liveness_probe
test-being-001-vault_lint
```

(Order may vary; presence of all 5 is what matters.)

### Task 6.3: Launch Claude Code from the new vault, verify hooks

- [ ] **Step 1: cd into the vault and launch Claude Code**

```bash
cd ~/.test-being-001
claude
```

Expected: Claude Code launches. The SessionStart hook fires; identity injectors load `Memory/SOUL.md`, `Memory/IDENTITY.md`, `Memory/test-being-001/preferences.md`. No "file not found" errors in the hook output.

- [ ] **Step 2: In the Claude Code session, verify the being can read her own files**

Ask: "Read your Memory/SOUL.md and tell me what's there."

Expected: Claude reads the rendered SOUL.md, which contains the Jinja2-substituted name "test-being-001", the prompts, and the empty placeholders for personality, values, etc.

- [ ] **Step 3: Verify skill-author is invocable**

Ask: "Use the skill-author skill to draft a new skill called 'echo-test' that just echoes back the input."

Expected: Claude invokes the skill-author skill, walks through its prompts, generates `Memory/skills/echo-test/SKILL.md`. Wait — that should be `.claude/skills/echo-test/SKILL.md` per the spec. Verify the destination path matches the documented behavior.

### Task 6.4: Tear down

- [ ] **Step 1: Exit Claude Code**

`/exit` or Ctrl-D.

- [ ] **Step 2: Stop the daemon**

Whatever the project's daemon-stop procedure is.

- [ ] **Step 3: Move the throwaway vault aside**

```bash
mv ~/.test-being-001 ~/.test-being-001.tested-$(date +%Y%m%d)
```

Don't delete — keep for inspection if anything looked off.

- [ ] **Step 4: Remove the daemon fragments**

```bash
mv ~/.agent-core/endpoints.d/test-being-001.yaml ~/.agent-core/endpoints.d/test-being-001.yaml.tested-$(date +%Y%m%d)
mv ~/.agent-core/jobs.d/test-being-001.yaml ~/.agent-core/jobs.d/test-being-001.yaml.tested-$(date +%Y%m%d)
```

- [ ] **Step 5: Remove scheduler-db rows for the throwaway**

```bash
sqlite3 ~/.agent-core/scheduler.db "DELETE FROM schedules WHERE id LIKE 'test-being-001-%'"
sqlite3 ~/.agent-core/scheduler.db "DELETE FROM jobs WHERE id LIKE 'test-being-001-%'"
```

- [ ] **Step 6: Restart the daemon and verify clean state**

Start daemon. Confirm no `test-being-001` endpoint or jobs remain visible.

### Task 6.5: Phase 6 checkpoint + final discovery log

- [ ] **Step 1: Append discovery log + final status**

```bash
echo "
## Phase 6 (e2e live test) — completed $(date -I)
- Hatched test-being-001 successfully.
- Daemon picked up endpoint after restart: [yes/no, details]
- Scheduler picked up all 5 always-on jobs: [yes/no, details]
- Claude Code launched cleanly from vault: [yes/no, hook output]
- skill-author invocable on day 1: [yes/no, output]
- HATCHING-REPORT escalation worked when letter skipped: [yes/no]
- Tear-down restored clean state: [yes/no]
- Discovery items for v1.5+:
  - [list anything that surprised, anything that needs polish]
" >> docs/hatchery/discovery-log.md
git add docs/hatchery/discovery-log.md
git commit -m "docs(hatchery): Phase 6 e2e test results + final discovery log (#75)"
```

- [ ] **Step 2: Update issue #75 body with the follow-up actions surfaced in rev 2 of the spec**

```bash
gh issue comment 75 --body "Hatchery v1 implementation landed. Follow-ups (per spec rev 2):
- Update issue body Memory/skills → .claude/skills (CW-7)
- Update issue body validation language to match spec's softer 'optional, blocking when daemon up' (CW-8)
- Pepper's being-bootstrap-requirements.md line 154 should be updated to point at .claude/skills/ for the same reason"
```

- [ ] **Step 3: Phase 6 closes the umbrella for v1**

Per the spec: "Phase 4 (in spec terminology — slice 2.5 in plan terminology — was the second-being-hardening exit. We have not yet hatched a SECOND being. The full v1 close happens after Cynthia hatches Deb (Phase 3 of the spec) AND Stephanie's being hatches with no per-being scaffolding tweaks (Phase 4 of the spec)."

So this plan completes the IMPLEMENTATION work. The closing of issue #75 happens after the two real hatchings. Leave the issue OPEN through Cynthia's Deb hatch and the second-being hatch.

---

## Self-review

Spec coverage check (every section/requirement of the rev-2 spec mapped to a task):

| Spec section | Plan task |
|---|---|
| Problem | (covered by overall plan goal) |
| What we're building | Phase 2 onwards |
| Source material | Task 2.6 migrates templates-draft |
| Inputs (TUI + --config) | Tasks 2.2 (config schema), 5.4 (wizard), 5.5 (CLI wiring) |
| Outputs (vault + daemon fragments) | Tasks 2.7 (vault), 3.2 (fragments), 5.1-5.2 (channels) |
| Failure semantics (tracked rollback) | Task 2.7 `_rollback` + tests |
| Validation (load-bearing, parse, daemon check) | Tasks 2.7 (basic), 3.3 (extended), 6.2 (live daemon check) |
| Idempotency (refuse + --init-missing, no --force) | Task 2.7 (`VaultExistsError`, `init_missing` path) |
| Constraints (Pepper's 7 principles) | Carried via templates' content, no specific task |
| Out of scope (no --force, no Pepper migration, etc.) | Honored implicitly |
| Acceptance criteria | Task 6.1-6.3 verifies criteria 1-5; Task 6.4 verifies criterion 7 (boundary check by tearing down cleanly) |
| Phased delivery (5 slices) | Phases 2-5 map 1:1 to slices 2.2-2.5; Phase 1 = slice 2.1; Phase 6 = e2e |
| Architecture: standalone package + minimal core additions | Phase 1 (core), Phase 2+ (hatchery) |
| Package structure | Task 2.1 |
| Daemon integration: runner.py + scheduler.py | Tasks 1.3, 1.6 |
| File-class metadata | Tasks 2.3, 2.4 |
| Jinja2 substitution variables | Task 2.5 (renderer) |
| `_being_/` rename | Task 2.7 (`_rewrite_being_dir`) |
| Hatcher CLI flow (TUI + --config) | Tasks 2.8, 5.4, 5.5 |
| `--daemon-config-dir` flag (CW-6) | Tasks 2.8, 5.5 |
| Universal skills (3) | Tasks 4.5, 4.6, 4.7, 4.8 |
| Skills location at .claude/skills/ (CW-7) | Task 4.8 (`_copy_skills_tree` writes to `.claude/skills/`) |
| Elder-letter mechanism | Tasks 4.1-4.4 |
| HATCHING-REPORT format (N-8) | Task 5.3 |
| Channels (Discord, webcam, GitHub backup) | Tasks 5.1, 5.2 |
| from-her-creator EDITOR gate (CW-5) | Task 5.4 (`offer_letter_authoring`), Task 5.5 (CLI wiring) |
| Validation step #7 sharpened (CW-8) | Phase 6 (live daemon check); HATCHING-REPORT escalations in Task 5.3 |
| File-class semantics including `system` (CW-3) | Task 2.3 (manifest), Task 2.4 (loader includes SYSTEM enum) |
| USER.md/MEMORY.md as structural (CW-2) | Task 2.3 (manifest already correct) |
| github_backup opt-in (CW-4) | Tasks 5.1, 5.2 |
| Testing strategy (3 layers) | Layer 1 unit tests in each task; layer 2 `--config` integration in Task 2.8; layer 3 live e2e in Phase 6 |

**Placeholder scan:** searched for "TBD", "TODO", "FIXME" — none in the plan. All steps have actual code or commands.

**Type consistency:** `HatchConfig` is the type used throughout. `Hatcher`, `Renderer`, `FileClassManifest`, `DaemonConfigWriter`, `ResolvedLetter`, `SourceKind`, `FileClass`, `HatchResult` — all consistently spelled across tasks.

**One outstanding caveat:** Task 4.8's `_copy_skills_tree` writes to `<vault_root>/.claude/skills/`. Make sure the file-class manifest's `skill: ["skills/**/*"]` glob covers the SOURCE-side path (template dir), which it does. The DEST-side path is hard-coded in the renderer code; it's not classified by the manifest because manifest classification is only for templates.

**Verification:** the plan is implementable as written. The engineer needs to know Pepper's existing config shape (provided via referenced paths), and they need access to the agent_core repo's existing patterns (also referenced). No undefined types, no missing context.

---

## Open issues to surface during execution

- The spec's "Cross-platform deferral" (acceptance criterion #8) is honored implicitly. If during implementation any choice would block a future port to a non-Claude-Code harness, flag it in the discovery log instead of just doing it.
- The spec mentions `validate_daemon_fragments_parse` should validate against agent-core/core's PipelineConfig + JobDef + endpoint schemas. The plan ships only YAML-parse validation in v1; full Pydantic round-trip is captured as a v1.5+ enhancement in the discovery log.
- The wizard preview's file-count display is approximate in the wizard code (`templates count` from a simple glob); precise per-config counting is a polish item.





