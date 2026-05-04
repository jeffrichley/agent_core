"""Filesystem-discovered class loader.

Scans configured paths, imports each ``.py`` via importlib.util, registers
classes satisfying a given Protocol by their ``type_id`` attribute. Used
for fetchers, destinations, and extensions — same shape, same code path.

Hot reload is implicit: the loader doesn't cache. Each call re-imports.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class LoaderError(Exception):
    """Raised on duplicate type_id, syntax error, or other load failure."""


def discover_implementations[T](
    paths: list[Path],
    *,
    protocol: type[T],
) -> dict[str, type[T]]:
    """Walk ``paths``, import each .py file, register classes satisfying
    ``protocol`` keyed by their ``type_id`` attribute.

    Missing paths log a WARNING and are skipped (operator may have
    misconfigured a path; warn loudly but don't crash). Syntax errors
    and duplicate type_ids raise LoaderError — fail loud, surface the
    conflict immediately rather than letting one silently shadow the
    other.
    """
    registry: dict[str, type[T]] = {}
    for base in paths:
        if not base.exists():
            log.warning("loader: path not found, skipping: %s", base)
            continue
        if not base.is_dir():
            raise LoaderError(f"loader: path is not a directory: {base}")
        for py_file in sorted(base.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module = _import_module_by_path(py_file)
            for _, member in inspect.getmembers(module, inspect.isclass):
                if member.__module__ != module.__name__:
                    continue  # imported, not defined here
                # Check the class itself has type_id (Protocol structural check
                # would also accept instances, but we want the class for registry)
                if not hasattr(member, "type_id"):
                    continue
                # Verify an instance satisfies the runtime-checkable Protocol
                try:
                    instance = member()
                except Exception:
                    continue  # can't instantiate without args; not our impl
                if not isinstance(instance, protocol):
                    continue
                type_id = member.type_id
                if type_id in registry:
                    raise LoaderError(
                        f"loader: duplicate type_id {type_id!r} "
                        f"(already registered from another module)"
                    )
                registry[type_id] = member
    return registry


def _import_module_by_path(py_file: Path) -> Any:
    """Import a .py file by absolute path without polluting sys.path.

    Uses a unique module name based on the file's absolute path so two
    files with the same basename in different directories don't collide.
    """
    module_name = f"_briefs_loaded_{py_file.stem}_{abs(hash(str(py_file.absolute())))}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            raise LoaderError(f"loader: cannot create spec for {py_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except SyntaxError as exc:
        raise LoaderError(f"loader: syntax error in {py_file}: {exc}") from exc
    except Exception as exc:
        raise LoaderError(f"loader: failed to import {py_file}: {exc}") from exc
