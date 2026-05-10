"""Post-hatch validation. Phase 3 expands this module with daemon-fragment
parse checks and endpoint-registration probes.
"""

from __future__ import annotations

from agent_core_hatchery.config import HatchConfig


LOAD_BEARING_FILES = (
    "Memory/IDENTITY.md",
    "Memory/SOUL.md",
    "Memory/USER.md",
    "Memory/MEMORY.md",
    "Memory/OPERATIONS.md",
)
LOAD_BEARING_DIRS = ("Memory/daily/summaries",)


class ValidationError(Exception):
    pass


def validate_load_bearing_paths(config: HatchConfig) -> None:
    vault = config.resolved_vault_root()
    for rel in LOAD_BEARING_FILES:
        p = vault / rel
        if not p.is_file():
            raise ValidationError(f"Missing required file: {p}")
        if p.stat().st_size == 0:
            raise ValidationError(f"Required file is empty: {p}")
    for rel in LOAD_BEARING_DIRS:
        p = vault / rel
        if not p.is_dir():
            raise ValidationError(f"Missing required directory: {p}")
