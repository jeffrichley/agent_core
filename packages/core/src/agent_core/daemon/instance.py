"""Daemon instance selection — pure resolution of prod vs dev.

An "instance" picks the daemon's home directory and default bus port.
Resolution precedence (most specific wins):
  1. explicit --instance flag
  2. AGENT_CORE_INSTANCE env var
  3. default: prod

AGENT_CORE_HOME is a separate escape hatch (used by tests): when set,
home_for() returns it directly, bypassing the instance -> home mapping.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path


class Instance(StrEnum):
    """The daemon instance: production or development."""

    PROD = "prod"
    DEV = "dev"


def resolve_instance(*, flag: str | None, env: str | None) -> Instance:
    """Resolve the daemon instance from a CLI flag and an env var.

    `flag` wins over `env`; `env` wins over the default (`prod`). Raises
    ValueError on an unrecognized value.
    """
    raw = (flag or env or "prod").lower()
    try:
        return Instance(raw)
    except ValueError:
        raise ValueError(
            f"unknown instance {raw!r} — expected 'prod' or 'dev'"
        ) from None


def home_for(instance: Instance) -> Path:
    """Return the home directory for an instance.

    AGENT_CORE_HOME, when set, overrides the mapping entirely (test
    escape hatch). Otherwise prod -> ~/.agent-core/, dev ->
    ~/.agent-core-dev/.
    """
    override = os.environ.get("AGENT_CORE_HOME")
    if override:
        return Path(override)
    name = ".agent-core" if instance is Instance.PROD else ".agent-core-dev"
    return Path.home() / name


def default_port(instance: Instance) -> int:
    """Default bus port for an instance (used by `daemon init` scaffolding)."""
    return 8789 if instance is Instance.PROD else 8788
