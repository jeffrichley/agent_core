"""Shared subprocess-environment helpers for the daemon launcher.

This thin module is the single source of truth for the subprocess env
blocklist.  Both ``cli.py`` and ``windows_service.py`` import from here
so there is no duplication — ``cli.py`` cannot be the source because it
already imports ``windows_service`` at module level (line 33), which would
create a circular import.
"""

from __future__ import annotations

import os

# Secret-key names that must never be inherited by child processes.
# Belt-and-suspenders: after Dα-3 the accessor reads from the vault, but
# any residual shell vars are stripped here.
_DAEMON_ENV_BLOCKLIST: frozenset[str] = frozenset(
    {
        "AGENT_CORE_VAULT_PASSWORD",
        "FOREMAN_GITHUB_WEBHOOK_SECRET",
        "AGENTMAIL_API_KEY",
    }
)


def _daemon_safe_env() -> dict[str, str]:
    """Return a copy of ``os.environ`` with known secret keys removed."""
    return {k: v for k, v in os.environ.items() if k not in _DAEMON_ENV_BLOCKLIST}
