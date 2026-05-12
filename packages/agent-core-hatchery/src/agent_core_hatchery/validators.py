"""Post-hatch validation. Phase 3 expands this module with daemon-fragment
parse checks and endpoint-registration probes.
"""

from __future__ import annotations

import yaml

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


def validate_daemon_fragments_parse(config: HatchConfig) -> None:
    """Confirm the written daemon fragments are valid YAML.

    Phase 5+ may expand this to also validate against agent-core/core's
    Pydantic endpoint schemas (PipelineConfig, JobDef, endpoint type schemas).
    """
    daemon_dir = config.resolved_daemon_config_dir()
    fragment_paths = [
        daemon_dir / "endpoints.d" / f"{config.being_name_lower}.yaml",
        daemon_dir / "jobs.d" / f"{config.being_name_lower}.yaml",
    ]
    for fp in fragment_paths:
        if not fp.is_file():
            raise ValidationError(f"Missing daemon fragment: {fp}")
        try:
            yaml.safe_load(fp.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse daemon fragment {fp.name}: {exc}") from exc
