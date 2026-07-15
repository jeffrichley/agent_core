# Spec: config hygiene / drift detection → extend daemon doctor (issue #321)

## Goal

Extend the `daemon doctor` command (added by C2-3 #317) with a config-hygiene pass: detect and (with `--fix`) prune `.bak`/`.pre-*`/`.cleanup` debris in the config dir and `endpoints.d/`, record the active venv path in the install stamp, and flag reserved-key config drift in fragments. Report-only by default; `--fix` removes debris files. Closes the `[P2]` drift-debris gap from the world-class eval (issue #321; see D4 in [`docs/superpowers/specs/2026-07-14-per-being-config-isolation-design.md`](docs/superpowers/specs/2026-07-14-per-being-config-isolation-design.md)).

## Acceptance criteria

- **`venv_path` in install stamp**: `InstallStamp` gains a `venv_path: str | None` field. `daemon install` writes the absolute path of the installed venv (e.g. `str(home / ".venv")`). `daemon status` shows the venv path from the stamp when present. `read_stamp` defaults `venv_path` to `None` for stamps written before this change (backward-compatible).
- **Debris detection**: `find_debris_files(config_dir)` returns all files in `config_dir/` and `config_dir/endpoints.d/` whose names match any of the `_DEBRIS_GLOBS` patterns (`*.yaml.bak`, `*.yaml.bak-*`, `*.yaml.pre-*`, `*.yaml.cleanup`, `*.yaml.cleanup-*`). The live examples `wren.yaml.bak-20260702-with-voice` and `testbeing.yaml.cleanup-2026-05-10` must be detected.
- **Fragment drift check**: `check_fragment_drift(config_dir)` reads each `endpoints.d/*.yaml` (skipping debris files), and returns a warning for any fragment containing reserved keys (`bus`, `http`, `bus_hooks`, `mcp_audit`) that belong only in the monolith. The live example `agent_core-cputest.yaml` (which is a monolith copy) must trigger a warning if it has a `bus:` key.
- **`doctor` command extended**: `agent-core daemon doctor [--fix]` (added by #317) runs the config hygiene pass. Without `--fix` it prints findings and exits 1 if any issues found. With `--fix` it also deletes debris files; schema drift is always report-only (manual intervention required).
- **`--fix` scope is limited to debris**: Schema drift warnings are printed but `--fix` does not auto-modify config files — the operator must resolve those manually.
- Unit tests in `packages/core/tests/test_daemon_config_hygiene.py` cover: debris glob patterns (both in root and endpoints.d/), clean directories returning empty lists, `check_fragment_drift` with and without reserved keys, YAML parse error handling, `run_config_hygiene` with `fix=False` does not delete files, `run_config_hygiene` with `fix=True` deletes debris.
- `packages/core/tests/test_daemon_install.py` updated: add round-trip test for `venv_path`, add backward-compat test that stamps without `venv_path` read as `None`.
- `packages/core/tests/test_daemon_cli.py` updated: add test that `doctor` prints a hygiene report (monkeypatched `run_config_hygiene`), add test that `install` writes `venv_path` to the stamp.
- `just check` passes (lint + full test suite with coverage).

## Approach

No GoF pattern applies. Guiding principle: **SRP** — `config_hygiene.py` is a pure-functions module (file system reads + Path manipulation); the CLI in `daemon/cli.py` is its only caller. **DIP** — `find_debris_files` and `check_fragment_drift` take an injected `config_dir: Path` so tests use `tmp_path` without touching the real filesystem.

**Dependency note**: This ticket is `blocked_by Cα-1` (the Pydantic daemon-config schema). The reserved-key drift check described here does not use the Pydantic model directly — it inspects dict keys after `yaml.safe_load` — so it is implementable with the current raw-dict approach that Cα-1 is replacing. This is intentional: the debris and reserved-key checks close the P2 gap without requiring the full Pydantic model. When Cα-1 lands, the drift check can be upgraded to call `model_validate()` for richer validation; that upgrade is explicitly out of scope here.

**`InstallStamp.venv_path`** (in `packages/core/src/agent_core/daemon/install.py`):
The `InstallStamp` frozen dataclass gains one optional field `venv_path: str | None`. The `daemon install` command already computes `venv = home / ".venv"` before calling `ensure_venv()`; it writes `venv_path=str(venv)` into the stamp. `read_stamp` uses `data.get("venv_path")` (None default) for backward compatibility. The `daemon status` command already shows stamp fields (lines 224–228 of `cli.py`); add `venv_path` to that output when present.

**`config_hygiene.py`** (new module at `packages/core/src/agent_core/daemon/config_hygiene.py`):
```python
_DEBRIS_GLOBS: list[str] = [
    "*.yaml.bak",
    "*.yaml.bak-*",
    "*.yaml.pre-*",
    "*.yaml.cleanup",
    "*.yaml.cleanup-*",
]

_FRAGMENT_RESERVED_KEYS: frozenset[str] = frozenset({"bus", "http", "bus_hooks", "mcp_audit"})

@dataclass
class HygieneReport:
    debris_found: list[Path] = field(default_factory=list)
    debris_removed: list[Path] = field(default_factory=list)
    drift_messages: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.debris_found or self.drift_messages)
```

`find_debris_files(config_dir)` globs both `config_dir/` and `config_dir/endpoints.d/` for each pattern in `_DEBRIS_GLOBS`; deduplicates via `set()`; returns `sorted()` for determinism.

`check_fragment_drift(config_dir)` globs `config_dir/endpoints.d/*.yaml`; skips debris files (intersects with `find_debris_files` result); calls `yaml.safe_load`; checks `set(raw.keys()) & _FRAGMENT_RESERVED_KEYS`; returns one message per fragment per violation. YAML parse errors produce their own message.

`run_config_hygiene(config_dir, *, fix)` calls both helpers, fills a `HygieneReport`, and if `fix=True` calls `path.unlink(missing_ok=True)` on each debris file.

**Doctor command extension**: `#317` adds `packages/core/src/agent_core/daemon/cli.py::doctor`. This ticket adds a call to `run_config_hygiene` inside that command body. The Worker must locate the `doctor` command function and extend it. If `#317` has not yet landed (the `doctor` command is absent), the Worker creates the command with just the config-hygiene section and a `# TODO #317: venv GC section goes here` comment at the top. The relevant import is:

```python
from agent_core.daemon.config_hygiene import run_config_hygiene
```

The doctor output section added by this ticket:
```
Config hygiene
  debris: ~/.agent-core/endpoints.d/wren.yaml.bak-20260702-with-voice
  → run with --fix to remove (or: removed 1 debris file)

Config drift
  ⚠ fragment 'agent_core-cputest.yaml': reserved key(s) ['bus'] belong in the
    monolith — silently ignored by the runner; move or remove this file
```

## Sub-requests (topologically sorted)

1. **Add `venv_path: str | None` to `InstallStamp`** in `packages/core/src/agent_core/daemon/install.py`:
   - Append `venv_path: str | None` to the `InstallStamp` dataclass (after `release_tag`).
   - In `read_stamp`: add `venv_path=data.get("venv_path")` to the `InstallStamp(...)` constructor call.

2. **Update `daemon install` to write `venv_path`** in `packages/core/src/agent_core/daemon/cli.py`:
   - In the `install` command body (around line 340 where `InstallStamp(...)` is constructed), add `venv_path=str(venv)` where `venv = home / ".venv"`.
   - In `status` command (around lines 224–228 where stamp fields are printed), add `console.print(f"venv path: {stamp.venv_path}")` when `stamp.venv_path` is not None.

3. **Create `packages/core/src/agent_core/daemon/config_hygiene.py`** — see Approach and File-level changes for the exact content.

4. **Extend the `doctor` command** in `packages/core/src/agent_core/daemon/cli.py`:
   - Import `run_config_hygiene` from `agent_core.daemon.config_hygiene`.
   - If `doctor` exists (from #317): locate the command function and add the config hygiene block (see File-level changes for the exact code to inject).
   - If `doctor` does not exist: create the command with just the config-hygiene block and a `# TODO #317: venv GC section goes here` comment.

5. **Create `packages/core/tests/test_daemon_config_hygiene.py`** — see File-level changes for exact content.

6. **Update `packages/core/tests/test_daemon_install.py`** — add `venv_path` round-trip and backward-compat tests.

7. **Update `packages/core/tests/test_daemon_cli.py`** — add tests for `install` stamp and `doctor` output.

## File-level changes

| File | Change |
|------|--------|
| `packages/core/src/agent_core/daemon/install.py` | **Modify** — add `venv_path: str \| None` field to `InstallStamp`; update `read_stamp` to parse it |
| `packages/core/src/agent_core/daemon/cli.py` | **Modify** — (a) write `venv_path` in `install` command; (b) print `venv_path` in `status`; (c) extend or create `doctor` command with config-hygiene section |
| `packages/core/src/agent_core/daemon/config_hygiene.py` | **New** — `_DEBRIS_GLOBS`, `_FRAGMENT_RESERVED_KEYS`, `HygieneReport`, `find_debris_files`, `check_fragment_drift`, `run_config_hygiene` |
| `packages/core/tests/test_daemon_config_hygiene.py` | **New** — unit tests for all functions in `config_hygiene.py` |
| `packages/core/tests/test_daemon_install.py` | **Modify** — two additional tests for `venv_path` |
| `packages/core/tests/test_daemon_cli.py` | **Modify** — two additional tests for `install` and `doctor` |

### Exact content: `packages/core/src/agent_core/daemon/config_hygiene.py`

```python
"""Config hygiene pass for `daemon doctor` — Cα-3, issue #321.

Detects and (with --fix) removes debris files in the daemon config dir and
endpoints.d/. Also flags reserved-key drift in endpoint fragments.

All functions take an injected config_dir Path so tests use tmp_path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Glob patterns identifying debris files produced by "mv aside" editing.
# Matched against files in config_dir/ and config_dir/endpoints.d/.
_DEBRIS_GLOBS: list[str] = [
    "*.yaml.bak",
    "*.yaml.bak-*",
    "*.yaml.pre-*",
    "*.yaml.cleanup",
    "*.yaml.cleanup-*",
]

# Keys that belong only in the monolith (agent_core.yaml), never in fragments.
# A fragment containing any of these is silently ignored by runner.py — which
# is confusing and constitutes config drift.
_FRAGMENT_RESERVED_KEYS: frozenset[str] = frozenset({"bus", "http", "bus_hooks", "mcp_audit"})


@dataclass
class HygieneReport:
    """Results of a single config hygiene pass."""

    debris_found: list[Path] = field(default_factory=list)
    """Debris files detected in this pass."""

    debris_removed: list[Path] = field(default_factory=list)
    """Debris files actually removed (populated only when fix=True)."""

    drift_messages: list[str] = field(default_factory=list)
    """Human-readable fragment drift warnings (always report-only)."""

    @property
    def has_issues(self) -> bool:
        """True if any debris or drift was found."""
        return bool(self.debris_found or self.drift_messages)


def find_debris_files(config_dir: Path) -> list[Path]:
    """Return debris files in config_dir/ and config_dir/endpoints.d/.

    A file is debris if its name matches any pattern in _DEBRIS_GLOBS.
    Results are sorted for determinism; duplicates are collapsed via set().
    """
    found: list[Path] = []
    search_dirs = [config_dir]
    endpoints_d = config_dir / "endpoints.d"
    if endpoints_d.is_dir():
        search_dirs.append(endpoints_d)
    for search_dir in search_dirs:
        for pattern in _DEBRIS_GLOBS:
            found.extend(search_dir.glob(pattern))
    return sorted(set(found))


def check_fragment_drift(config_dir: Path) -> list[str]:
    """Check endpoints.d/*.yaml fragments for reserved-key drift.

    Returns one warning string per violation found. An empty list means
    no drift detected. Debris files are excluded from the check (they are
    not parsed as fragments).
    """
    messages: list[str] = []
    endpoints_d = config_dir / "endpoints.d"
    if not endpoints_d.is_dir():
        return messages

    debris = set(find_debris_files(config_dir))

    for frag_path in sorted(endpoints_d.glob("*.yaml")):
        if frag_path in debris:
            continue
        try:
            raw = yaml.safe_load(frag_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            messages.append(f"fragment {frag_path.name!r}: YAML parse error — {exc}")
            continue
        if not isinstance(raw, dict):
            messages.append(
                f"fragment {frag_path.name!r}: expected a YAML mapping, "
                f"got {type(raw).__name__}"
            )
            continue
        reserved_present = sorted(set(raw.keys()) & _FRAGMENT_RESERVED_KEYS)
        if reserved_present:
            messages.append(
                f"fragment {frag_path.name!r}: reserved key(s) {reserved_present} "
                "belong in the monolith (agent_core.yaml), not in a fragment — "
                "these keys are silently ignored by the runner; move or remove this file"
            )
    return messages


def run_config_hygiene(config_dir: Path, *, fix: bool) -> HygieneReport:
    """Run the full config hygiene pass.

    If fix=True, debris files are removed. Fragment drift is always report-only
    — the operator must resolve schema drift manually.
    """
    report = HygieneReport()

    report.debris_found = find_debris_files(config_dir)
    if fix:
        for path in report.debris_found:
            path.unlink(missing_ok=True)
            report.debris_removed.append(path)

    report.drift_messages = check_fragment_drift(config_dir)

    return report
```

### Exact content: `packages/core/tests/test_daemon_config_hygiene.py`

```python
"""Unit tests for agent_core.daemon.config_hygiene (Cα-3, issue #321)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_core.daemon.config_hygiene import (
    HygieneReport,
    check_fragment_drift,
    find_debris_files,
    run_config_hygiene,
)


# ---------------------------------------------------------------------------
# find_debris_files
# ---------------------------------------------------------------------------

class TestFindDebrisFiles:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_debris_files(tmp_path) == []

    def test_detects_bak_in_root(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_dated_bak_suffix(self, tmp_path: Path) -> None:
        debris = tmp_path / "wren.yaml.bak-20260702-with-voice"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_cleanup_dated(self, tmp_path: Path) -> None:
        debris = tmp_path / "testbeing.yaml.cleanup-2026-05-10"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_pre_suffix(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.pre-voice"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_bare_bak(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_bare_cleanup(self, tmp_path: Path) -> None:
        debris = tmp_path / "something.yaml.cleanup"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_ignores_plain_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "agent_core.yaml").write_text("bus: {}")
        assert find_debris_files(tmp_path) == []

    def test_detects_debris_in_endpoints_d(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        debris = endpoints_d / "wren.yaml.bak-20260702-with-voice"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_debris_in_both_dirs(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        root_debris = tmp_path / "agent_core.yaml.bak"
        frag_debris = endpoints_d / "testbeing.yaml.cleanup-2026-05-10"
        root_debris.write_text("")
        frag_debris.write_text("")
        result = find_debris_files(tmp_path)
        assert root_debris in result
        assert frag_debris in result

    def test_no_endpoints_d_dir_still_works(self, tmp_path: Path) -> None:
        # endpoints.d doesn't exist — no error
        assert find_debris_files(tmp_path) == []

    def test_results_sorted(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        b = endpoints_d / "z.yaml.bak"
        a = endpoints_d / "a.yaml.bak"
        a.write_text("")
        b.write_text("")
        result = find_debris_files(tmp_path)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# check_fragment_drift
# ---------------------------------------------------------------------------

class TestCheckFragmentDrift:
    def _make_endpoints_d(self, config_dir: Path) -> Path:
        d = config_dir / "endpoints.d"
        d.mkdir()
        return d

    def test_no_endpoints_d_returns_empty(self, tmp_path: Path) -> None:
        assert check_fragment_drift(tmp_path) == []

    def test_clean_fragment_returns_no_warnings(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "wren.yaml").write_text(
            "endpoints:\n  - type: builtin.stub\n    name: wren-stub\n"
        )
        assert check_fragment_drift(tmp_path) == []

    def test_fragment_with_reserved_bus_key_is_flagged(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "agent_core-cputest.yaml").write_text(
            "bus:\n  storage_path: /tmp/bus.sqlite\nendpoints: []\n"
        )
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 1
        assert "agent_core-cputest.yaml" in messages[0]
        assert "bus" in messages[0]

    def test_all_four_reserved_keys_are_caught(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "bad.yaml").write_text(
            "bus: {}\nhttp: {}\nbus_hooks: {}\nmcp_audit: {}\nendpoints: []\n"
        )
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 1
        assert "bus" in messages[0]
        assert "http" in messages[0]
        assert "bus_hooks" in messages[0]
        assert "mcp_audit" in messages[0]

    def test_yaml_parse_error_produces_message(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "broken.yaml").write_text(": invalid: yaml: content [[\n")
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 1
        assert "broken.yaml" in messages[0]
        assert "parse error" in messages[0].lower()

    def test_debris_files_are_excluded_from_drift_check(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        # A debris file that happens to contain reserved keys must not trigger drift
        (endpoints_d / "old.yaml.bak-20260101").write_text("bus: {}\n")
        assert check_fragment_drift(tmp_path) == []

    def test_multiple_fragments_each_produce_own_message(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "alpha.yaml").write_text("bus: {}\nendpoints: []\n")
        (endpoints_d / "beta.yaml").write_text("http: {}\nendpoints: []\n")
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 2


# ---------------------------------------------------------------------------
# run_config_hygiene
# ---------------------------------------------------------------------------

class TestRunConfigHygiene:
    def test_no_fix_does_not_delete_debris(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        report = run_config_hygiene(tmp_path, fix=False)
        assert len(report.debris_found) == 1
        assert report.debris_removed == []
        assert debris.exists(), "fix=False must not delete anything"

    def test_fix_deletes_debris_files(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        report = run_config_hygiene(tmp_path, fix=True)
        assert len(report.debris_found) == 1
        assert len(report.debris_removed) == 1
        assert not debris.exists()

    def test_has_issues_false_when_clean(self, tmp_path: Path) -> None:
        report = run_config_hygiene(tmp_path, fix=False)
        assert not report.has_issues

    def test_has_issues_true_when_debris_found(self, tmp_path: Path) -> None:
        (tmp_path / "x.yaml.bak").write_text("")
        report = run_config_hygiene(tmp_path, fix=False)
        assert report.has_issues

    def test_has_issues_true_when_drift_found(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        (endpoints_d / "bad.yaml").write_text("bus: {}\nendpoints: []\n")
        report = run_config_hygiene(tmp_path, fix=False)
        assert report.has_issues
        assert report.drift_messages

    def test_drift_is_report_only_even_with_fix(self, tmp_path: Path) -> None:
        """--fix never auto-modifies YAML fragments (schema drift is manual-review-only)."""
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        bad = endpoints_d / "bad.yaml"
        bad.write_text("bus: {}\nendpoints: []\n")
        run_config_hygiene(tmp_path, fix=True)
        # The drifted fragment must still exist after --fix
        assert bad.exists()

    def test_fix_with_multiple_debris_removes_all(self, tmp_path: Path) -> None:
        files = [
            tmp_path / "a.yaml.bak",
            tmp_path / "b.yaml.bak-20260101",
            tmp_path / "c.yaml.cleanup-2026-05-10",
        ]
        for f in files:
            f.write_text("")
        report = run_config_hygiene(tmp_path, fix=True)
        assert len(report.debris_removed) == 3
        for f in files:
            assert not f.exists()
```

### Additions to `packages/core/tests/test_daemon_install.py`

Add after the existing `test_read_stamp_returns_none_when_missing_required_fields` test:

```python
def test_write_then_read_stamp_round_trips_venv_path(tmp_path: Path) -> None:
    stamp = InstallStamp(
        installed_at="2026-07-15T10:00:00Z",
        installed_sha="abc1234",
        installed_version="0.8.0",
        python_version="3.12",
        extra=None,
        release_tag="v0.8.0",
        venv_path="/home/user/.agent-core/.venv",
    )
    write_stamp(tmp_path, stamp)
    result = read_stamp(tmp_path)
    assert result is not None
    assert result.venv_path == "/home/user/.agent-core/.venv"


def test_read_stamp_defaults_venv_path_to_none_for_old_stamps(tmp_path: Path) -> None:
    """Stamps written before Cα-3 lack venv_path; read_stamp must default it to None."""
    import json
    (tmp_path / STAMP_FILENAME).write_text(
        json.dumps({
            "installed_at": "2026-07-14T12:00:00Z",
            "installed_sha": "aaa0001",
            "installed_version": "0.7.0",
            "python_version": "3.12",
            "extra": None,
            "release_tag": "v0.7.0",
        }) + "\n",
        encoding="utf-8",
    )
    stamp = read_stamp(tmp_path)
    assert stamp is not None
    assert stamp.venv_path is None
```

### Additions to `packages/core/tests/test_daemon_cli.py`

Add after the existing `install` and `doctor` tests:

```python
def test_install_writes_venv_path_to_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon install records venv_path = str(home / '.venv') in the install stamp."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    # Monkeypatch away GitHub and subprocess calls
    monkeypatch.setattr("agent_core.daemon.cli.resolve_version", lambda r, repo: "v0.8.0")
    monkeypatch.setattr("agent_core.daemon.cli.list_release_wheels", lambda t, repo: [("core.whl", "url")])
    monkeypatch.setattr("agent_core.daemon.cli.download_wheels", lambda a, dest: [tmp_path / "core.whl"])
    monkeypatch.setattr("agent_core.daemon.cli.download_requirements", lambda t, repo, dest: tmp_path / "requirements.txt")
    monkeypatch.setattr("agent_core.daemon.cli.ensure_venv", lambda venv, python_version: None)
    monkeypatch.setattr("agent_core.daemon.cli.install_requirements", lambda req, venv_python: None)
    monkeypatch.setattr("agent_core.daemon.cli.install_wheels", lambda wheels, venv_python: None)
    monkeypatch.setattr("agent_core.daemon.cli._git_sha_of_tag", lambda tag: "abc1234")

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 0, result.stdout

    from agent_core.daemon.install import read_stamp
    stamp = read_stamp(tmp_path)
    assert stamp is not None
    assert stamp.venv_path == str(tmp_path / ".venv")


def test_doctor_reports_config_hygiene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor runs the config hygiene pass and reports findings."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.config_hygiene import HygieneReport

    fake_report = HygieneReport(
        debris_found=[tmp_path / "agent_core.yaml.bak"],
        debris_removed=[],
        drift_messages=["fragment 'bad.yaml': reserved key(s) ['bus']"],
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: fake_report,
    )
    # Also stub venv GC if #317's doctor adds it (noop if absent)

    result = runner.invoke(daemon_app, ["doctor"])
    # doctor exits 1 when issues are found
    assert result.exit_code == 1
    assert "debris" in result.stdout.lower() or "agent_core.yaml.bak" in result.stdout
    assert "drift" in result.stdout.lower() or "bad.yaml" in result.stdout
```

## Alternatives considered

1. **Separate `daemon config-hygiene` command instead of extending doctor**: Would give the hygiene pass its own namespace and avoid coupling to #317. Ruled out: D4 explicitly says "folds into the existing doctor — one command for venv + config hygiene." One entry point for operators is better UX than two.

2. **Pattern-match on the full name rather than using glob suffixes**: E.g. regex `r'\.yaml\.(bak|pre-|cleanup).*$'` applied to `Path.name`. More flexible but harder to read and test. Ruled out: `Path.glob()` is idiomatic and the examples all fit the `*.yaml.bak*` family cleanly. Glob patterns also telegraph intent at a glance.

3. **Use Cα-1's Pydantic model for schema drift instead of reserved-key dict check**: Would give richer error messages (full model validation, not just key enumeration). Ruled out for this ticket: Cα-1's exact Pydantic class name and module path aren't known at spec time; the reserved-key check closes the P2 gap without that dependency, and the upgrade is additive (Cα-1 can replace the check in-place once its model is settled).

4. **Auto-quarantine drifted fragments with `--fix` instead of report-only**: Move (not delete) fragments with reserved keys to a `_quarantine/` dir when `--fix` is given. Ruled out: too destructive for a P2 polish item; the fragments are currently silently tolerated by the runner and the operator may have intentional reasons. Report-only respects operator intent.

## Open questions

1. **Doctor command shape from #317**: The spec assumes `#317`'s `doctor` command will add a `--fix: bool` flag and follow the same `_INSTANCE_OPTION` pattern as the other daemon commands. If `#317`'s implementation differs, the Worker should adapt the integration accordingly and not introduce a second `--fix` flag.

2. **Cα-1 Pydantic model upgrade**: Once Cα-1 (Pydantic daemon-config schema) lands, the drift check can be upgraded to call `DaemonFragmentConfig.model_validate(raw)` for richer per-field validation. The Worker should NOT implement that upgrade here — defer to a Cα-1 follow-up or Cα-4 ticket.

## Out of scope

- Venv GC (superseded versioned venv pruning, broken junction detection) — that is `#317`'s responsibility, not this ticket.
- Schema-based drift using Cα-1's Pydantic model — reserved-key dict check is sufficient for this P2 item; full model validation is a post-Cα-1 upgrade.
- Detecting `agent_core-cputest.yaml` (a YAML that doesn't match a debris glob but contains monolith keys) via `--fix` auto-removal — it IS caught by `check_fragment_drift` as drift, but `--fix` deliberately does not auto-delete fragments; operator reviews and removes manually.
- Migration of Pepper's inline endpoints to `endpoints.d/` — Cα-2's responsibility.
- Lint/reporting for the monolith (`agent_core.yaml`) shape — the monolith schema check (does it match the expected Pydantic shape?) is Cα-1's gate (real `validate_config`), not the doctor's job.
