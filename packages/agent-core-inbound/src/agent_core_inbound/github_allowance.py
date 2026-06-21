"""TOML-driven GitHub allowance policy.

The principal being (Wren) edits this file directly to manage their
GitHub inbound rules. The router watches mtime and reloads on change
(see Task 10).
"""
from __future__ import annotations

import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_core_inbound.types import Tier


class AllowRule(BaseModel):
    """One allowance rule. First-match-wins in classify()."""

    rule_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    tier: Tier
    reason: str = Field(min_length=1)

    # Optional match constraints. All present constraints must match
    # for the rule to apply. Missing constraints are wildcard.
    repo: str | None = None
    reviewer: str | None = None
    label_name: str | None = None
    match: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_body_contains(cls, data: Any) -> Any:
        if isinstance(data, dict) and "body_contains" in data:
            raise ValueError(
                "AllowRule field 'body_contains' was removed in v2. "
                "Use `match` with an exact-equality dotted path; substring "
                "matching is deferred to v2.1 (`match_contains` operator)."
            )
        return data


class AllowanceConfig(BaseModel):
    """The complete allowance policy."""

    allow: list[AllowRule] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _enforce_unique_rule_ids(self) -> AllowanceConfig:
        counts = Counter(r.rule_id for r in self.allow)
        dups = [rid for rid, n in counts.items() if n > 1]
        if dups:
            raise ValueError(f"duplicate rule_id(s): {', '.join(sorted(dups))}")
        return self


def load_allowance(path: Path) -> AllowanceConfig:
    """Read + validate the TOML at ``path``.

    Returns an empty config (allow=[]) for an empty or missing-rules
    file. Validation errors propagate as ``pydantic.ValidationError``
    for the caller to log / surface.
    """
    if not path.exists():
        return AllowanceConfig(allow=[])
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return AllowanceConfig.model_validate(data)
