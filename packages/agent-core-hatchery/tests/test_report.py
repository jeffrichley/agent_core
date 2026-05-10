"""Tests for HATCHING-REPORT.md generator."""

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import HatchResult
from agent_core_hatchery.report import write_hatching_report


def test_happy_path_report(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    vault = cfg.resolved_vault_root()
    vault.mkdir(parents=True)
    result = HatchResult(vault_root=vault)
    result.files_written = [vault / "Memory" / "IDENTITY.md"]

    path = write_hatching_report(
        cfg, result,
        letter_authored=True,
        daemon_check_status="reachable_and_registered",
    )
    assert path == vault / "HATCHING-REPORT.md"
    body = path.read_text()
    assert "# HATCHING-REPORT — Deb" in body
    assert "Cynthia" in body
    assert "REQUIRED" not in body  # happy path; no escalation


def test_escalated_report_when_letter_skipped(tmp_path):
    cfg = HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )
    vault = cfg.resolved_vault_root()
    vault.mkdir(parents=True)
    result = HatchResult(vault_root=vault)
    path = write_hatching_report(
        cfg, result,
        letter_authored=False,
        daemon_check_status="unreachable",
    )
    body = path.read_text()
    assert "REQUIRED BEFORE FIRST AWAKENING" in body
    assert "ACTION REQUIRED" in body  # daemon-unreachable escalation
