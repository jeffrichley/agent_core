"""Pure generator for a minimal daemon `agent_core.yaml`.

Used by `agent-core daemon init` to scaffold a fresh config for an
instance. Minimal by design: bus + http (correct port) + one stub
endpoint. Specific endpoints are added by hand when they are being
tested.
"""

from __future__ import annotations

from pathlib import Path

from agent_core.daemon.instance import Instance, default_port


def build_default_config(*, instance: Instance, home: Path) -> str:
    """Return the text of a minimal `agent_core.yaml` for `instance`.

    `storage_path` points inside `home`; `bind_port` is the instance
    default (8789 prod / 8788 dev).
    """
    port = default_port(instance)
    storage = home / "bus.sqlite"
    return (
        "bus:\n"
        f"  storage_path: {storage}\n"
        "\n"
        "http:\n"
        "  bind_host: 127.0.0.1\n"
        f"  bind_port: {port}\n"
        "\n"
        "endpoints:\n"
        "  - type: builtin.stub\n"
        "    name: stub\n"
    )
