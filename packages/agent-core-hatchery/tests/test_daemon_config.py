"""Tests for daemon_config.py — writing endpoints.d/ and jobs.d/ fragments."""

from pathlib import Path

import pytest
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.daemon_config import DaemonConfigWriter


def test_writes_endpoints_fragment(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    writer = DaemonConfigWriter(cfg)
    written = writer.write_all()

    endpoints_path = (tmp_path / ".agent-core" / "endpoints.d" / "deb.yaml")
    assert endpoints_path in written
    assert endpoints_path.is_file()

    parsed = yaml.safe_load(endpoints_path.read_text())
    names = {e["name"] for e in parsed["endpoints"]}
    assert names == {"deb", "briefs.deb"}


def test_writes_jobs_fragment(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    writer = DaemonConfigWriter(cfg)
    writer.write_all()

    jobs_path = tmp_path / ".agent-core" / "jobs.d" / "deb.yaml"
    parsed = yaml.safe_load(jobs_path.read_text())
    expected = {
        "deb-heartbeat",
        "deb-nightly_reflection",
        "deb-vault_lint",
        "deb-auth_health_probe",
        "deb-service_liveness_probe",
    }
    assert set(parsed.keys()) == expected


def test_endpoint_name_collision_with_existing_fragment_raises(tmp_path):
    """If endpoints.d/<being>.yaml already exists, refuse (don't overwrite)."""
    daemon_dir = tmp_path / ".agent-core"
    (daemon_dir / "endpoints.d").mkdir(parents=True)
    (daemon_dir / "endpoints.d" / "deb.yaml").write_text("endpoints: []\n")

    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(daemon_dir),
    )
    with pytest.raises(FileExistsError, match="deb.yaml"):
        DaemonConfigWriter(cfg).write_all()
