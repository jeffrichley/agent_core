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


def test_redelivered_event_is_deduped_and_not_republished(tmp_path: Path):
    connector = FakeConnector()
    connector.allow(
        event_id="evt-redeliver",
        target_being="wren",
        tier=Tier.RED,
        reason="PR review requested on foreman",
        rule_id="r1",
    )
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    ev = ConnectorEvent(
        event_id="evt-redeliver",
        landed_at=_stamp(),
        raw={"x": 1},
    )

    router.receive(connector_name="fake", target_being="wren", event=ev)
    router.receive(connector_name="fake", target_being="wren", event=ev)
    router.receive(connector_name="fake", target_being="wren", event=ev)

    # First delivery publishes; the next two are dropped by de-dupe.
    assert len(bus.published) == 1
    assert len(connector.classify_calls) == 1  # connector not re-called for dupes


def test_dedupe_is_scoped_to_target_being(tmp_path: Path):
    # Same event_id, different target_being → distinct entries. This is
    # the gmail-fan-out shape (one email reaches both Pepper and Jeff)
    # so the router cannot collapse them.
    connector = FakeConnector()
    connector.allow(event_id="evt-shared", target_being="wren", reason="r")
    connector.allow(event_id="evt-shared", target_being="pepper", reason="r")
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    ev = ConnectorEvent(event_id="evt-shared", landed_at=_stamp(), raw={})

    router.receive(connector_name="fake", target_being="wren", event=ev)
    router.receive(connector_name="fake", target_being="pepper", event=ev)

    assert len(bus.published) == 2
    assert {p["to"] for p in bus.published} == {"wren", "pepper"}


def test_lru_evicts_oldest_when_capacity_exceeded(tmp_path: Path):
    connector = FakeConnector()
    for evt_id in ("a", "b", "c"):
        connector.allow(event_id=evt_id, target_being="wren", reason="r")
    bus = _FakeBus()
    audit = AuditLog(path=tmp_path / "audit.jsonl", clock=lambda: _stamp())
    router = Router(
        connectors={"fake": connector},
        bus_publish=bus.publish,
        audit=audit,
        clock=lambda: _stamp(),
        dedupe_capacity=2,
    )
    for evt_id in ("a", "b", "c"):
        router.receive(
            connector_name="fake",
            target_being="wren",
            event=ConnectorEvent(event_id=evt_id, landed_at=_stamp(), raw={}),
        )
    # After three deliveries with capacity 2, the cache holds {b, c} and
    # "a" was evicted. Check the still-cached entry FIRST so we don't
    # mutate the LRU state before the eviction assertion.
    #
    # "b" is still cached — re-delivering "b" should be deduped.
    router.receive(
        connector_name="fake",
        target_being="wren",
        event=ConnectorEvent(event_id="b", landed_at=_stamp(), raw={}),
    )
    assert len(bus.published) == 3
    # "a" was evicted when "c" landed — re-delivering "a" should publish again.
    router.receive(
        connector_name="fake",
        target_being="wren",
        event=ConnectorEvent(event_id="a", landed_at=_stamp(), raw={}),
    )
    assert len(bus.published) == 4


def test_deny_is_not_cached_so_policy_flip_takes_effect(tmp_path: Path):
    # Verifies Deny verdicts do NOT populate the de-dupe cache, so a
    # subsequent connector policy edit (Deny -> Allow on the same
    # event_id) takes effect on the next delivery.
    connector = FakeConnector()  # nothing configured → first call Denies
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)
    event = ConnectorEvent(event_id="policy-flip", landed_at=_stamp(), raw={})

    router.receive(connector_name="fake", target_being="wren", event=event)
    assert bus.published == []  # Denied, not published

    # Operator updates connector policy to allow the event.
    connector.allow(
        event_id="policy-flip",
        target_being="wren",
        tier=Tier.GREEN,
        reason="newly allowed",
        rule_id="r1",
    )

    # Same event re-arrives. De-dupe must NOT short-circuit the new policy.
    router.receive(connector_name="fake", target_being="wren", event=event)
    assert len(bus.published) == 1
    assert bus.published[0]["urgency"] == "green"
