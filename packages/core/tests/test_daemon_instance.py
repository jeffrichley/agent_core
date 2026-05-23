"""Unit tests for daemon/instance.py — pure instance resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.daemon.instance import (
    Instance,
    default_port,
    home_for,
    resolve_instance,
)

# ---- resolve_instance -------------------------------------------------------

def test_resolve_instance_defaults_to_prod() -> None:
    assert resolve_instance(flag=None, env=None) is Instance.PROD


def test_resolve_instance_env_selects_source() -> None:
    assert resolve_instance(flag=None, env="source") is Instance.SOURCE


def test_resolve_instance_flag_beats_env() -> None:
    # flag says prod, env says source — flag wins
    assert resolve_instance(flag="prod", env="source") is Instance.PROD


def test_resolve_instance_is_case_insensitive() -> None:
    assert resolve_instance(flag="SOURCE", env=None) is Instance.SOURCE


def test_resolve_instance_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown instance"):
        resolve_instance(flag="staging", env=None)


# ---- home_for ---------------------------------------------------------------

def test_home_for_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    assert home_for(Instance.PROD) == Path.home() / ".agent-core"


def test_home_for_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    assert home_for(Instance.SOURCE) == Path.home() / ".agent-core-source"


def test_home_for_honors_agent_core_home_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Override wins for ALL instances.
    assert home_for(Instance.PROD) == tmp_path
    assert home_for(Instance.SOURCE) == tmp_path


# ---- default_port -----------------------------------------------------------

def test_default_port_prod() -> None:
    assert default_port(Instance.PROD) == 8789


def test_default_port_source() -> None:
    assert default_port(Instance.SOURCE) == 8788


# ---- Instance.TEST ----------------------------------------------------------

def test_instance_test_value():
    """TEST instance is the third value, used for sandboxed deploy validation."""
    assert Instance.TEST == "test"
    assert Instance.TEST.value == "test"


def test_home_for_test_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST home is ~/.agent-core-test/ to keep state disjoint from prod/source."""
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    expected = Path.home() / ".agent-core-test"
    assert home_for(Instance.TEST) == expected


def test_default_port_test_instance():
    """TEST port is 8787 (decrementing from prod=8789, source=8788)."""
    assert default_port(Instance.TEST) == 8787


def test_resolve_instance_rejects_dev_after_rename():
    """Hard cutover (Phase 3.5): --instance dev is no longer accepted; the
    parse error directs callers to the new {prod, source, test} choice set."""
    with pytest.raises(ValueError) as exc:
        Instance("dev")
    assert "dev" in str(exc.value)
