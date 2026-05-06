"""Cross-endpoint MCP wiring — Pepper's MCP session gets the two webcam tools.

Mirrors agent-core-briefs/tests/test_mcp_wiring.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend
from agent_core_webcam.plugin import wire_endpoints_after_registration

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.plugins.specs import RunnerServices


_EXPECTED = {"capture_webcam_frame", "list_cameras"}


async def test_pepper_mcp_gets_two_webcam_tools_after_bus_start(tmp_path: Path):
    webcam_ep = WebcamEndpoint(
        name="webcam-pepper",
        captures_root=tmp_path / "captures",
        audit_log_path=tmp_path / "audit.jsonl",
        camera_backend=FakeCameraBackend.with_cameras([0]),
    )
    mcp_ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    assert mcp_ep.deferred_tool_mounters == []

    bus = Bus(
        BusConfig(
            storage_path=tmp_path / "bus.sqlite",
            redelivery_timeout_seconds=60,
            max_delivery_attempts=2,
            ttl_sweep_seconds=3600,
            redelivery_sweep_seconds=3600,
        )
    )
    bus.register(EndpointSpec(endpoint=webcam_ep, description="webcam"))
    bus.register(EndpointSpec(endpoint=mcp_ep, description="pepper MCP"))

    services = RunnerServices(notify_broker=NotificationBroker())
    wire_endpoints_after_registration(
        endpoints={"webcam-pepper": webcam_ep, "pepper": mcp_ep},
        raw_endpoint_configs={
            "webcam-pepper": {"name": "webcam-pepper", "type": "builtin.webcam", "params": {}},
            "pepper": {
                "name": "pepper",
                "type": "builtin.claude_code_mcp",
                "params": {"mount": "/mcp/pepper", "webcam": "webcam-pepper"},
            },
        },
        services=services,
    )
    assert len(mcp_ep.deferred_tool_mounters) == 1

    try:
        await bus.start()
        tools = await mcp_ep._mcp.list_tools()
        names = {t.name for t in tools}
        missing = _EXPECTED - names
        assert not missing, f"missing webcam tools: {sorted(missing)}, registered: {sorted(names)}"
    finally:
        await bus.stop()


async def test_wire_skips_mcp_without_webcam_param(tmp_path: Path):
    mcp_ep = ClaudeCodeMCPEndpoint(name="other-agent", mount="/mcp/other-agent")
    services = RunnerServices(notify_broker=NotificationBroker())
    wire_endpoints_after_registration(
        endpoints={"other-agent": mcp_ep},
        raw_endpoint_configs={
            "other-agent": {
                "name": "other-agent",
                "type": "builtin.claude_code_mcp",
                "params": {"mount": "/mcp/other-agent"},
            },
        },
        services=services,
    )
    assert mcp_ep.deferred_tool_mounters == []


async def test_wire_raises_on_unknown_webcam_name(tmp_path: Path):
    mcp_ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    services = RunnerServices(notify_broker=NotificationBroker())
    with pytest.raises(
        ValueError,
        match=r"webcam='not\.real'.*Available WebcamEndpoint names: \[\]",
    ):
        wire_endpoints_after_registration(
            endpoints={"pepper": mcp_ep},
            raw_endpoint_configs={
                "pepper": {
                    "name": "pepper",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/pepper", "webcam": "not.real"},
                },
            },
            services=services,
        )


async def test_wire_raises_when_webcam_name_points_at_wrong_type(tmp_path: Path):
    mcp_ep = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    other_ep = ClaudeCodeMCPEndpoint(name="not-a-webcam", mount="/mcp/other")
    services = RunnerServices(notify_broker=NotificationBroker())
    with pytest.raises(
        ValueError,
        match=r"not a WebcamEndpoint.*Available WebcamEndpoint names: \[\]",
    ):
        wire_endpoints_after_registration(
            endpoints={"pepper": mcp_ep, "not-a-webcam": other_ep},
            raw_endpoint_configs={
                "pepper": {
                    "name": "pepper",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/pepper", "webcam": "not-a-webcam"},
                },
                "not-a-webcam": {
                    "name": "not-a-webcam",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/other"},
                },
            },
            services=services,
        )
