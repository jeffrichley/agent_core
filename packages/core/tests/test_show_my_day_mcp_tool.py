"""MCP tool ``show_my_day``: agent-scoped day view."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


@pytest.fixture
def sample_log(tmp_path: Path) -> Path:
    base = datetime(2026, 5, 3, 17, 42, 13, tzinfo=UTC)
    envs = [
        Envelope(
            id="a", correlation_id="ca", from_="discord", to="pepper",
            kind="TextMessage", payload=TextMessagePayload(text="hi"),
            created_at=base,
        ),
        Envelope(
            id="b", correlation_id="cb", from_="discord", to="vale",
            kind="TextMessage", payload=TextMessagePayload(text="not pepper"),
            created_at=base,
        ),
    ]
    root = tmp_path / "raw"
    root.mkdir()
    (root / "2026-05-03.jsonl").write_text(
        "\n".join(e.model_dump_json(by_alias=True) for e in envs) + "\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_show_my_day_returns_only_calling_agents_traffic(sample_log: Path):
    """Endpoint named ``pepper`` returns Pepper's perspective ONLY.
    There is no agent= argument; the agent identity comes from self.name."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-05-03", projected=True)
    assert len(rows) == 1
    assert rows[0]["cid"] == "ca"
    assert rows[0]["dir"] == "in"


@pytest.mark.asyncio
async def test_show_my_day_returns_empty_when_no_log_for_date(sample_log: Path):
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-04-01", projected=True)
    assert rows == []


@pytest.mark.asyncio
async def test_show_my_day_limit_returns_last_n(sample_log: Path, tmp_path: Path):
    """Add a second pepper-touching envelope and verify limit=1 returns the latest."""
    extra = Envelope(
        id="z", correlation_id="cz", from_="pepper", to="discord",
        kind="TextMessage", payload=TextMessagePayload(text="reply"),
        created_at=datetime(2026, 5, 3, 17, 50, 0, tzinfo=UTC),
    )
    target = sample_log / "2026-05-03.jsonl"
    target.write_text(
        target.read_text(encoding="utf-8") + extra.model_dump_json(by_alias=True) + "\n",
        encoding="utf-8",
    )
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-05-03", projected=True, limit=1)
    assert len(rows) == 1
    assert rows[0]["cid"] == "cz"  # the latest


@pytest.mark.asyncio
async def test_show_my_day_default_log_root_when_none_given():
    """Constructor accepts None and falls back to ~/.agent-core/bus/raw."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    from agent_core.bus_log.writer import default_log_root
    assert ep._bus_log_root == default_log_root()


@pytest.mark.asyncio
async def test_show_my_day_raw_returns_envelope_dicts(sample_log: Path):
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(date="2026-05-03", projected=False)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"
    assert "kind" in rows[0]


@pytest.mark.asyncio
async def test_show_my_day_cannot_be_coerced_to_other_agent(sample_log: Path):
    """The tool has no `agent` parameter; vale's endpoint sees only vale's
    traffic regardless of how the call is made."""
    vale = ClaudeCodeMCPEndpoint(name="vale", mount="/mcp/vale", bus_log_root=sample_log)
    rows = await vale._show_my_day_impl(date="2026-05-03", projected=True)
    assert len(rows) == 1
    assert rows[0]["cid"] == "cb"


@pytest.mark.asyncio
async def test_show_my_day_default_date_uses_configured_timezone(sample_log: Path, monkeypatch):
    """Default date resolves in the impl's timezone, not UTC, matching the
    writer's rollover. At 02:00 UTC May 4, an Eastern operator's 'today' is
    May 3 — the impl must read the May 3 file, not May 4 (or it returns
    empty despite minutes-old traffic)."""
    import agent_core.endpoints.claude_code_mcp as mcp_module

    class _FakeDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 5, 4, 2, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(mcp_module, "datetime", _FakeDatetime)

    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    rows = await ep._show_my_day_impl(projected=True, timezone="US/Eastern")
    # Fixture file is "2026-05-03.jsonl"; default date must resolve to it.
    assert len(rows) == 1
    assert rows[0]["cid"] == "ca"


@pytest.mark.asyncio
async def test_show_my_day_rejects_limit_zero_or_negative(sample_log: Path):
    """limit must be >= 1; 0 and negatives are caller bugs."""
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper", bus_log_root=sample_log)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await ep._show_my_day_impl(date="2026-05-03", projected=True, limit=0)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await ep._show_my_day_impl(date="2026-05-03", projected=True, limit=-3)
