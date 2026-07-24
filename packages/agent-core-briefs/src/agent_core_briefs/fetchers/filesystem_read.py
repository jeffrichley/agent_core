"""filesystem_read — read a file into the context, parsed per format."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import yaml

from agent_core_briefs.config import expand_path


class FilesystemReadFetcher:
    """Read a single file into the gather context.

    Config:
    - ``path``: filesystem path (supports ``~/`` expansion).
    - ``format``: ``"text"`` (default), ``"json"``, ``"yaml"``, ``"lines"``.

    Returns a dict shape that varies by format. Use a wrapper fetcher
    in the agent's repo if you need fixed-shape output across formats.

    JSON/YAML roots must be dicts (mappings); list or scalar roots raise
    ``ValueError``. Wrap with a fetcher in your agent's repo if you need
    to adapt non-dict file shapes.
    """

    type_id = "filesystem_read"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict[str, Any], when: datetime) -> dict[str, Any]:
        path = expand_path(config["path"])
        fmt = config.get("format", "text")
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")

        if fmt == "text":
            return {"content": text, "path": str(path)}
        if fmt == "json":
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"filesystem_read: json root must be an object/dict, "
                    f"got {type(parsed).__name__}"
                )
            return parsed
        if fmt == "yaml":
            parsed = yaml.safe_load(text)
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"filesystem_read: yaml root must be a mapping/dict, "
                    f"got {type(parsed).__name__}"
                )
            return parsed
        if fmt == "lines":
            return {"lines": [line.strip() for line in text.splitlines() if line.strip()]}
        raise ValueError(f"filesystem_read: unknown format {fmt!r}")
