"""DailyRawJsonlHook: write side. Append-only JSONL of bus envelopes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus_hooks.daily_raw_jsonl import DailyRawJsonlHook
from agent_core.bus_log.reader import iter_envelopes


def _text_env(eid="e1", *, urgency="green", text="hello") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        urgency=urgency,
        created_at=datetime.now(UTC),
    )


def _ack_env() -> Envelope:
    return Envelope(
        id="ack-1",
        correlation_id="c-ack-1",
        from_="discord",
        to="pepper",
        kind="Acknowledgment",
        payload=AcknowledgmentPayload(of="other-id"),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_hook_appends_text_envelope_to_dated_file(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    env = _text_env()
    out = await hook.execute(stage="pre_publish", envelope=env, params={})
    assert out is env  # never modifies envelope flow
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    written = list(iter_envelopes(files[0]))
    assert len(written) == 1
    assert written[0].id == "e1"
    assert written[0].payload.text == "hello"


@pytest.mark.asyncio
async def test_hook_round_trips_event_envelope_with_payload_data(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    env = Envelope(
        id="h1",
        correlation_id="c-h1",
        from_="handoff-jobs",
        to="pepper",
        kind="Event",
        payload=EventPayload(
            type="HandoffReady",
            data={"job_id": "j-1", "handoff_path": "/x/handoff.md"},
        ),
        created_at=datetime.now(UTC),
    )
    await hook.execute(stage="pre_publish", envelope=env, params={})
    files = list(tmp_path.glob("*.jsonl"))
    written = list(iter_envelopes(files[0]))
    assert len(written) == 1
    assert written[0].payload.type == "HandoffReady"
    assert written[0].payload.data["job_id"] == "j-1"
    assert written[0].payload.data["handoff_path"] == "/x/handoff.md"
    # Confirm the on-disk shape uses the "from" alias, not the field
    # name "from_". Read-side parses either because populate_by_name=True,
    # but external tools and cross-language consumers expect the alias.
    raw = files[0].read_text(encoding="utf-8")
    assert '"from":' in raw
    assert '"from_":' not in raw


@pytest.mark.asyncio
async def test_hook_skips_acknowledgment_by_default(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    out = await hook.execute(stage="pre_publish", envelope=_ack_env(), params={})
    assert out is not None
    assert out.id == "ack-1"
    # No file created (nothing was written).
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_hook_can_be_configured_to_log_acks(tmp_path: Path):
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC", skip_kinds=[])
    await hook.execute(stage="pre_publish", envelope=_ack_env(), params={})
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    written = list(iter_envelopes(files[0]))
    assert len(written) == 1
    assert written[0].kind == "Acknowledgment"


@pytest.mark.asyncio
async def test_hook_only_writes_at_pre_publish(tmp_path: Path):
    """pre_deliver fires per delivery attempt; logging there would
    duplicate on redelivery. The hook must no-op for pre_deliver."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    out = await hook.execute(stage="pre_deliver", envelope=_text_env(), params={})
    assert out.id == "e1"
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_hook_does_not_abort_publish_on_oserror(tmp_path: Path, monkeypatch, caplog):
    """A write failure must not raise — bus is source of truth, log is observability."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path / "does-not-exist-and-cannot-be-created"), timezone="UTC")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("agent_core.bus_hooks.daily_raw_jsonl._writer.append_envelope_jsonl", _boom)

    with caplog.at_level("ERROR"):
        out = await hook.execute(stage="pre_publish", envelope=_text_env(), params={})
    assert out.id == "e1"  # envelope flow continues
    assert any("daily_raw_jsonl" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_hook_uses_iso_date_filename_in_configured_timezone(tmp_path: Path):
    """The dated filename must reflect the operator's configured timezone,
    not UTC. A 23:30 UTC publish on 2026-05-03 lands in 2026-05-03 in
    Eastern (19:30 EDT) and 2026-05-03 in UTC — pick a clearer case:
    01:30 UTC May 4 == 21:30 EDT May 3 — Eastern file is May 3."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="US/Eastern")
    env = Envelope(
        id="e-tz",
        correlation_id="c-tz",
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="x"),
        created_at=datetime(2026, 5, 4, 1, 30, 0, tzinfo=UTC),  # 21:30 EDT May 3
    )
    await hook.execute(stage="pre_publish", envelope=env, params={})
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].name == "2026-05-03.jsonl"  # Eastern date


@pytest.mark.asyncio
async def test_hook_creates_log_root_if_missing(tmp_path: Path):
    nested = tmp_path / "nested" / "deeper" / "raw"
    hook = DailyRawJsonlHook(log_root=str(nested), timezone="UTC")
    await hook.execute(stage="pre_publish", envelope=_text_env(), params={})
    assert nested.exists()
    assert any(nested.glob("*.jsonl"))


@pytest.mark.asyncio
async def test_hook_writes_lf_only_terminators_on_all_platforms(tmp_path: Path):
    """JSONL spec requires \\n line terminators. With Python's default
    text-mode newline translation, Windows would write \\r\\n. The writer
    opens with newline='' to disable translation. Cross-machine sync
    (Windows desktop ↔ Mac laptop) and external tooling rely on \\n."""
    hook = DailyRawJsonlHook(log_root=str(tmp_path), timezone="UTC")
    await hook.execute(stage="pre_publish", envelope=_text_env(), params={})
    files = list(tmp_path.glob("*.jsonl"))
    raw_bytes = files[0].read_bytes()
    assert b"\r\n" not in raw_bytes
    assert raw_bytes.endswith(b"\n")
