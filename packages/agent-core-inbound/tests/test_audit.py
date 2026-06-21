"""AuditLog: one JSONL line per classification (Allow OR Deny).

Allow entries carry tier + reason + connector + rule_id; Deny entries
carry only timestamp + source + target. A Deny line MUST NOT
serialize the underlying event payload (privacy + storage), so the
writer takes ``connector_name`` and ``target_being`` directly rather
than reading them off an event.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.types import Allow, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def test_audit_log_writes_allow_line(tmp_path: Path):
    log_path = tmp_path / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_allow(
        connector_name="github",
        target_being="wren",
        verdict=Allow(tier=Tier.RED, reason="PR review requested on foreman"),
        rule_id="pr_review_requested_foreman",
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {
        "ts": "2026-06-20T22:00:00+00:00",
        "source": "github",
        "to": "wren",
        "verdict": "allow",
        "tier": "red",
        "rule_id": "pr_review_requested_foreman",
        "reason": "PR review requested on foreman",
    }


def test_audit_log_writes_deny_line(tmp_path: Path):
    log_path = tmp_path / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_deny(connector_name="github", target_being="wren")
    lines = log_path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert entry == {
        "ts": "2026-06-20T22:00:00+00:00",
        "source": "github",
        "to": "wren",
        "verdict": "deny",
    }
    # No reason, no tier, no rule_id, no event body on Deny lines.


def test_audit_log_appends_not_truncates(tmp_path: Path):
    log_path = tmp_path / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_deny(connector_name="github", target_being="wren")
    log.record_deny(connector_name="github", target_being="wren")
    log.record_allow(
        connector_name="github",
        target_being="wren",
        verdict=Allow(tier=Tier.GREEN, reason="x"),
        rule_id="r1",
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def test_audit_log_creates_parent_dir(tmp_path: Path):
    log_path = tmp_path / "nested" / "deeper" / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_deny(connector_name="github", target_being="wren")
    assert log_path.exists()
