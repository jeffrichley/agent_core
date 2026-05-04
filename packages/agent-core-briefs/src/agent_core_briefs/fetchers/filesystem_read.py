"""filesystem_read — read a file into the context, parsed per format."""

from __future__ import annotations

import json
from datetime import datetime

import yaml

from agent_core_briefs.config import expand_path


class FilesystemReadFetcher:
    """Read a single file into the gather context.

    Config:
    - ``path``: filesystem path (supports ``~/`` expansion).
    - ``format``: ``"text"`` (default), ``"json"``, ``"yaml"``, ``"lines"``.

    Returns a dict shape that varies by format. Use a wrapper fetcher
    in the agent's repo if you need fixed-shape output across formats.
    """

    type_id = "filesystem_read"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict, when: datetime) -> dict:
        path = expand_path(config["path"])
        fmt = config.get("format", "text")
        text = path.read_text(encoding="utf-8")

        if fmt == "text":
            return {"content": text, "path": str(path)}
        if fmt == "json":
            return json.loads(text)
        if fmt == "yaml":
            return yaml.safe_load(text)
        if fmt == "lines":
            return {"lines": [line.strip() for line in text.splitlines() if line.strip()]}
        raise ValueError(f"filesystem_read: unknown format {fmt!r}")
