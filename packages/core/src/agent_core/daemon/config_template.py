"""Pure generator for a minimal daemon `agent_core.yaml`.

Used by `agent-core daemon init` to scaffold a fresh config for an
instance. Minimal by design: bus + http (correct port) + one stub
endpoint. Specific endpoints are added by hand when they are being
tested.

The TEST instance additionally registers a `qa` ClaudeCodeMCPEndpoint
so that release-validation tooling (agent-core-qa) can call MCP tools
against `/mcp/qa/` on a freshly-installed test daemon without any
post-install configuration. The test instance exists FOR validation;
provisioning the validation endpoint by default makes the qa-runner's
assumption true by construction. Prod and source intentionally do NOT
register `qa` — keystone identity is install-code-path, not config.
"""

from __future__ import annotations

from pathlib import Path

from agent_core.daemon.instance import Instance, default_port


def build_default_config(*, instance: Instance, home: Path) -> str:
    """Return the text of a minimal `agent_core.yaml` for `instance`.

    `storage_path` points inside `home`; `bind_port` is the instance
    default (prod 8789 / source 8788 / test 8787). TEST adds a `qa`
    ClaudeCodeMCPEndpoint at `/mcp/qa/` so agent-core-qa can hit a
    fresh test daemon without manual config.
    """
    port = default_port(instance)
    storage = home / "bus.sqlite"
    base = (
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
    if instance is Instance.TEST:
        base += (
            "  - type: builtin.claude_code_mcp\n"
            "    name: qa\n"
            "    params:\n"
            "      mount: /mcp/qa\n"
            "  - type: builtin.scheduler\n"
            "    name: scheduler\n"
            "  - type: builtin.stub\n"
            "    name: discord\n"
        )
    return base
