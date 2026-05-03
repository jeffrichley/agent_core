"""iter_for_agent: filter envelopes by perspective and project to Tool 3 rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.bus_log.projectors import (
    TextMessageProjector,
    register_projector,
    reset_registry,
)
from agent_core.bus_log.reader import iter_for_agent


@pytest.fixture(autouse=True)
def _registry_with_text_message():
    reset_registry()
    register_projector("TextMessage", TextMessageProjector())
    yield
    reset_registry()


def _text_env(eid: str, *, frm: str, to: str, created: datetime, text: str = "x") -> Envelope:
    return Envelope(
        id=eid,
        correlation_id=f"c-{eid}",
        from_=frm,
        to=to,
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        created_at=created,
    )


def _write_jsonl(path: Path, envelopes: list[Envelope]) -> None:
    path.write_text(
        "\n".join(env.model_dump_json(by_alias=True) for env in envelopes) + "\n",
        encoding="utf-8",
    )


def test_filter_includes_envelopes_where_agent_is_to(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="discord", to="pepper", created=base),
        _text_env("b", frm="discord", to="vale", created=base),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper"))
    assert len(rows) == 1
    assert rows[0]["cid"] == "c-a"
    assert rows[0]["dir"] == "in"


def test_filter_includes_envelopes_where_agent_is_from(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="pepper", to="discord", created=base),  # outbound
        _text_env("b", frm="vale", to="discord", created=base),    # not pepper
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper"))
    assert len(rows) == 1
    assert rows[0]["cid"] == "c-a"
    assert rows[0]["dir"] == "out"


def test_filter_excludes_envelopes_touching_neither(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [_text_env("a", frm="vale", to="discord", created=base)]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper"))
    assert rows == []


def test_filter_includes_self_loop_envelope_once_with_dir_self(tmp_path: Path):
    """A self-loop envelope (from_=to=agent) must appear exactly once with dir='self'.
    It must not be deduped (dropped) or double-yielded by the from_/to filter."""
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    env = _text_env("a", frm="pepper", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows = list(iter_for_agent(path, agent="pepper"))
    assert len(rows) == 1
    assert rows[0]["dir"] == "self"


def test_projected_default_returns_tool3_rows(tmp_path: Path):
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base, text="hi")
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows = list(iter_for_agent(path, agent="pepper", timezone="US/Eastern"))
    assert len(rows) == 1
    assert rows[0] == {
        "ts": "2026-05-03T13:42:13-04:00",
        "dir": "in",
        "src": "discord",
        "cid": "c-e1",
        "sender": "discord",
        "content": "hi",
    }


def test_projected_false_yields_envelope_objects(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    items = list(iter_for_agent(path, agent="pepper", projected=False))
    assert len(items) == 1
    assert isinstance(items[0], Envelope)
    assert items[0].id == "e1"


def test_projector_returning_none_drops_envelope_in_projected_mode(tmp_path: Path):
    """A projector that returns None means 'do not include in summary'.
    Envelope is still in the raw stream — we just skip it during projection."""
    class _SkipAll:
        def render(self, envelope, *, perspective, timezone):
            return None

    register_projector("TextMessage", _SkipAll())

    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows = list(iter_for_agent(path, agent="pepper"))
    assert rows == []
    raw = list(iter_for_agent(path, agent="pepper", projected=False))
    assert len(raw) == 1


def test_iter_for_agent_passes_timezone_through_to_projector(tmp_path: Path):
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    env = _text_env("e1", frm="discord", to="pepper", created=base)
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, [env])
    rows_utc = list(iter_for_agent(path, agent="pepper", timezone="UTC"))
    rows_eastern = list(iter_for_agent(path, agent="pepper", timezone="US/Eastern"))
    assert rows_utc[0]["ts"] == "2026-05-03T17:42:13+00:00"
    assert rows_eastern[0]["ts"] == "2026-05-03T13:42:13-04:00"


def test_iter_for_agent_honors_time_range(tmp_path: Path):
    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="discord", to="pepper", created=base),
        _text_env("b", frm="discord", to="pepper", created=base + timedelta(seconds=10)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    rows = list(iter_for_agent(path, agent="pepper", since=base + timedelta(seconds=5)))
    assert len(rows) == 1
    assert rows[0]["cid"] == "c-b"


def test_projector_raising_does_not_poison_iteration(tmp_path: Path, caplog):
    """A projector that raises must be logged + skipped — one bad envelope
    does not crash the day's reflection. Mirrors iter_envelopes' tolerance
    for malformed JSON lines."""
    class _Boom:
        def render(self, envelope, *, perspective, timezone):
            raise RuntimeError("projector boom")

    register_projector("TextMessage", _Boom())

    base = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    envs = [
        _text_env("a", frm="discord", to="pepper", created=base),
        _text_env("b", frm="discord", to="pepper", created=base + timedelta(seconds=1)),
    ]
    path = tmp_path / "day.jsonl"
    _write_jsonl(path, envs)
    with caplog.at_level("WARNING"):
        rows = list(iter_for_agent(path, agent="pepper"))
    # Both envelopes were filtered through and both projector calls raised; rows is empty.
    assert rows == []
    # Operator got TWO warnings — one per failed projector call.
    boom_warnings = [r for r in caplog.records if "projector" in r.message.lower()]
    assert len(boom_warnings) == 2
