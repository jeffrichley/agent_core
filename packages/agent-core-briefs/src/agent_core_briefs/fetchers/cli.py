"""cli — run a CLI command and parse its stdout into the context."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

import yaml

from agent_core_briefs.config import expand_path


class CliFetcher:
    """Wrap a CLI command. Captures stdout, parses per ``parse`` setting.

    Config:
    - ``command`` (list[str]): argv. First element is the executable.
    - ``cwd`` (str | None): working directory (supports ``~/`` expansion).
    - ``parse``: ``"text"`` | ``"json"`` | ``"yaml"`` | ``"lines"``.
    - ``env_passthrough`` (list[str]): env var names to forward; everything
      else is dropped (clean env, no inherited surprises).

    Non-zero exit codes raise ``RuntimeError`` with stderr captured. Invalid
    parse output raises ``ValueError``.

    JSON/YAML roots must be dicts (mappings); list or scalar roots raise
    ``ValueError``. Wrap with a fetcher in your agent's repo if you need
    to adapt non-dict shapes.
    """

    type_id = "cli"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict, when: datetime) -> dict:
        command = list(config["command"])
        cwd = expand_path(config["cwd"]) if config.get("cwd") else None
        parse = config.get("parse", "text")
        passthrough = config.get("env_passthrough", [])

        env: dict[str, str] = {name: os.environ[name] for name in passthrough if name in os.environ}

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"cli: command exited with {proc.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')}"
            )

        text = stdout.decode("utf-8")
        if parse == "text":
            return {"stdout": text}
        if parse == "json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"cli: stdout is not valid json: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"cli: json root must be an object/dict, got {type(parsed).__name__}"
                )
            return parsed
        if parse == "yaml":
            parsed = yaml.safe_load(text)
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"cli: yaml root must be a mapping/dict, got {type(parsed).__name__}"
                )
            return parsed
        if parse == "lines":
            return {"lines": [line.strip() for line in text.splitlines() if line.strip()]}
        raise ValueError(f"cli: unknown parse {parse!r}")
