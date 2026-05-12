"""Validator tests."""

import pytest

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher
from agent_core_hatchery.validators import (
    ValidationError,
    validate_daemon_fragments_parse,
    validate_load_bearing_paths,
)


def _hatched_cfg(tmp_path):
    cfg = HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    Hatcher(cfg).hatch()
    # Note: after Step 2 below, Hatcher will write the fragments itself.
    # If your Hatcher already does that by the time this test runs, the
    # explicit DaemonConfigWriter call below is redundant. Remove it then.
    return cfg


def test_load_bearing_paths_pass_after_hatch(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    validate_load_bearing_paths(cfg)


def test_load_bearing_paths_fail_when_missing(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    (cfg.resolved_vault_root() / "Memory" / "SOUL.md").unlink()
    with pytest.raises(ValidationError, match="SOUL.md"):
        validate_load_bearing_paths(cfg)


def test_daemon_fragments_parse(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    validate_daemon_fragments_parse(cfg)


def test_daemon_fragments_fail_on_corruption(tmp_path):
    cfg = _hatched_cfg(tmp_path)
    fragment = cfg.resolved_daemon_config_dir() / "endpoints.d" / "testbeing.yaml"
    fragment.write_text("not: valid: yaml: at all: [")
    with pytest.raises(ValidationError, match="testbeing.yaml"):
        validate_daemon_fragments_parse(cfg)
