"""Generic append-only JSONL audit log base.

Subclasses implement ``_serialize`` to convert a domain event to a JSON
string. The base owns the async write, thread offload, POSIX-atomic
append, and swallow policy.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path


class JsonlAuditLog[E](ABC):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._log = logging.getLogger(self.__class__.__module__)

    @property
    def path(self) -> Path:
        return self._path

    @abstractmethod
    def _serialize(self, event: E) -> str: ...

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def write(self, event: E) -> None:
        try:
            line = self._serialize(event)
            await asyncio.to_thread(self._append_line, self._path, line)
        except Exception as exc:
            msg = (
                f"{self.__class__.__module__}: write failed "
                f"for {self._path}: {exc}"
            )
            self._log.warning(msg)
            print(msg, file=sys.stderr)


__all__ = ["JsonlAuditLog"]
