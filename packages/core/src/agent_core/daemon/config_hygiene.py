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
