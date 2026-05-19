"""CLI: agent-core bus-log show."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.cli import app


@pytest.fixture
def sample_log(tmp_path: Path) -> Path:
    """Build a small daily file with two envelopes Pepper participates in
    and one she doesn't."""
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    envs = [
        Envelope(
            id="a", correlation_id="ca", from_="discord", to="pepper",
            kind="TextMessage", payload=TextMessagePayload(text="hi"),
            created_at=base,
        ),
        Envelope(
            id="b", correlation_id="cb", from_="pepper", to="discord",
            kind="TextMessage", payload=TextMessagePayload(text="reply"),
            created_at=base,
        ),
        Envelope(
            id="c", correlation_id="cc", from_="vale", to="discord",
            kind="TextMessage", payload=TextMessagePayload(text="not pepper"),
            created_at=base,
        ),
    ]
    log_root = tmp_path / "raw"
    log_root.mkdir()
    target = log_root / "2026-05-03.jsonl"
    target.write_text(
        "\n".join(env.model_dump_json(by_alias=True) for env in envs) + "\n",
        encoding="utf-8",
    )
    return log_root


def test_bus_log_show_requires_agent(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
    ])
    assert result.exit_code == 2
    assert "Missing option" in result.output
    assert "--agent" in result.output


def test_bus_log_show_projected_default_filters_to_agent(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--timezone", "UTC",
    ])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    assert len(rows) == 2  # a (in) and b (out); c excluded
    assert {r["cid"] for r in rows} == {"ca", "cb"}


def test_bus_log_show_raw_outputs_full_envelopes(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--raw",
    ])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == 2
    # Raw envelopes have id/from/to/kind, NOT the projected ts/dir/sender shape.
    assert all("kind" in p for p in parsed)
    assert all("from" in p for p in parsed)


def test_bus_log_show_limit_returns_last_n(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--limit", "1",
    ])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["cid"] == "cb"


def test_bus_log_show_missing_file_yields_no_output(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(tmp_path),  # empty
    ])
    assert result.exit_code == 0
    # Empty stdout is the right behavior: a quiet day, not an error.
    assert result.output.strip() == ""


def test_bus_log_show_default_date_uses_configured_timezone(sample_log: Path, monkeypatch):
    """Default --date is today in --timezone, matching the writer's local-tz
    rollover. At 02:00 UTC on May 4, an Eastern operator's 'today' is May 3
    (21:30 EDT May 3 = 01:30 UTC May 4). The CLI default must read the May 3
    file, not May 4 — otherwise it shows nothing despite minutes-old traffic."""
    import agent_core.bus_log.cli as cli_module

    class _FakeDatetime:
        @staticmethod
        def now(tz):
            # 02:00 UTC May 4 — Eastern is still May 3 (22:00 EDT May 3).
            return datetime(2026, 5, 4, 2, 0, 0, tzinfo=UTC)

    # Patch only datetime.now usage in the CLI module.
    monkeypatch.setattr(cli_module, "datetime", _FakeDatetime)

    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--log-root", str(sample_log),
        "--timezone", "US/Eastern",
    ])
    assert result.exit_code == 0
    # The fixture's file is "2026-05-03.jsonl" — the default date must resolve to it.
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2  # the May 3 file's pepper rows


def test_bus_log_show_rejects_invalid_timezone(sample_log: Path):
    """A typo'd --timezone is a CLI boundary error — surface it loudly,
    don't silently produce empty output via swallowed projector errors."""
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--timezone", "Atlantis/Lemuria",
    ])
    assert result.exit_code != 0
    assert "Atlantis/Lemuria" in result.output or "timezone" in result.output.lower()


def test_bus_log_show_rejects_limit_zero(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-05-03",
        "--log-root", str(sample_log),
        "--limit", "0",
    ])
    assert result.exit_code != 0
    assert "limit" in result.output.lower()


def test_bus_log_show_rejects_invalid_date_format(sample_log: Path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "bus-log", "show",
        "--agent", "pepper",
        "--date", "2026-13-99",
        "--log-root", str(sample_log),
    ])
    assert result.exit_code != 0
    assert "date" in result.output.lower() or "2026-13-99" in result.output
