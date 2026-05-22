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


def test_resolve_instance_env_selects_dev() -> None:
    assert resolve_instance(flag=None, env="dev") is Instance.DEV


def test_resolve_instance_flag_beats_env() -> None:
    # flag says prod, env says dev — flag wins
    assert resolve_instance(flag="prod", env="dev") is Instance.PROD


def test_resolve_instance_is_case_insensitive() -> None:
    assert resolve_instance(flag="DEV", env=None) is Instance.DEV


def test_resolve_instance_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown instance"):
        resolve_instance(flag="staging", env=None)


# ---- home_for ---------------------------------------------------------------

def test_home_for_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    assert home_for(Instance.PROD) == Path.home() / ".agent-core"


def test_home_for_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_CORE_HOME", raising=False)
    assert home_for(Instance.DEV) == Path.home() / ".agent-core-dev"


def test_home_for_honors_agent_core_home_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    # Override wins for BOTH instances.
    assert home_for(Instance.PROD) == tmp_path
    assert home_for(Instance.DEV) == tmp_path


# ---- default_port -----------------------------------------------------------

def test_default_port_prod() -> None:
    assert default_port(Instance.PROD) == 8789


def test_default_port_dev() -> None:
    assert default_port(Instance.DEV) == 8788
