"""Reader: iter_envelopes — raw round-trip + time bounds + tolerant parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload
from agent_core.bus_log.reader import iter_envelopes


def _text_env(eid: str, *, created: datetime, frm: str = "src", to: str = "pepper") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text=f"text-{eid}"),
        created_at=created,
    )


def _event_env(eid: str, *, created: datetime, event_type: str, data: dict) -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_="src",
        to="pepper",
        kind="Event",
        payload=EventPayload(type=event_type, data=data),
        created_at=created,
    )


def _write_jsonl(path: Path, envelopes: list[Envelope]) -> None:
    lines = [env.model_dump_json(by_alias=True) for env in envelopes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_iter_envelopes_round_trips_text_and_event(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("e1", created=base),
        _event_env("e2", created=base + timedelta(seconds=1), event_type="HandoffReady",
                   data={"job_id": "j-1", "handoff_path": "/x/handoff.md"}),
    ]
    path = tmp_path / "2026-05-03.jsonl"
    _write_jsonl(path, envs)
    parsed = list(iter_envelopes(path))
    assert len(parsed) == 2
    assert parsed[0].id == "e1"
    assert parsed[0].kind == "TextMessage"
    assert parsed[0].payload.text == "text-e1"
    assert parsed[1].id == "e2"
    assert parsed[1].kind == "Event"
    assert parsed[1].payload.type == "HandoffReady"
    assert parsed[1].payload.data["job_id"] == "j-1"


def test_iter_envelopes_returns_empty_for_missing_file(tmp_path: Path):
    parsed = list(iter_envelopes(tmp_path / "nonexistent.jsonl"))
    assert parsed == []


def test_iter_envelopes_skips_blank_and_malformed_lines(tmp_path: Path, caplog):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    good = _text_env("e1", created=base)
    path = tmp_path / "day.jsonl"
    path.write_text(
        good.model_dump_json(by_alias=True) + "\n"
        + "\n"  # blank line
        + "{not valid json\n"
        + "\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        parsed = list(iter_envelopes(path))
    assert len(parsed) == 1
    assert parsed[0].id == "e1"
    # Malformed line was reported (operator visibility), not silently ignored.
    assert any("malformed" in r.message.lower() for r in caplog.records)


def test_iter_envelopes_filters_by_time_range(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", created=base),
        _text_env("b", created=base + timedelta(seconds=10)),
        _text_env("c", created=base + timedelta(seconds=20)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    parsed = list(iter_envelopes(path, since=base + timedelta(seconds=5), until=base + timedelta(seconds=15)))
    assert [e.id for e in parsed] == ["b"]


def test_iter_envelopes_inclusive_since_exclusive_until(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", created=base),
        _text_env("b", created=base + timedelta(seconds=5)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    parsed = list(iter_envelopes(path, since=base, until=base + timedelta(seconds=5)))
    # since is inclusive (a included), until is exclusive (b excluded).
    assert [e.id for e in parsed] == ["a"]


def test_iter_envelopes_accepts_naive_bounds_without_typeerror(tmp_path: Path):
    """Bounds passed as naive datetimes (no tzinfo) must not raise
    TypeError when compared against tz-aware envelope timestamps. The
    reader coerces both sides to UTC before comparison.

    Real callers (Typer's --since flag, MCP tool args) may pass naive
    datetimes if the caller forgets a timezone — we should accept and
    treat them as UTC, not crash."""
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", created=base),
        _text_env("b", created=base + timedelta(seconds=10)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    naive_since = datetime(2026, 5, 3, 12, 0, 5)  # no tzinfo — coerced to UTC
    parsed = list(iter_envelopes(path, since=naive_since))
    assert [e.id for e in parsed] == ["b"]


def test_iter_envelopes_yields_empty_when_all_lines_malformed(tmp_path: Path, caplog):
    """A day file containing only malformed lines yields nothing and logs
    one WARNING per bad line. Operator visibility for the 'your day was
    100% corrupted' failure mode — should never crash the reflection
    pipeline."""
    path = tmp_path / "all-bad.jsonl"
    path.write_text("{not json}\n{also not}\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        parsed = list(iter_envelopes(path))
    assert parsed == []
    malformed_warnings = [r for r in caplog.records if "malformed" in r.message.lower()]
    assert len(malformed_warnings) == 2
