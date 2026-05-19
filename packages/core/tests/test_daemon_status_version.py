"""daemon status prints an `installed version:` line (best-effort)."""

from __future__ import annotations

from agent_core.daemon import cli


def test_installed_version_helper_returns_unknown_when_unresolvable(tmp_path):
    # A bogus interpreter path → helper must not raise, returns "unknown".
    assert cli._installed_version(str(tmp_path / "nope" / "python")) == "unknown"


def test_installed_version_helper_reads_metadata() -> None:
    # The current interpreter has agent-core installed (editable dev env).
    import sys

    v = cli._installed_version(sys.executable)
    assert v != "unknown" and v.strip()
