"""Guard: dynamic-version members must not carry a pinned version in uv.lock,
so source-only commits don't thrash the lockfile (the daemon's --frozen path).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_core.daemon.install import find_workspace_root

WORKSPACE_MEMBERS = {
    "agent-core", "agent-core-credentials", "agent-core-notify",
    "agent-core-briefs", "agent-core-busproxy", "agent-core-channel",
    "agent-core-discord", "agent-core-hatchery", "agent-core-voice",
    "agent-core-webcam",
}


def test_workspace_members_have_no_pinned_version_in_lock() -> None:
    root = find_workspace_root(Path(__file__))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    offenders = [
        p["name"]
        for p in lock["package"]
        if p["name"] in WORKSPACE_MEMBERS and "version" in p
    ]
    assert not offenders, (
        "dynamic-version members must omit `version` in uv.lock "
        f"(lock thrash risk): {offenders}"
    )
