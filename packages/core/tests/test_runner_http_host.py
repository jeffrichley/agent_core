"""Tests for the runner's HTTPHost discovery and lifecycle."""

from __future__ import annotations

import pytest

from agent_core.bus.runner import build_bus_from_config
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint


@pytest.mark.asyncio
async def test_runner_returns_none_http_host_when_no_mcp_endpoints(tmp_path):
    """If no MCPHostable endpoints are registered, http_host is None."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - type: builtin.stub
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
  - type: builtin.claude_code_mcp
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
  - type: builtin.claude_code_mcp
    name: agent-pepper
    params:
      mount: /mcp/agent-pepper
  - type: builtin.claude_code_mcp
    name: agent-deb
    params:
      mount: /mcp/agent-deb
  - type: builtin.stub
    name: stub
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    mounts = sorted(m.mount for m in http_host._mounts)
    assert mounts == ["/mcp/agent-deb", "/mcp/agent-pepper"]


@pytest.mark.asyncio
async def test_runner_claude_code_mcp_accepts_auto_ack_params(tmp_path):
    """Issue #12: YAML params for auto-ack / missing-ack must construct (no BusBootError)."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.claude_code_mcp
    name: agent-ack
    params:
      mount: /mcp/agent-ack
      wake_on_all_acknowledgments: true
      outbound_registry_ttl_seconds: 120.5
      missing_ack_default_seconds: 7.25
      max_tracked_outbounds: 500
""",
        encoding="utf-8",
    )
    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    spec = bus._endpoints_by_name["agent-ack"]
    ep = spec.endpoint
    assert isinstance(ep, ClaudeCodeMCPEndpoint)
    assert ep.wake_on_all_acknowledgments is True
    assert ep._outbound_registry_ttl_seconds == 120.5
    assert ep._missing_ack_default_seconds == 7.25
    assert ep._max_tracked_outbounds == 500
