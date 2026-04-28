"""Tests for the runner's HTTPHost discovery and lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.runner import build_bus_from_config


@pytest.mark.asyncio
async def test_runner_returns_none_http_host_when_no_mcp_endpoints(tmp_path):
    """If no MCPHostable endpoints are registered, http_host is None."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is None
    assert "stub" in bus._endpoints_by_name


@pytest.mark.asyncio
async def test_runner_constructs_http_host_when_mcp_endpoints_present(tmp_path):
    """One ClaudeCodeMCPEndpoint → HTTPHost is built with that mount."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-test
    params:
      mount: /mcp/agent-test
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    assert len(http_host._mounts) == 1
    assert http_host._mounts[0].mount == "/mcp/agent-test"


@pytest.mark.asyncio
async def test_runner_constructs_http_host_with_multiple_mcp_endpoints(tmp_path):
    """Two CC endpoints → both mounted on the same HTTPHost."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-pepper
    params:
      mount: /mcp/agent-pepper
  - class: agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint
    name: agent-deb
    params:
      mount: /mcp/agent-deb
  - class: agent_core.endpoints.stub.StubEndpoint
    name: stub
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    mounts = sorted(m.mount for m in http_host._mounts)
    assert mounts == ["/mcp/agent-deb", "/mcp/agent-pepper"]
