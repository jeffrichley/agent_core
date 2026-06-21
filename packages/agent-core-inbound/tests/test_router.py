"""Router: receive → classify → bus delivery + audit log."""
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent_core_inbound.audit import AuditLog
from agent_core_inbound.router import Router
from agent_core_inbound.testing import FakeConnector
from agent_core_inbound.types import ConnectorEvent, Tier


class _FakeBus:
    """Captures published envelopes for assertion."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(
        self,
        *,
        to: str,
        kind: str,
        payload: dict[str, Any],
        urgency: str,
    ) -> None:
        self.published.append({
            "to": to,
            "kind": kind,
            "payload": payload,
            "urgency": urgency,
        })


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def _event(event_id: str = "evt-1") -> ConnectorEvent:
    return ConnectorEvent(
        event_id=event_id,
        landed_at=_stamp(),
        raw={"pr_number": 387, "repo": "jeffrichley/foreman"},
    )


def _router(
    *,
    tmp_path: Path,
    connector: FakeConnector,
    bus: _FakeBus,
) -> Router:
    audit = AuditLog(path=tmp_path / "audit.jsonl", clock=lambda: _stamp())
    return Router(
        connectors={"fake": connector},
        bus_publish=bus.publish,
        audit=audit,
        clock=lambda: _stamp(),
    )


def test_allow_publishes_notification_envelope(tmp_path: Path):
    connector = FakeConnector()
    connector.allow(
        event_id="evt-1",
        target_being="wren",
        tier=Tier.RED,
        reason="PR review requested on foreman",
        rule_id="pr_review_requested_foreman",
    )
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    assert len(bus.published) == 1
    pub = bus.published[0]
    assert pub["to"] == "wren"
    assert pub["kind"] == "Notification"
    assert pub["urgency"] == "red"
    assert pub["payload"]["source"] == "fake"
    assert pub["payload"]["reason"] == "PR review requested on foreman"
    assert pub["payload"]["body"] == {"pr_number": 387, "repo": "jeffrichley/foreman"}


def test_allow_writes_audit_line(tmp_path: Path):
    connector = FakeConnector()
    connector.allow(
        event_id="evt-1",
        target_being="wren",
        tier=Tier.RED,
        reason="PR review requested on foreman",
        rule_id="pr_review_requested_foreman",
    )
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "allow" in lines[0]
    assert "pr_review_requested_foreman" in lines[0]


def test_deny_does_not_publish(tmp_path: Path):
    connector = FakeConnector()  # no allow rule configured → defaults to Deny
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    assert bus.published == []


def test_deny_writes_audit_line(tmp_path: Path):
    connector = FakeConnector()  # no allow rule configured → defaults to Deny
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "deny" in lines[0]


def test_unknown_connector_raises(tmp_path: Path):
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=FakeConnector(), bus=bus)

    with pytest.raises(KeyError, match="unknown connector"):
        router.receive(
            connector_name="never-registered",
            target_being="wren",
            event=_event(),
        )
