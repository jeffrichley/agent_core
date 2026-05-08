"""ClaudeCodeMCPEndpoint.attach_audit_writer registers MCPAuditMiddleware."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from agent_core.bus.protocol import AuditWriterAwareEndpoint
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.mcp_audit.writer import MCPAuditWriter


@pytest.mark.asyncio
async def test_endpoint_implements_audit_writer_aware_protocol():
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    assert isinstance(ep, AuditWriterAwareEndpoint)


@pytest.mark.asyncio
async def test_attach_audit_writer_registers_middleware_that_audits_calls(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    ep.attach_audit_writer(writer, skip_tools=frozenset())

    async with Client(ep._mcp) as client:
        await client.call_tool("list_endpoints", {})

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert any(r["tool"] == "list_endpoints" and r["endpoint"] == "pepper" for r in rows)


@pytest.mark.asyncio
async def test_attach_audit_writer_respects_skip_tools(tmp_path: Path):
    writer = MCPAuditWriter(log_root=tmp_path, timezone="UTC")
    ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    ep.attach_audit_writer(writer, skip_tools=frozenset({"list_endpoints"}))

    async with Client(ep._mcp) as client:
        await client.call_tool("list_endpoints", {})

    files = list(tmp_path.glob("*.jsonl"))
    # No file is created because no audited call happened.
    assert files == []
