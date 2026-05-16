"""Package scaffold smoke test."""

from __future__ import annotations


def test_package_imports() -> None:
    import agent_core_busproxy

    assert agent_core_busproxy.__doc__ is not None
