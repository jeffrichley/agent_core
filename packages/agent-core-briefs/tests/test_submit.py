"""Tests for ``submit_brief`` — atomic validate + format + send + audit.

These tests cover the full submit flow: validation rejection (no
deliveries attempted, token preserved), validation success (token
consumed, every destination called best-effort), and the audit-log
shape under each path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.audit import AuditLog
from agent_core_briefs.protocol import (
    DeliveryResult,
    FieldSpec,
    PlaybookRef,
    SectionSpec,
)
from agent_core_briefs.session import (
    ComposeSession,
    SessionConsumedError,
    SessionNotFoundError,
    SessionRegistry,
)
from agent_core_briefs.submit import (
    DestinationOutcome,
    SubmitResult,
    submit_brief,
)

# -- test fixtures --------------------------------------------------------


class _RecordingDestination:
    """Stub Destination that records the deliver() call and returns
    a configurable DeliveryResult.
    """

    def __init__(self, type_id: str, *, result: DeliveryResult | None = None):
        self.type_id = type_id
        self._result = result or DeliveryResult(success=True, ref=f"ref-{type_id}")
        self.calls: list[dict] = []

    async def deliver(
        self,
        sections,
        playbook,
        scope,
        when,
        config,
        bus_handle,
    ) -> DeliveryResult:
        self.calls.append(
            {
                "sections": sections,
                "playbook": playbook,
                "scope": scope,
                "when": when,
                "config": config,
                "bus_handle": bus_handle,
            }
        )
        return self._result


class _RaisingDestination:
    """Destination whose ``deliver`` raises — submit should still continue."""

    type_id = "raising"

    async def deliver(self, sections, playbook, scope, when, config, bus_handle):
        raise RuntimeError("boom in destination")


class _StubBusHandle:
    """Sentinel bus handle — submit just threads it through to ``deliver``."""

    pass


def _make_session(
    *,
    sections: list[SectionSpec] | None = None,
    sections_required: list[str] | None = None,
    destinations: list[dict] | None = None,
) -> ComposeSession:
    return ComposeSession(
        brief_type="morning_brief",
        playbook_path=Path("/playbooks/morning.md"),
        voice="test",
        scope="today",
        when=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        context={},
        sections_required=sections_required or [],
        sections_optional=[],
        sections_conditional_active=[],
        target_agent="agent",
        correlation_id="c1",
        metadata={},
        sections=sections or [],
        colors_palette={},
        created_at=datetime.now(UTC),
        destinations=destinations or [],
    )


def _section(section_id: str) -> SectionSpec:
    return SectionSpec(
        section_id=section_id,
        title=section_id.title(),
        color=15548997,
        required=True,
        fields=[FieldSpec(name="Today", required=True, max_chars=500)],
    )


def _submitted(section_id: str, *, value: str = "Hi.") -> dict:
    return {
        "section_id": section_id,
        "title": section_id.title(),
        "color": 15548997,
        "fields": [{"name": "Today", "value": value}],
    }


def _read_audit_lines(audit: AuditLog) -> list[dict]:
    if not audit.path.exists():
        return []
    return [
        json.loads(line) for line in audit.path.read_text(encoding="utf-8").splitlines() if line
    ]


# -- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_with_no_validation_issues_attempts_all_destinations(tmp_path: Path):
    """Valid submission → every destination's deliver() is called."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[
            {"type": "discord_embed", "config": {"channel_id": "123"}},
            {"type": "markdown_file", "config": {"path": str(tmp_path / "out.md")}},
        ],
    )
    token = registry.create(session)

    discord = _RecordingDestination("discord_embed", result=DeliveryResult(True, ref="env-1"))
    markdown = _RecordingDestination("markdown_file", result=DeliveryResult(True, ref="/p/out.md"))
    factories = {
        "discord_embed": lambda: discord,
        "markdown_file": lambda: markdown,
    }

    audit = AuditLog(tmp_path / "audit.jsonl")
    result = await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    assert isinstance(result, SubmitResult)
    assert result.success is True
    assert result.validation_issues == []
    assert len(result.deliveries) == 2
    assert all(d.success for d in result.deliveries)
    # Both destinations were actually called.
    assert len(discord.calls) == 1
    assert len(markdown.calls) == 1
    # Each destination got a PlaybookRef.
    assert isinstance(discord.calls[0]["playbook"], PlaybookRef)
    assert discord.calls[0]["playbook"].brief_type == "morning_brief"
    # Per-destination config was threaded through.
    assert discord.calls[0]["config"] == {"channel_id": "123"}


@pytest.mark.asyncio
async def test_submit_with_validation_issues_skips_delivery(tmp_path: Path):
    """Invalid submission → no destination is called, no token consumption."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[{"type": "discord_embed", "config": {}}],
    )
    token = registry.create(session)
    discord = _RecordingDestination("discord_embed")
    factories = {"discord_embed": lambda: discord}
    audit = AuditLog(tmp_path / "audit.jsonl")

    # Submission omits the required section entirely.
    result = await submit_brief(
        registry=registry,
        token=token,
        sections=[],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    assert result.success is False
    assert result.validation_issues, "expected at least one validation issue"
    assert result.deliveries == []
    assert discord.calls == [], "destinations must not be invoked on validation failure"

    # Token still active — the agent can retry.
    again = registry.get(token)
    assert again is session


@pytest.mark.asyncio
async def test_submit_consumes_session_token_after_validation(tmp_path: Path):
    """After successful validation, the session token is consumed (one-shot)."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[{"type": "discord_embed", "config": {}}],
    )
    token = registry.create(session)

    factories = {"discord_embed": lambda: _RecordingDestination("discord_embed")}
    audit = AuditLog(tmp_path / "audit.jsonl")

    await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    # Token now consumed — second lookup raises.
    with pytest.raises(SessionConsumedError):
        registry.get(token)


@pytest.mark.asyncio
async def test_submit_propagates_session_not_found(tmp_path: Path):
    """Bad token → SessionNotFoundError propagates from registry.get."""
    registry = SessionRegistry()
    audit = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(SessionNotFoundError):
        await submit_brief(
            registry=registry,
            token="deadbeef" * 4,
            sections=[],
            destination_factories={},
            bus_handle=_StubBusHandle(),
            audit_log=audit,
        )


@pytest.mark.asyncio
async def test_submit_propagates_session_consumed(tmp_path: Path):
    """Already-consumed token → SessionConsumedError propagates."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(sections=[spec], sections_required=["greeting"])
    token = registry.create(session)
    registry.consume(token)

    audit = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(SessionConsumedError):
        await submit_brief(
            registry=registry,
            token=token,
            sections=[_submitted("greeting")],
            destination_factories={},
            bus_handle=_StubBusHandle(),
            audit_log=audit,
        )


# -- best-effort fan-out --------------------------------------------------


@pytest.mark.asyncio
async def test_submit_one_destination_failure_doesnt_block_others(tmp_path: Path):
    """A failing destination must not stop subsequent destinations from running."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[
            {"type": "raising", "config": {}},
            {"type": "good", "config": {}},
        ],
    )
    token = registry.create(session)
    good = _RecordingDestination("good", result=DeliveryResult(True, ref="g-1"))
    factories = {
        "raising": _RaisingDestination,
        "good": lambda: good,
    }
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    # The "good" destination still ran.
    assert len(good.calls) == 1
    assert len(result.deliveries) == 2
    by_type = {d.destination_type: d for d in result.deliveries}
    assert by_type["raising"].success is False
    assert by_type["raising"].error is not None
    assert by_type["good"].success is True
    assert result.success is True  # at least one succeeded


@pytest.mark.asyncio
async def test_submit_unknown_destination_type_recorded_as_failure(tmp_path: Path):
    """A destination type not in the factory map → failure outcome captured."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[{"type": "ghost_destination", "config": {}}],
    )
    token = registry.create(session)
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories={},  # no factories registered
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    assert len(result.deliveries) == 1
    only = result.deliveries[0]
    assert only.destination_type == "ghost_destination"
    assert only.success is False
    assert "unknown destination type" in (only.error or "")
    assert result.success is False


@pytest.mark.asyncio
async def test_submit_returns_success_when_at_least_one_destination_succeeds(tmp_path: Path):
    """Mixed outcomes → SubmitResult.success is True iff any destination succeeded."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[
            {"type": "fail", "config": {}},
            {"type": "ok", "config": {}},
        ],
    )
    token = registry.create(session)

    factories = {
        "fail": lambda: _RecordingDestination("fail", result=DeliveryResult(False, error="nope")),
        "ok": lambda: _RecordingDestination("ok", result=DeliveryResult(True, ref="r")),
    }
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_submit_returns_failure_when_all_destinations_fail(tmp_path: Path):
    """Every destination failing → SubmitResult.success is False."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[
            {"type": "fail1", "config": {}},
            {"type": "fail2", "config": {}},
        ],
    )
    token = registry.create(session)
    factories = {
        "fail1": lambda: _RecordingDestination("fail1", result=DeliveryResult(False, error="x")),
        "fail2": lambda: _RecordingDestination("fail2", result=DeliveryResult(False, error="y")),
    }
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    assert result.success is False
    assert all(not d.success for d in result.deliveries)


# -- audit log shape ------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_writes_three_audit_events_on_success(tmp_path: Path):
    """Two destinations → one submit.deliver per dest + one submit.complete."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[
            {"type": "a", "config": {}},
            {"type": "b", "config": {}},
        ],
    )
    token = registry.create(session)
    factories = {
        "a": lambda: _RecordingDestination("a"),
        "b": lambda: _RecordingDestination("b"),
    }
    audit = AuditLog(tmp_path / "audit.jsonl")

    await submit_brief(
        registry=registry,
        token=token,
        sections=[_submitted("greeting")],
        destination_factories=factories,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    events = _read_audit_lines(audit)
    types = [e["event_type"] for e in events]
    # Two deliveries, then a complete summary.
    assert types == ["submit.deliver", "submit.deliver", "submit.complete"]
    # Token is truncated to 8 chars in every event.
    for event in events:
        assert len(event["session_token"]) == 8
        assert event["session_token"] == token[:8]
        assert event["brief_type"] == "morning_brief"
        assert event["target_agent"] == "agent"
    # Complete summary has the totals.
    final = events[-1]
    assert final["data"]["total_destinations"] == 2
    assert final["data"]["successful"] == 2
    assert final["data"]["failed"] == 0
    assert final["data"]["overall_success"] is True


@pytest.mark.asyncio
async def test_submit_writes_one_audit_event_on_validation_failure(tmp_path: Path):
    """Validation failure → exactly one ``submit.validate.fail`` event, no others."""
    registry = SessionRegistry()
    spec = _section("greeting")
    session = _make_session(
        sections=[spec],
        sections_required=["greeting"],
        destinations=[{"type": "a", "config": {}}],
    )
    token = registry.create(session)
    audit = AuditLog(tmp_path / "audit.jsonl")

    await submit_brief(
        registry=registry,
        token=token,
        sections=[],  # missing required section
        destination_factories={"a": lambda: _RecordingDestination("a")},
        bus_handle=_StubBusHandle(),
        audit_log=audit,
    )

    events = _read_audit_lines(audit)
    assert [e["event_type"] for e in events] == ["submit.validate.fail"]
    payload = events[0]
    assert payload["data"]["issue_count"] >= 1
    assert all("code" in i and "message" in i for i in payload["data"]["issues"])


@pytest.mark.asyncio
async def test_submit_outcome_is_frozen_dataclass():
    """DestinationOutcome / SubmitResult immutable post-return."""
    import dataclasses

    o = DestinationOutcome(destination_type="x", success=True)
    r = SubmitResult(success=True, validation_issues=[], deliveries=[o])
    assert dataclasses.is_dataclass(o)
    assert dataclasses.is_dataclass(r)
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.success = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.success = False  # type: ignore[misc]
