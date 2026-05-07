"""MCPAuditMiddleware: per-tools/call audit emission via FastMCP middleware."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from agent_core.mcp_audit.middleware import MCPAuditMiddleware
from agent_core.mcp_audit.writer import MCPAuditWriter


def _build_server(
    *,
    name: str,
    writer: MCPAuditWriter,
    skip_tools: frozenset[str] = frozenset(),
) -> FastMCP:
    mcp = FastMCP(name)

    @mcp.tool()
    async def echo(text: str) -> str:
        return text

    @mcp.tool()
    async def add(a: int, b: int) -> int:
        return a + b

    @mcp.tool()
    async def boom() -> str:
        raise RuntimeError("kaboom")

    mcp.add_middleware(
        MCPAuditMiddleware(endpoint_name=name, writer=writer, skip_tools=skip_tools)
    )
    return mcp


def _read_lines(log_root: Path) -> list[dict]:
    files = list(log_root.glob("*.jsonl"))
    if not files:
        return []
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_middleware_writes_one_line_per_tool_call(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "hi"})
        await client.call_tool("add", {"a": 2, "b": 3})

    rows = _read_lines(tmp_path)
    assert len(rows) == 2
    assert [r["tool"] for r in rows] == ["echo", "add"]
    for r in rows:
        assert r["endpoint"] == "pepper"
        assert r["result"] == "ok"
        assert "duration_ms" in r
        assert isinstance(r["duration_ms"], int)
        assert r["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_middleware_default_args_summary_has_arg_keys_and_arg_count(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        # Pick values whose digits don't collide with the structural
        # ``arg_count: 2`` field, so the "no values leak" check is
        # actually probative. (Plan used ``a=2, b=3`` which causes the
        # digit ``2`` to appear legitimately as ``arg_count``.)
        await client.call_tool("add", {"a": 7, "b": 8})

    rows = _read_lines(tmp_path)
    assert rows[0]["args_summary"] == {"arg_keys": ["a", "b"], "arg_count": 2}
    # No values leak.
    assert "7" not in json.dumps(rows[0]["args_summary"])
    assert "8" not in json.dumps(rows[0]["args_summary"])


@pytest.mark.asyncio
async def test_middleware_handles_no_args_call(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)

    @mcp.tool()
    async def noargs() -> str:
        return "ok"

    async with Client(mcp) as client:
        await client.call_tool("noargs", {})

    rows = _read_lines(tmp_path)
    assert rows[-1]["args_summary"] == {"arg_keys": [], "arg_count": 0}


@pytest.mark.asyncio
async def test_middleware_request_id_present_when_available(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "hi"})

    rows = _read_lines(tmp_path)
    # In-memory transport assigns request ids; field must exist either as
    # a string or as null (never missing).
    assert "request_id" in rows[0]


@pytest.mark.asyncio
async def test_middleware_session_id_field_is_present_even_when_null(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    mcp = _build_server(name="pepper", writer=writer)
    async with Client(mcp) as client:
        await client.call_tool("echo", {"text": "hi"})

    rows = _read_lines(tmp_path)
    assert "session_id" in rows[0]
    # In-memory transport may not have a session id; serialized as null.
    assert rows[0]["session_id"] is None or isinstance(rows[0]["session_id"], str)
