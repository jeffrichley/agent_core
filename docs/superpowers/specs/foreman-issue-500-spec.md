# Spec: daemon doctor venv GC report engine + detectors (issue #500)

## Goal

Add the venv-GC report section to `agent-core daemon doctor`: implement the `VenvGcReport` dataclass and five detector functions in a new pure-functions module `venv_gc.py`, wire them into the existing `doctor` command (replacing the `# TODO #317: venv GC section goes here` comment), and verify every detector category against a `tmp_path` fixture layout. C2-3a is **report-only**: the `--fix` flag does not yet trigger pruning for the venv GC section (that is C2-3b). Issue: https://github.com/jeffrichley/agent_core/issues/500. Design authority: `docs/superpowers/specs/2026-07-14-interpreter-venv-resolution-design.md` (M3).

## Acceptance criteria

- `packages/core/src/agent_core/daemon/venv_gc.py` exists with `VenvGcReport`, `discover_being_homes`, `current_stable_target`, `find_superseded_venvs`, `find_broken_stable_link`, `find_orphaned_partial_builds`, `find_drifted_mcp_json`, `find_dead_central_corpses`, and `run_venv_doctor`.
- `discover_being_homes(home_root)` returns only hidden dirs under `home_root` that have a `.agent-core/venvs/` subdirectory; it naturally excludes daemon instance homes (`~/.agent-core/`, `~/.agent-core-source/`, `~/.agent-core-test/`) because those have `venvs/` at the top level, not nested under `.agent-core/`.
- `find_superseded_venvs(venvs_dir, stable)` returns all versioned dirs in `venvs_dir` except the current (the dir that `stable` symlink/junction points to) and the N-1 (the next most recent by version number). Returns `[]` when `venvs_dir` is absent or has ≤2 dirs.
- `find_broken_stable_link(stable)` returns `stable` when `stable` is a symlink or junction whose resolved target does not exist; returns `None` otherwise.
- `find_orphaned_partial_builds(venvs_dir)` returns all versioned dirs in `venvs_dir` that lack a valid Python binary (`python_in_venv(d)` from `agent_core.venv.builder`).
- `find_drifted_mcp_json(being_name, *, vault_root, daemon_config_dir)` delegates to `mcp_json_needs_repair` from `agent_core.venv.mcp_config`; returns `vault_root / ".mcp.json"` when repair is needed, `None` otherwise.
- `find_dead_central_corpses(daemon_home)` returns all entries in `daemon_home` whose names match `.venv-v*` (old versioned-venv naming scheme), plus `daemon_home / ".venv"` if it is a plain directory (not a symlink or junction — the 0.6.1-style real-dir corpse).
- `run_venv_doctor(daemon_home, *, home_root, daemon_config_dir)` aggregates all five detectors across the daemon home and all discovered being homes, and returns a populated `VenvGcReport`.
- The `doctor` command in `packages/core/src/agent_core/daemon/cli.py` has its `# TODO #317: venv GC section goes here` comment replaced with a call to `run_venv_doctor` and prints a "Venv GC" section with findings grouped by category.
- The doctor command exits 1 if either the config hygiene report OR the venv GC report `has_issues`. The `--fix` flag is **ignored** by the venv GC section (mutations are C2-3b).
- `VenvGcReport.has_issues` returns `True` iff any of its five lists is non-empty.
- Unit tests in `packages/core/tests/test_daemon_venv_gc.py` cover each detector function using `tmp_path` fixture layouts; no real filesystem beyond `tmp_path` is accessed.
- `packages/core/tests/test_daemon_cli.py` gains one test: `doctor` command with a monkeypatched `run_venv_doctor` returning a report with findings prints those findings and exits 1.
- `just check` passes (lint + full test suite with coverage).

## Approach

No GoF pattern applies. Guiding principle: **SRP** — `venv_gc.py` is a pure-functions module (filesystem reads, `Path` manipulation, delegation to `mcp_config.mcp_json_needs_repair`); the CLI in `daemon/cli.py` is its only caller. **DIP** — all functions accept injected `Path` arguments so tests use `tmp_path` without touching the real filesystem, mirroring the exact pattern in `config_hygiene.py` (issue #321).

**Module placement**: `packages/core/src/agent_core/daemon/venv_gc.py` follows the precedent of `config_hygiene.py` — a new module inside the `daemon` package for doctor-related pure functions. It imports from `agent_core.venv.builder` (`python_in_venv`) and `agent_core.venv.mcp_config` (`mcp_json_needs_repair`); these are safe imports (no circular deps: `venv.*` does not import from `daemon.*`).

**Being discovery**: `discover_being_homes(home_root)` scans `home_root` for hidden subdirectories (name starts with `.`) whose `.agent-core/venvs/` subdirectory exists. This criterion is specific to the per-being layout introduced by C2-1 (#315); daemon instance homes (`~/.agent-core/`) have `venvs/` at the top level and are not matched.

**Superseded-venv detection**: sort all versioned dirs in `venvs_dir` by parsed version tuple DESC. The "current" dir is the resolved target of the stable symlink/junction (via `current_stable_target`). Keep the current dir + the next sorted dir that isn't current (N-1). When current is absent or not in the list (broken stable link), keep the top-2 by version. Everything else is superseded.

**Version sorting**: `_version_sort_key(name: str) → tuple[int, ...]` splits on `.` and converts to ints. Non-numeric components fall back to `(0,)`. This handles `0.7.0`, `0.8.0` correctly without importing `packaging`.

**Dead central corpses**: `find_dead_central_corpses(daemon_home)` globs for `.venv-v*` entries (any filesystem entry — file, dir, or junction — whose name starts with `.venv-v`) in `daemon_home`. It additionally includes `daemon_home / ".venv"` itself if it is a plain directory (`.is_dir()` but not `.is_symlink()` and not `.is_junction()`) — the pre-C2-1 state where the daemon venv was a real directory at `~/.agent-core/.venv`.

**CLI integration**: The existing `doctor` command (lines 398–436, `packages/core/src/agent_core/daemon/cli.py`) has the `# TODO #317` comment on line 406. This ticket removes that comment and adds a "Venv GC" section before the existing "Config hygiene" section (or after — Worker's discretion on ordering, but the `has_issues` exit-code check at the bottom must cover both reports). The import `from agent_core.daemon.venv_gc import run_venv_doctor` is added at the top of `cli.py` alongside `run_config_hygiene`.

**Report-only constraint**: even with `--fix`, the `run_venv_doctor` call returns a report but **nothing is deleted or modified**. The `--fix` flag is passed through unchanged to `run_config_hygiene` for the existing hygiene section. The venv GC section simply ignores `fix` for C2-3a.

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/daemon/venv_gc.py`** with the following public symbols (see File-level changes for exact content):
   - `VenvGcReport` dataclass (5 `list[Path]` fields + `has_issues` property)
   - `_version_sort_key(name: str) → tuple[int, ...]`
   - `discover_being_homes(home_root: Path) → list[Path]`
   - `current_stable_target(stable: Path) → Path | None`
   - `find_superseded_venvs(venvs_dir: Path, stable: Path) → list[Path]`
   - `find_broken_stable_link(stable: Path) → Path | None`
   - `find_orphaned_partial_builds(venvs_dir: Path) → list[Path]`
   - `find_drifted_mcp_json(being_name: str, *, vault_root: Path, daemon_config_dir: Path) → Path | None`
   - `find_dead_central_corpses(daemon_home: Path) → list[Path]`
   - `run_venv_doctor(daemon_home: Path, *, home_root: Path, daemon_config_dir: Path) → VenvGcReport`

2. **Modify `packages/core/src/agent_core/daemon/cli.py`**:
   - Add `from agent_core.daemon.venv_gc import run_venv_doctor` alongside the existing `from agent_core.daemon.config_hygiene import run_config_hygiene` import (around line 36).
   - Add `from pathlib import Path` if not already present at the top-level (it already is, at line 27).
   - In the `doctor` command body, remove the `# TODO #317: venv GC section goes here` comment (line 406) and add the venv GC section described in File-level changes.
   - Update the exit-code guard at the bottom of `doctor` to `if report.has_issues or venv_report.has_issues:`.

3. **Create `packages/core/tests/test_daemon_venv_gc.py`** — unit tests for all functions in `venv_gc.py`. See File-level changes for exact content.

4. **Add one test to `packages/core/tests/test_daemon_cli.py`** — `test_doctor_reports_venv_gc_section` — monkeypatching `run_venv_doctor` to return a report with findings and asserting the command output contains venv GC findings and exits 1.

## File-level changes

| File | Change |
|------|--------|
| `packages/core/src/agent_core/daemon/venv_gc.py` | **New** — `VenvGcReport`, all detector functions, `run_venv_doctor` orchestrator |
| `packages/core/src/agent_core/daemon/cli.py` | **Modify** — add `run_venv_doctor` import; remove `# TODO #317` comment; add venv GC doctor section; update `has_issues` guard |
| `packages/core/tests/test_daemon_venv_gc.py` | **New** — unit tests for `venv_gc.py` using `tmp_path` fixture layouts |
| `packages/core/tests/test_daemon_cli.py` | **Modify** — add one test: `test_doctor_reports_venv_gc_section` |

### Exact content: `packages/core/src/agent_core/daemon/venv_gc.py`

```python
"""Venv GC / doctor detectors for `daemon doctor` — C2-3a, issue #500.

Detects (but does not prune) venv lifecycle debris:
- superseded versioned venvs (keep current + N-1);
- broken junctions/symlinks (target missing);
- orphaned partial build dirs (no Python binary);
- drifted .mcp.json (re-runs M2's repair check);
- dead central corpses in the daemon home (.venv-v* old naming scheme).

All functions take injected Path arguments so tests use tmp_path without
touching the real filesystem. Pruning (--fix) is C2-3b.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VenvGcReport:
    """Accumulated findings from a venv doctor pass. Report-only — no mutations."""

    superseded_venvs: list[Path] = field(default_factory=list)
    """Versioned venv dirs that are older than current + N-1 and can be pruned."""

    broken_stable_links: list[Path] = field(default_factory=list)
    """Stable .venv paths that are symlinks/junctions pointing to a non-existent target."""

    orphaned_partial_builds: list[Path] = field(default_factory=list)
    """Versioned venv dirs that exist but have no Python binary (failed builds)."""

    drifted_mcp_jsons: list[Path] = field(default_factory=list)
    """.mcp.json paths that are missing or do not match the canonical shape."""

    dead_central_corpses: list[Path] = field(default_factory=list)
    """Old .venv-v* dirs (and .venv plain dirs) in the daemon home from before the migration."""

    @property
    def has_issues(self) -> bool:
        """True if any detector found at least one issue."""
        return bool(
            self.superseded_venvs
            or self.broken_stable_links
            or self.orphaned_partial_builds
            or self.drifted_mcp_jsons
            or self.dead_central_corpses
        )


def _version_sort_key(name: str) -> tuple[int, ...]:
    """Parse a version directory name (e.g. '0.8.0') into a tuple of ints for sorting.

    Non-numeric components fall back to (0,) so unknown names sort last.
    """
    try:
        return tuple(int(x) for x in name.split("."))
    except ValueError:
        return (0,)


def discover_being_homes(home_root: Path) -> list[Path]:
    """Return being home directories discovered under home_root.

    A being home is any hidden directory (name starts with '.') in home_root
    that contains a '.agent-core/venvs/' subdirectory. This criterion is
    specific to the per-being layout from C2-1 and naturally excludes daemon
    instance homes (~/.agent-core/, ~/.agent-core-source/, etc.) whose
    versioned venvs live at <home>/venvs/ (top level, no .agent-core nesting).

    Results are sorted by name for determinism.
    """
    if not home_root.is_dir():
        return []
    results: list[Path] = []
    for entry in sorted(home_root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("."):
            continue
        if (entry / ".agent-core" / "venvs").is_dir():
            results.append(entry)
    return results


def current_stable_target(stable: Path) -> Path | None:
    """Return the resolved target of the stable symlink/junction, or None.

    Returns None if stable does not exist, is not a symlink/junction, or
    if os.readlink raises (e.g. permissions error on Windows).
    """
    is_link = stable.is_symlink() or (sys.platform == "win32" and stable.is_junction())
    if not is_link:
        return None
    try:
        target = Path(os.readlink(stable))
    except OSError:
        return None
    if not target.is_absolute():
        target = stable.parent / target
    return target.resolve()


def find_superseded_venvs(venvs_dir: Path, stable: Path) -> list[Path]:
    """Return versioned venv dirs in venvs_dir that are older than current + N-1.

    Algorithm:
    1. Collect all subdirectories of venvs_dir; sort DESC by _version_sort_key.
    2. The 'current' dir is the one whose resolved path equals current_stable_target(stable).
    3. Keep: current + the next dir in sorted order that isn't current (N-1 for rollback).
    4. If current is absent from the list (broken stable), keep top-2 by version.
    5. Return sorted list of dirs not in the keep set.

    Returns [] if venvs_dir is absent or the total dir count is ≤2 (nothing to prune).
    """
    if not venvs_dir.is_dir():
        return []
    all_dirs = sorted(
        (d for d in venvs_dir.iterdir() if d.is_dir()),
        key=lambda d: _version_sort_key(d.name),
        reverse=True,  # newest first
    )
    if len(all_dirs) <= 2:
        return []

    current_target = current_stable_target(stable)

    # Identify current dir by resolved path comparison.
    current_dir: Path | None = None
    if current_target is not None:
        for d in all_dirs:
            if d.resolve() == current_target:
                current_dir = d
                break

    # Build keep set: current + N-1.
    keep: list[Path] = []
    if current_dir is not None:
        keep.append(current_dir)
    for d in all_dirs:
        if d not in keep:
            keep.append(d)
            break  # one additional (N-1)

    # Fallback: if keep is still empty, keep top-2.
    if not keep:
        keep = list(all_dirs[:2])

    keep_set = set(keep)
    return sorted(d for d in all_dirs if d not in keep_set)


def find_broken_stable_link(stable: Path) -> Path | None:
    """Return stable if it is a symlink/junction pointing to a non-existent target.

    Returns None if stable does not exist, is a regular directory, or if the
    target exists (i.e. the link is healthy).
    """
    is_link = stable.is_symlink() or (sys.platform == "win32" and stable.is_junction())
    if not is_link:
        return None
    # stable.exists() follows the link; False means target missing.
    if not stable.exists():
        return stable
    return None


def find_orphaned_partial_builds(venvs_dir: Path) -> list[Path]:
    """Return versioned dirs in venvs_dir that have no valid Python binary.

    A partial build is a directory that was created by the venv builder but
    the install or verify step failed before completion. The Python binary
    path is determined by agent_core.venv.builder.python_in_venv — same
    logic used by create_venv's idempotency check.
    """
    from agent_core.venv.builder import python_in_venv

    if not venvs_dir.is_dir():
        return []
    orphans: list[Path] = []
    for d in sorted(venvs_dir.iterdir()):
        if not d.is_dir():
            continue
        if not python_in_venv(d).exists():
            orphans.append(d)
    return orphans


def find_drifted_mcp_json(
    being_name: str,
    *,
    vault_root: Path,
    daemon_config_dir: Path,
) -> Path | None:
    """Return the .mcp.json path if it is missing or does not match the canonical shape.

    Delegates to mcp_json_needs_repair from agent_core.venv.mcp_config (C2-2
    / #316). Returns None when the file is already canonical.
    """
    from agent_core.venv.mcp_config import mcp_json_needs_repair

    needs_repair = mcp_json_needs_repair(
        being_name,
        vault_root=vault_root,
        daemon_config_dir=daemon_config_dir,
    )
    return (vault_root / ".mcp.json") if needs_repair else None


def find_dead_central_corpses(daemon_home: Path) -> list[Path]:
    """Return old-style venv entries in daemon_home from before the C2-1 migration.

    Two corpse types:
    1. .venv-v* entries (any filesystem type) — the pre-migration versioned
       naming scheme (e.g. ~/.agent-core/.venv-v0.7.0).
    2. daemon_home/.venv if it is a plain directory (not a symlink or junction)
       — the 0.6.1-era shared venv that was a real directory, not yet migrated
       to the stable-junction layout.

    Results are sorted for determinism.
    """
    if not daemon_home.is_dir():
        return []
    found: list[Path] = []

    # Pattern 1: .venv-v* entries.
    for entry in daemon_home.iterdir():
        if entry.name.startswith(".venv-v"):
            found.append(entry)

    # Pattern 2: .venv as a plain real directory (0.6.1-era corpse).
    plain_venv = daemon_home / ".venv"
    if (
        plain_venv.exists()
        and plain_venv.is_dir()
        and not plain_venv.is_symlink()
        and not (sys.platform == "win32" and plain_venv.is_junction())
    ):
        found.append(plain_venv)

    return sorted(found)


def run_venv_doctor(
    daemon_home: Path,
    *,
    home_root: Path,
    daemon_config_dir: Path,
) -> VenvGcReport:
    """Run all venv GC detectors and return a VenvGcReport.

    Scans:
    - The daemon home: dead central corpses, daemon versioned venvs, daemon
      stable link, daemon orphaned partial builds.
    - Every discovered being home under home_root: being versioned venvs,
      being stable links, being orphaned partial builds, being .mcp.json drift.

    Args:
        daemon_home: The daemon instance home (e.g. ~/.agent-core/).
        home_root: The user's home directory (e.g. Path.home()), used to
            discover being homes.
        daemon_config_dir: The daemon config directory passed to
            find_drifted_mcp_json for .mcp.json comparison.
    """
    report = VenvGcReport()

    # --- Daemon-specific checks ---
    report.dead_central_corpses = find_dead_central_corpses(daemon_home)

    daemon_venvs_dir = daemon_home / "venvs"
    daemon_stable = daemon_home / ".venv"

    report.superseded_venvs.extend(
        find_superseded_venvs(daemon_venvs_dir, daemon_stable)
    )
    broken = find_broken_stable_link(daemon_stable)
    if broken is not None:
        report.broken_stable_links.append(broken)
    report.orphaned_partial_builds.extend(
        find_orphaned_partial_builds(daemon_venvs_dir)
    )

    # --- Being-specific checks ---
    for being_home in discover_being_homes(home_root):
        being_name = being_home.name.lstrip(".")
        being_venvs_dir = being_home / ".agent-core" / "venvs"
        being_stable = being_home / ".venv"

        report.superseded_venvs.extend(
            find_superseded_venvs(being_venvs_dir, being_stable)
        )
        broken = find_broken_stable_link(being_stable)
        if broken is not None:
            report.broken_stable_links.append(broken)
        report.orphaned_partial_builds.extend(
            find_orphaned_partial_builds(being_venvs_dir)
        )
        drifted = find_drifted_mcp_json(
            being_name,
            vault_root=being_home,
            daemon_config_dir=daemon_config_dir,
        )
        if drifted is not None:
            report.drifted_mcp_jsons.append(drifted)

    return report
```

### Modification to `packages/core/src/agent_core/daemon/cli.py`

**Import addition** (alongside existing `run_config_hygiene` import, around line 36):
```python
from agent_core.daemon.venv_gc import run_venv_doctor
```

**`doctor` command body**: remove `# TODO #317: venv GC section goes here` (line 406) and replace with the venv GC section. Final `doctor` function body:

```python
@app.command()
def doctor(
    instance: str | None = _INSTANCE_OPTION,
    fix: bool = typer.Option(
        False, "--fix", help="Remove detected debris files (drift warnings are always report-only)."
    ),
) -> None:
    """Check config hygiene and venv health: detect debris, stale venvs, drifted .mcp.json."""
    inst = _resolve(instance)
    home = home_for(inst)

    # Venv GC section (C2-3a: report-only, --fix is not yet wired here)
    venv_report = run_venv_doctor(
        daemon_home=home,
        home_root=Path.home(),
        daemon_config_dir=home,
    )

    console.print("\nVenv GC")
    if venv_report.dead_central_corpses:
        for path in venv_report.dead_central_corpses:
            console.print(f"  dead corpse: {path}")
        console.print("  → run with --fix (C2-3b) to remove")
    if venv_report.superseded_venvs:
        for path in venv_report.superseded_venvs:
            console.print(f"  superseded venv: {path}")
        console.print("  → run with --fix (C2-3b) to prune (keeps current + N-1)")
    if venv_report.broken_stable_links:
        for path in venv_report.broken_stable_links:
            console.print(f"  broken stable link: {path}")
    if venv_report.orphaned_partial_builds:
        for path in venv_report.orphaned_partial_builds:
            console.print(f"  orphaned partial build: {path}")
        console.print("  → run with --fix (C2-3b) to remove")
    if venv_report.drifted_mcp_jsons:
        for path in venv_report.drifted_mcp_jsons:
            console.print(f"  drifted .mcp.json: {path}")
        console.print("  → run [bold]agent-core venv regen-mcp <being>[/bold] to repair")
    if not venv_report.has_issues:
        console.print("  no venv issues found")

    # Config hygiene section (existing, from #321)
    report = run_config_hygiene(home, fix=fix)

    console.print("\nConfig hygiene")
    if report.debris_found:
        for path in report.debris_found:
            if path in report.debris_removed:
                console.print(f"  debris (removed): {path}")
            else:
                console.print(f"  debris: {path}")
        if not fix:
            console.print("  → run with --fix to remove debris files")
        elif report.debris_removed:
            console.print(f"  → removed {len(report.debris_removed)} debris file(s)")
    else:
        console.print("  no debris found")

    console.print("\nConfig drift")
    if report.drift_messages:
        for msg in report.drift_messages:
            console.print(f"  ⚠ {msg}")
    else:
        console.print("  no drift detected")

    if report.has_issues or venv_report.has_issues:
        raise typer.Exit(code=1)
```

### Exact content: `packages/core/tests/test_daemon_venv_gc.py`

```python
"""Unit tests for agent_core.daemon.venv_gc (C2-3a, issue #500)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_core.daemon.venv_gc import (
    VenvGcReport,
    _version_sort_key,
    current_stable_target,
    discover_being_homes,
    find_broken_stable_link,
    find_dead_central_corpses,
    find_drifted_mcp_json,
    find_orphaned_partial_builds,
    find_superseded_venvs,
    run_venv_doctor,
)


# ---------------------------------------------------------------------------
# _version_sort_key
# ---------------------------------------------------------------------------

class TestVersionSortKey:
    def test_simple_semver(self) -> None:
        assert _version_sort_key("0.8.0") == (0, 8, 0)

    def test_different_major(self) -> None:
        assert _version_sort_key("1.0.0") > _version_sort_key("0.9.9")

    def test_non_numeric_returns_zero_tuple(self) -> None:
        assert _version_sort_key("abc") == (0,)

    def test_sorts_correctly_descending(self) -> None:
        names = ["0.6.1", "0.8.0", "0.7.0"]
        sorted_names = sorted(names, key=_version_sort_key, reverse=True)
        assert sorted_names == ["0.8.0", "0.7.0", "0.6.1"]


# ---------------------------------------------------------------------------
# discover_being_homes
# ---------------------------------------------------------------------------

class TestDiscoverBeingHomes:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert discover_being_homes(tmp_path) == []

    def test_finds_being_with_agent_core_venvs(self, tmp_path: Path) -> None:
        being_home = tmp_path / ".wren"
        (being_home / ".agent-core" / "venvs").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == [being_home]

    def test_ignores_non_hidden_dirs(self, tmp_path: Path) -> None:
        visible = tmp_path / "wren"
        (visible / ".agent-core" / "venvs").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == []

    def test_ignores_daemon_home_pattern(self, tmp_path: Path) -> None:
        # ~/.agent-core/ has venvs/ at top level, not .agent-core/venvs/
        daemon_home = tmp_path / ".agent-core"
        (daemon_home / "venvs").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == []

    def test_discovers_multiple_beings(self, tmp_path: Path) -> None:
        for being in ["pepper", "wren"]:
            (tmp_path / f".{being}" / ".agent-core" / "venvs").mkdir(parents=True)
        result = discover_being_homes(tmp_path)
        assert len(result) == 2
        assert result == sorted(result)  # deterministically sorted

    def test_home_root_does_not_exist_returns_empty(self, tmp_path: Path) -> None:
        assert discover_being_homes(tmp_path / "nonexistent") == []

    def test_ignores_dirs_without_venvs_subdir(self, tmp_path: Path) -> None:
        # Has .agent-core but no venvs/ inside it
        (tmp_path / ".wren" / ".agent-core").mkdir(parents=True)
        assert discover_being_homes(tmp_path) == []


# ---------------------------------------------------------------------------
# current_stable_target
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
class TestCurrentStableTargetPosix:
    def test_returns_target_for_healthy_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "venvs" / "0.8.0"
        target.mkdir(parents=True)
        stable = tmp_path / ".venv"
        os.symlink(target, stable)
        assert current_stable_target(stable) == target.resolve()

    def test_returns_none_for_plain_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / ".venv"
        plain.mkdir()
        assert current_stable_target(plain) is None

    def test_returns_none_when_stable_absent(self, tmp_path: Path) -> None:
        assert current_stable_target(tmp_path / ".venv") is None

    def test_returns_target_for_broken_symlink(self, tmp_path: Path) -> None:
        stable = tmp_path / ".venv"
        os.symlink(tmp_path / "nonexistent", stable)
        result = current_stable_target(stable)
        # Returns the (non-existent) resolved target, not None
        assert result is not None


# ---------------------------------------------------------------------------
# find_superseded_venvs
# ---------------------------------------------------------------------------

class TestFindSupersededVenvs:
    def _make_venvs_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "venvs"
        d.mkdir()
        return d

    def test_empty_venvs_dir_returns_empty(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        stable = tmp_path / ".venv"
        assert find_superseded_venvs(venvs_dir, stable) == []

    def test_one_version_returns_empty(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        (venvs_dir / "0.8.0").mkdir()
        stable = tmp_path / ".venv"
        assert find_superseded_venvs(venvs_dir, stable) == []

    def test_two_versions_returns_empty(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        (venvs_dir / "0.7.0").mkdir()
        (venvs_dir / "0.8.0").mkdir()
        stable = tmp_path / ".venv"
        assert find_superseded_venvs(venvs_dir, stable) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_three_versions_prunes_oldest(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        v060 = venvs_dir / "0.6.1"
        v070 = venvs_dir / "0.7.0"
        v080 = venvs_dir / "0.8.0"
        v060.mkdir()
        v070.mkdir()
        v080.mkdir()
        stable = tmp_path / ".venv"
        os.symlink(v080, stable)

        result = find_superseded_venvs(venvs_dir, stable)
        assert result == [v060]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_keeps_current_and_n_minus_1(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        v1 = venvs_dir / "0.6.0"
        v2 = venvs_dir / "0.7.0"
        v3 = venvs_dir / "0.8.0"
        v4 = venvs_dir / "0.9.0"
        for d in (v1, v2, v3, v4):
            d.mkdir()
        stable = tmp_path / ".venv"
        os.symlink(v4, stable)

        result = find_superseded_venvs(venvs_dir, stable)
        assert v1 in result
        assert v2 in result
        assert v3 not in result  # N-1
        assert v4 not in result  # current

    def test_absent_venvs_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_superseded_venvs(tmp_path / "venvs", tmp_path / ".venv") == []

    def test_broken_stable_link_keeps_top_two(self, tmp_path: Path) -> None:
        venvs_dir = self._make_venvs_dir(tmp_path)
        v060 = venvs_dir / "0.6.0"
        v070 = venvs_dir / "0.7.0"
        v080 = venvs_dir / "0.8.0"
        v060.mkdir()
        v070.mkdir()
        v080.mkdir()
        # stable doesn't exist — no current can be determined
        stable = tmp_path / ".venv"
        result = find_superseded_venvs(venvs_dir, stable)
        assert v060 in result
        assert v070 not in result
        assert v080 not in result


# ---------------------------------------------------------------------------
# find_broken_stable_link
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
class TestFindBrokenStableLinkPosix:
    def test_returns_path_for_dangling_symlink(self, tmp_path: Path) -> None:
        stable = tmp_path / ".venv"
        os.symlink(tmp_path / "nonexistent", stable)
        assert find_broken_stable_link(stable) == stable

    def test_returns_none_for_healthy_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "venvs" / "0.8.0"
        target.mkdir(parents=True)
        stable = tmp_path / ".venv"
        os.symlink(target, stable)
        assert find_broken_stable_link(stable) is None

    def test_returns_none_for_plain_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / ".venv"
        plain.mkdir()
        assert find_broken_stable_link(plain) is None

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert find_broken_stable_link(tmp_path / ".venv") is None


# ---------------------------------------------------------------------------
# find_orphaned_partial_builds
# ---------------------------------------------------------------------------

class TestFindOrphanedPartialBuilds:
    def test_absent_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_orphaned_partial_builds(tmp_path / "venvs") == []

    def test_complete_venv_is_not_orphaned(self, tmp_path: Path) -> None:
        from agent_core.venv.builder import python_in_venv

        venvs_dir = tmp_path / "venvs"
        v = venvs_dir / "0.8.0"
        py = python_in_venv(v)
        py.parent.mkdir(parents=True)
        py.write_text("")  # create fake python binary

        assert find_orphaned_partial_builds(venvs_dir) == []

    def test_dir_without_python_is_orphaned(self, tmp_path: Path) -> None:
        venvs_dir = tmp_path / "venvs"
        v = venvs_dir / "0.8.0"
        v.mkdir(parents=True)
        # No python binary created

        result = find_orphaned_partial_builds(venvs_dir)
        assert result == [v]

    def test_multiple_mixed_versions(self, tmp_path: Path) -> None:
        from agent_core.venv.builder import python_in_venv

        venvs_dir = tmp_path / "venvs"
        good = venvs_dir / "0.8.0"
        bad = venvs_dir / "0.7.0"
        py = python_in_venv(good)
        py.parent.mkdir(parents=True)
        py.write_text("")
        bad.mkdir(parents=True)

        result = find_orphaned_partial_builds(venvs_dir)
        assert result == [bad]


# ---------------------------------------------------------------------------
# find_drifted_mcp_json
# ---------------------------------------------------------------------------

class TestFindDriftedMcpJson:
    def test_returns_path_when_mcp_json_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        being_home = tmp_path / ".wren"
        being_home.mkdir()
        monkeypatch.setattr(
            "agent_core.daemon.venv_gc.mcp_json_needs_repair",
            lambda *a, **kw: True,
        )
        # Need to patch via the imported reference in the module namespace
        import agent_core.daemon.venv_gc as venv_gc_mod
        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: True,
        )

        result = find_drifted_mcp_json("wren", vault_root=being_home, daemon_config_dir=tmp_path)
        assert result == being_home / ".mcp.json"

    def test_returns_none_when_canonical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        being_home = tmp_path / ".wren"
        being_home.mkdir()
        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: False,
        )
        result = find_drifted_mcp_json("wren", vault_root=being_home, daemon_config_dir=tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# find_dead_central_corpses
# ---------------------------------------------------------------------------

class TestFindDeadCentralCorpses:
    def test_empty_daemon_home_returns_empty(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        assert find_dead_central_corpses(tmp_path) == []

    def test_absent_daemon_home_returns_empty(self, tmp_path: Path) -> None:
        assert find_dead_central_corpses(tmp_path / "nonexistent") == []

    def test_detects_old_versioned_dir(self, tmp_path: Path) -> None:
        corpse = tmp_path / ".venv-v0.7.0"
        corpse.mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert corpse in result

    def test_detects_multiple_old_versioned_dirs(self, tmp_path: Path) -> None:
        c1 = tmp_path / ".venv-v0.6.1"
        c2 = tmp_path / ".venv-v0.7.0"
        c1.mkdir()
        c2.mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert c1 in result
        assert c2 in result

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_does_not_flag_healthy_stable_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "venvs" / "0.8.0"
        target.mkdir(parents=True)
        stable = tmp_path / ".venv"
        os.symlink(target, stable)
        assert find_dead_central_corpses(tmp_path) == []

    def test_detects_plain_venv_dir_as_corpse(self, tmp_path: Path) -> None:
        # Old 0.6.1-era .venv was a real directory, not a junction/symlink
        plain_venv = tmp_path / ".venv"
        plain_venv.mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert plain_venv in result

    def test_results_are_sorted(self, tmp_path: Path) -> None:
        (tmp_path / ".venv-v0.7.0").mkdir()
        (tmp_path / ".venv-v0.6.1").mkdir()
        result = find_dead_central_corpses(tmp_path)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# VenvGcReport
# ---------------------------------------------------------------------------

class TestVenvGcReport:
    def test_has_issues_false_when_empty(self) -> None:
        assert not VenvGcReport().has_issues

    def test_has_issues_true_when_superseded_venvs(self, tmp_path: Path) -> None:
        r = VenvGcReport(superseded_venvs=[tmp_path / "venvs" / "0.6.0"])
        assert r.has_issues

    def test_has_issues_true_when_dead_corpses(self, tmp_path: Path) -> None:
        r = VenvGcReport(dead_central_corpses=[tmp_path / ".venv-v0.7.0"])
        assert r.has_issues


# ---------------------------------------------------------------------------
# run_venv_doctor integration (monkeypatched detectors)
# ---------------------------------------------------------------------------

class TestRunVenvDoctor:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink test")
    def test_discovers_being_and_collects_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full layout: daemon home with corpse + being home with 3 venvs."""
        daemon_home = tmp_path / ".agent-core"
        daemon_home.mkdir()
        corpse = daemon_home / ".venv-v0.7.0"
        corpse.mkdir()

        being_home = tmp_path / ".wren"
        venvs_dir = being_home / ".agent-core" / "venvs"
        for v in ("0.6.0", "0.7.0", "0.8.0"):
            (venvs_dir / v).mkdir(parents=True)
        # Stable symlink pointing to current (0.8.0)
        stable = being_home / ".venv"
        os.symlink(venvs_dir / "0.8.0", stable)

        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: False,
        )

        report = run_venv_doctor(
            daemon_home=daemon_home,
            home_root=tmp_path,
            daemon_config_dir=daemon_home,
        )
        assert corpse in report.dead_central_corpses
        # 0.6.0 is superseded (current=0.8.0, N-1=0.7.0)
        assert (venvs_dir / "0.6.0") in report.superseded_venvs

    def test_clean_layout_has_no_issues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No beings, clean daemon home → no issues."""
        daemon_home = tmp_path / ".agent-core"
        daemon_home.mkdir()

        monkeypatch.setattr(
            "agent_core.venv.mcp_config.mcp_json_needs_repair",
            lambda *a, **kw: False,
        )

        report = run_venv_doctor(
            daemon_home=daemon_home,
            home_root=tmp_path,
            daemon_config_dir=daemon_home,
        )
        assert not report.has_issues
```

### Addition to `packages/core/tests/test_daemon_cli.py`

```python
def test_doctor_reports_venv_gc_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor runs the venv GC pass and prints findings."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    fake_venv_report = VenvGcReport(
        dead_central_corpses=[tmp_path / ".venv-v0.7.0"],
        superseded_venvs=[tmp_path / "venvs" / "0.6.0"],
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor"])
    assert result.exit_code == 1  # has_issues → non-zero
    output = result.stdout
    assert "dead corpse" in output.lower() or ".venv-v0.7.0" in output
    assert "superseded" in output.lower() or "0.6.0" in output
```

## Alternatives considered

1. **Inline all five detectors directly in `daemon/cli.py`**: Keeps the file count lower. Ruled out — `config_hygiene.py` precedent shows the correct pattern is a separate pure-functions module that the CLI delegates to. Inline logic in the CLI cannot be unit-tested without invoking Typer.

2. **Separate `daemon venv-doctor` sub-command instead of extending `daemon doctor`**: Would give the venv GC section its own namespace. Ruled out — the design spec (M3) explicitly says "daemon doctor [--fix]" as a single unified command. The existing `# TODO #317: venv GC section goes here` comment in `cli.py` confirms the intended integration point.

3. **Discover beings from a config registry instead of filesystem scan**: More explicit, but requires a registry that doesn't exist and isn't on the C2 ticket slate. Ruled out — `discover_being_homes(home_root)` scanning for `.*/. agent-core/venvs/` is self-contained, testable, and finds all real beings on the machine without external state.

4. **Use `packaging.version.Version` for version sorting**: More robust for pre-release strings (e.g. `0.8.0a1`). Ruled out for this ticket — `packaging` is not an explicit declared dep of `packages/core` and the venv builder uses simple `importlib.metadata.version()` which returns normalized version strings. A `tuple[int, ...]` parse of `X.Y.Z` is sufficient; a malformed name gracefully falls back to `(0,)`.

## Open questions

None. The `# TODO #317` comment in `daemon/cli.py:406` is the exact integration point. The venv layout paths are confirmed by reading `venv/builder.py` (`versioned_venv_dir`, `stable_venv_path`). The `.mcp.json` drift check is confirmed by reading `venv/mcp_config.py` (`mcp_json_needs_repair`). The dead corpse patterns (`~/.agent-core/.venv-v0.7.0`, plain `.venv`) are named explicitly in the design spec M3.

## Out of scope

- Pruning (removal) of superseded venvs, broken links, orphaned partial builds, or dead corpses — that is C2-3b (`--fix` side).
- Repairing drifted `.mcp.json` files via the doctor — use `agent-core venv regen-mcp <being>` (C2-2 / #316) for that; the doctor only reports drift.
- Adding the `regen-mcp` call automatically within `--fix` — deferred.
- Multi-being layouts where `vault_root ≠ being_home` (e.g. a being whose vault lives in a non-default directory) — the doctor uses `being_home` as `vault_root`, which matches what `venv regen-mcp` does.
- Source and test daemon instance homes (`~/.agent-core-source/`, `~/.agent-core-test/`) as scan targets — the doctor command already selects the instance via `--instance`; the venv GC section passes `home_for(inst)` as `daemon_home`, so picking `--instance source` would scan `~/.agent-core-source/` instead.
