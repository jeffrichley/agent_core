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


def _matches_glob(path: str, pattern: str) -> bool:
    """Match `path` against `pattern`, supporting ** for recursive directory match.

    fnmatch doesn't natively support **, so we handle it explicitly:
    - "a/**/*" matches a/b, a/b/c, a/b/c/d, etc.
    - "a/**" matches anything under a/.
    - Literal patterns and single-segment globs delegate to fnmatch.fnmatchcase.
    """
    if "**" not in pattern:
        return fnmatch.fnmatchcase(path, pattern)

    # Split on /**/ — anything before is the prefix, anything after is suffix glob.
    if "/**/" in pattern:
        prefix, _, suffix = pattern.partition("/**/")
        if not path.startswith(prefix + "/"):
            return False
        remaining = path[len(prefix) + 1:]
        # Walk every possible split point in `remaining` and try suffix match
        # at the leaf segment first; widen if needed.
        # Simpler: check if any suffix of remaining matches the suffix glob.
        parts = remaining.split("/")
        for i in range(len(parts)):
            candidate = "/".join(parts[i:])
            if fnmatch.fnmatchcase(candidate, suffix):
                return True
        return False

    # Pattern ends with /** — match anything under the prefix.
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")

    # ** appearing without surrounding slashes — uncommon; treat as fnmatch with ** → *.
    return fnmatch.fnmatchcase(path, pattern.replace("**", "*"))


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
                if _matches_glob(norm, g):
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
