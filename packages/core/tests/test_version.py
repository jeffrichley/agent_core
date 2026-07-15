"""Fast: __version__ is backed by importlib.metadata, not a hardcoded string."""
from __future__ import annotations

from importlib.metadata import version


def test_version_matches_importlib_metadata() -> None:
    import agent_core

    assert agent_core.__version__ == version("agent-core")


def test_version_is_not_hardcoded_sentinel() -> None:
    import agent_core

    # The old hardcoded value was "0.1.0".  If dynamic versioning is working,
    # the installed version will be something else (or at minimum not the
    # stale sentinel).  This guard catches accidental rollback.
    assert agent_core.__version__ != "0.1.0"
