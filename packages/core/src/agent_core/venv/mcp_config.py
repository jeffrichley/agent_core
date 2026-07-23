"""Canonical ``.mcp.json`` generator (C2-2, issue #316).

Each being's ``.mcp.json`` lists three stdio MCP servers — ``agent-core``
(busproxy), ``agent-core-channel``, and ``notify`` — that Claude Code launches
as subprocesses. Every server's ``command`` is the being's Python interpreter.

The headline ``[P0]`` this module fixes: that interpreter path used to be
hand-written as a **version-stamped, machine-specific** absolute path
(``…/.agent-core/.venv-v0.7.0/Scripts/python.exe``), so every venv bump
stranded the being. The canonical shape instead points at the **stable**
per-being venv path ``~/.<being>/.venv/…`` that the venv builder (C2-1)
atomically repoints across upgrades.

Two correctness invariants worth stating loudly:

* The path is written **absolute**, because Claude Code does not expand ``~``
  or environment variables in an MCP ``command``.
* The path is **never symlink-resolved**. ``~/.<being>/.venv`` is a junction
  to the current versioned dir; resolving it would land back on
  ``…/venvs/<version>/`` and re-introduce the exact version-stamp drift this
  generator exists to kill. We build the path from the stable location only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agent_core.venv.builder import (
    home_for_target,
    python_in_venv,
    stable_venv_path,
)

#: Filename of a being's MCP server manifest, written at its vault root.
MCP_JSON_NAME = ".mcp.json"

#: Loopback bus URL used when the daemon config declares no explicit port.
_DEFAULT_DAEMON_PORT = 8789


def stable_interpreter(being_name: str) -> Path:
    """Return the stable, absolute interpreter path for a being's venv.

    ``~/.<being>/.venv/<bin>/python[.exe]`` — the junction path the venv
    builder repoints on upgrade. Deliberately **not** resolved through the
    junction, so a venv version bump never strands the being (the C2-2
    headline P0).

    Args:
        being_name: Lowercased being name (e.g. ``"wren"``).

    Returns:
        The absolute interpreter path (unresolved junction).
    """
    return python_in_venv(stable_venv_path(home_for_target(being_name)))


def _daemon_url(daemon_config_dir: Path) -> str:
    """Return the loopback bus URL, taking the port from the daemon config.

    Reads ``http.bind_port`` from ``<daemon_config_dir>/agent_core.yaml`` when
    present; falls back to the default port otherwise. The host is always
    ``127.0.0.1``: the bus binds loopback, and an MCP client connects over
    loopback regardless of the server's advertised ``bind_host``.

    Args:
        daemon_config_dir: The daemon's config directory (e.g. ``~/.agent-core``).

    Returns:
        A ``http://127.0.0.1:<port>`` URL.
    """
    port = _DEFAULT_DAEMON_PORT
    config_path = daemon_config_dir / "agent_core.yaml"
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return f"http://127.0.0.1:{port}"
    http = raw.get("http") if isinstance(raw, dict) else None
    if isinstance(http, dict) and isinstance(http.get("bind_port"), int):
        port = http["bind_port"]
    return f"http://127.0.0.1:{port}"


def build_mcp_config(
    being_name: str,
    *,
    daemon_url: str,
    interpreter: Path,
) -> dict[str, Any]:
    """Return the canonical ``.mcp.json`` document for a being.

    Pure and deterministic — no filesystem access — so it is the single source
    of truth shared by :func:`generate_mcp_json` (writes) and
    :func:`mcp_json_needs_repair` (compares).

    Args:
        being_name: Lowercased being name, used as the ``--agent`` value.
        daemon_url: The bus URL passed to the busproxy and channel servers.
        interpreter: The stable interpreter path used as every server's command.

    Returns:
        The ``.mcp.json`` document as a nested dict.
    """
    command = str(interpreter)
    return {
        "mcpServers": {
            "agent-core": {
                "type": "stdio",
                "command": command,
                "args": [
                    "-m",
                    "agent_core_busproxy",
                    "--agent",
                    being_name,
                    "--daemon-url",
                    daemon_url,
                ],
            },
            "agent-core-channel": {
                "type": "stdio",
                "command": command,
                "args": [
                    "-m",
                    "agent_core_channel",
                    "--agent",
                    being_name,
                    "--daemon-url",
                    daemon_url,
                ],
            },
            "notify": {
                "type": "stdio",
                "command": command,
                "args": ["-m", "agent_core_notify.mcp_server"],
            },
        }
    }


def generate_mcp_json(
    being_name: str,
    *,
    vault_root: Path,
    daemon_config_dir: Path,
) -> Path:
    """Write a being's canonical ``.mcp.json`` and return its path.

    Overwrites any existing (possibly drifted) file — this is the write half of
    the generate/repair pair, and the entry point the hatchery reuses (Theme C).

    Args:
        being_name: Lowercased being name.
        vault_root: The being's vault directory; ``.mcp.json`` is written here.
        daemon_config_dir: The daemon config dir, used to resolve the bus port.

    Returns:
        The path to the written ``.mcp.json``.
    """
    # The file is written at ``vault_root`` (where the being's project lives),
    # but the interpreter is derived from the being's *venv* location
    # (``home_for_target(being_name)``) — the exact path the C2-1 builder
    # repoints. In the standard case these coincide (``vault_root`` defaults to
    # the being's home); the interpreter must track the builder, not the write
    # location, so a non-default ``vault_root`` never mis-points the command.
    document = build_mcp_config(
        being_name,
        daemon_url=_daemon_url(daemon_config_dir),
        interpreter=stable_interpreter(being_name),
    )
    path = vault_root / MCP_JSON_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def mcp_json_needs_repair(
    being_name: str,
    *,
    vault_root: Path,
    daemon_config_dir: Path,
) -> bool:
    """Return whether a being's ``.mcp.json`` is missing or not canonical.

    A missing file, an unparseable file, or any deviation from the canonical
    document (most importantly a version-stamped interpreter path) counts as
    needing repair.

    Args:
        being_name: Lowercased being name.
        vault_root: The being's vault directory.
        daemon_config_dir: The daemon config dir, used to resolve the bus port.

    Returns:
        ``True`` if the file should be regenerated.
    """
    path = vault_root / MCP_JSON_NAME
    if not path.is_file():
        return True
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    expected = build_mcp_config(
        being_name,
        daemon_url=_daemon_url(daemon_config_dir),
        interpreter=stable_interpreter(being_name),
    )
    return current != expected


def repair_mcp_json(
    being_name: str,
    *,
    vault_root: Path,
    daemon_config_dir: Path,
) -> tuple[Path, bool]:
    """Regenerate a being's ``.mcp.json`` only if it has drifted.

    The idempotent entry point ``daemon doctor`` (M3) re-runs: a canonical file
    is left untouched; a missing or drifted one is rewritten.

    Args:
        being_name: Lowercased being name.
        vault_root: The being's vault directory.
        daemon_config_dir: The daemon config dir, used to resolve the bus port.

    Returns:
        ``(path, changed)`` — ``changed`` is ``True`` iff the file was rewritten.
    """
    if not mcp_json_needs_repair(
        being_name, vault_root=vault_root, daemon_config_dir=daemon_config_dir
    ):
        return vault_root / MCP_JSON_NAME, False
    path = generate_mcp_json(being_name, vault_root=vault_root, daemon_config_dir=daemon_config_dir)
    return path, True
