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
