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
