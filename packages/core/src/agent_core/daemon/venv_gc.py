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

    # When current is unknown (broken/absent stable), keep top-2 by version.
    if current_dir is None:
        for d in all_dirs:
            if d not in keep:
                keep.append(d)
                break

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
