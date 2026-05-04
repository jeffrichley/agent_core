"""T19 cross-endpoint MCP wiring — Pepper's MCP session gets the seven briefs tools.

This test proves the cutover-gate-blocker scenario: starting a real
:class:`agent_core.bus.core.Bus` with a :class:`BriefsOrchestratorEndpoint`
and a :class:`ClaudeCodeMCPEndpoint` whose deferred mounter list has
been populated by the briefs plugin's
:func:`agent_core_briefs.plugin.wire_endpoints_after_registration`
hookimpl. After ``bus.start()`` runs, the MCP endpoint's FastMCP server
has the seven briefs agent tools registered (the same surface
:func:`agent_core_briefs.mcp.register_briefs_tools` mounts directly).

The test bypasses the runner's yaml-loading path so the assertion is
on the plugin's wiring contract, not on yaml parsing — runner integration
is exercised separately by
``packages/core/tests/test_pepper_example_yaml.py``'s tripwire suite.

If this test fails, Step 6 of the cutover #09 playbook ("real Pepper
morning_brief firing on cron") cannot pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint
from agent_core_briefs.plugin import wire_endpoints_after_registration

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint
from agent_core.plugins.specs import RunnerServices

FIXTURES = Path(__file__).parent / "fixtures"
PLAYBOOKS_DIR = FIXTURES / "playbooks"
GATHER_DIR = FIXTURES / "gather"
PACKAGE_DESTINATIONS = Path(__file__).parent.parent / "src" / "agent_core_briefs" / "destinations"


_EXPECTED_TOOLS = {
    "compose_brief",
    "list_sections",
    "get_section_spec",
    "validate_section",
    "compress_sections",
    "add_extension_section",
    "submit_brief",
}


@pytest.mark.asyncio
async def test_pepper_mcp_endpoint_has_seven_briefs_tools_after_bus_start(tmp_path):
    """After cross-endpoint wiring + bus.start, Pepper's MCP server has the
    seven briefs agent tools registered.

    Steps:
        1. Build a real Bus + a BriefsOrchestratorEndpoint with built-in
           destinations discovered + a tmp_path-scoped audit log.
        2. Build a ClaudeCodeMCPEndpoint named ``pepper`` with no
           ``briefs_orchestrator`` constructor arg — the wiring runs
           through ``deferred_tool_mounters``, not __init__.
        3. Register both on the bus.
        4. Synthesize the (endpoints, raw_endpoint_configs, services)
           triple the runner would pass and call
           ``wire_endpoints_after_registration`` directly. Assert one
           mounter was appended to ``deferred_tool_mounters``.
        5. Start the bus. Assert the seven briefs tools are registered
           on the MCP endpoint's FastMCP server.
    """
    # --- 1. Build the orchestrator with built-in destinations + audit. -----
    # Copy the orchestrator fixture playbook into tmp_path so the
    # orchestrator finds it via the brief_type-named convention.
    src_playbook = (PLAYBOOKS_DIR / "morning-orchestrator.md").read_text(encoding="utf-8")
    playbook_dst = tmp_path / "morning_brief.md"
    playbook_dst.write_text(src_playbook, encoding="utf-8")

    audit_path = tmp_path / "audit.jsonl"
    # Pass destination_paths=None (use defaults only) so the test exercises
    # the built-in destination discovery without colliding type_ids. The
    # PACKAGE_DESTINATIONS reference is kept above as a load-time sanity
    # check (the path must exist) for future maintainers reading this test.
    assert PACKAGE_DESTINATIONS.is_dir()
    orchestrator = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=tmp_path,
        vars_map={"gather_config_path": str(GATHER_DIR / "morning-test.yaml")},
        fetcher_catalog={},  # gather isn't exercised here; tools mount fine without it
        default_target_agent="pepper",
        audit_log_path=audit_path,
    )
    # Sanity: the constructor discovered both built-in destinations.
    assert set(orchestrator.destination_factories) == {"discord_embed", "markdown_file"}
    # And the audit log is the one we configured.
    assert orchestrator.audit_log.path == audit_path

    # --- 2. Build the MCP endpoint with no briefs_orchestrator wiring. -----
    mcp_endpoint = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    assert mcp_endpoint.deferred_tool_mounters == []

    # --- 3. Register both on the bus (don't start yet). --------------------
    bus = Bus(
        BusConfig(
            storage_path=tmp_path / "bus.sqlite",
            redelivery_timeout_seconds=60,
            max_delivery_attempts=2,
            ttl_sweep_seconds=3600,
            redelivery_sweep_seconds=3600,
        )
    )
    bus.register(EndpointSpec(endpoint=orchestrator, description="briefs orchestrator"))
    bus.register(EndpointSpec(endpoint=mcp_endpoint, description="pepper MCP"))

    # --- 4. Drive the plugin's hookimpl with a synthesized triple. ---------
    endpoints_by_name = {
        "briefs.orchestrator": orchestrator,
        "pepper": mcp_endpoint,
    }
    raw_endpoint_configs = {
        "briefs.orchestrator": {
            "name": "briefs.orchestrator",
            "type": "builtin.briefs_orchestrator",
            "params": {},
        },
        "pepper": {
            "name": "pepper",
            "type": "builtin.claude_code_mcp",
            "params": {
                "mount": "/mcp/pepper",
                "briefs_orchestrator": "briefs.orchestrator",
            },
        },
    }
    services = RunnerServices(notify_broker=NotificationBroker())

    wire_endpoints_after_registration(
        endpoints=endpoints_by_name,
        raw_endpoint_configs=raw_endpoint_configs,
        services=services,
    )

    # Exactly one mounter was appended — the briefs plugin's
    # closure for this MCP endpoint.
    assert len(mcp_endpoint.deferred_tool_mounters) == 1

    # --- 5. Start the bus, assert the seven briefs tools are registered. ---
    try:
        await bus.start()
        # Tools registered with FastMCP are visible via mcp.list_tools().
        tools = await mcp_endpoint._mcp.list_tools()
        names = {t.name for t in tools}
        missing = _EXPECTED_TOOLS - names
        assert not missing, (
            f"after bus.start, the seven briefs tools must be on Pepper's MCP "
            f"server; missing: {sorted(missing)} (registered: {sorted(names)})"
        )
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_wire_endpoints_skips_mcp_without_briefs_orchestrator(tmp_path):
    """An MCP endpoint whose yaml entry doesn't name a briefs_orchestrator
    must not have a deferred mounter appended. This guards against the
    plugin wiring up unrelated MCP endpoints (e.g., a non-Pepper agent
    on the same bus that doesn't use briefs)."""
    mcp_endpoint = ClaudeCodeMCPEndpoint(name="other-agent", mount="/mcp/other-agent")
    services = RunnerServices(notify_broker=NotificationBroker())

    wire_endpoints_after_registration(
        endpoints={"other-agent": mcp_endpoint},
        raw_endpoint_configs={
            "other-agent": {
                "name": "other-agent",
                "type": "builtin.claude_code_mcp",
                "params": {"mount": "/mcp/other-agent"},
            },
        },
        services=services,
    )

    assert mcp_endpoint.deferred_tool_mounters == []


@pytest.mark.asyncio
async def test_wire_endpoints_raises_on_unknown_orchestrator_name(tmp_path):
    """A typo in ``params.briefs_orchestrator`` must surface as a clear
    error at runner construction time, not as a silent no-op that
    leaves Pepper's session toolless."""
    mcp_endpoint = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    services = RunnerServices(notify_broker=NotificationBroker())

    with pytest.raises(
        ValueError,
        match=(
            r"briefs_orchestrator='not\.a\.real\.orch'.*"
            r"Available BriefsOrchestratorEndpoint names: \[\]"
        ),
    ):
        wire_endpoints_after_registration(
            endpoints={"pepper": mcp_endpoint},
            raw_endpoint_configs={
                "pepper": {
                    "name": "pepper",
                    "type": "builtin.claude_code_mcp",
                    "params": {
                        "mount": "/mcp/pepper",
                        "briefs_orchestrator": "not.a.real.orch",
                    },
                },
            },
            services=services,
        )


@pytest.mark.asyncio
async def test_wire_endpoints_raises_when_orchestrator_name_points_at_wrong_type(tmp_path):
    """If ``params.briefs_orchestrator`` names an endpoint that exists but
    is not a ``BriefsOrchestratorEndpoint``, raise — silently mounting
    nothing would be a worse outcome than failing loud at boot."""
    mcp_endpoint = ClaudeCodeMCPEndpoint(name="pepper", mount="/mcp/pepper")
    other_endpoint = ClaudeCodeMCPEndpoint(name="not-an-orchestrator", mount="/mcp/other")
    services = RunnerServices(notify_broker=NotificationBroker())

    with pytest.raises(
        ValueError,
        match=(
            r"not a BriefsOrchestratorEndpoint.*"
            r"Available BriefsOrchestratorEndpoint names: \[\]"
        ),
    ):
        wire_endpoints_after_registration(
            endpoints={
                "pepper": mcp_endpoint,
                "not-an-orchestrator": other_endpoint,
            },
            raw_endpoint_configs={
                "pepper": {
                    "name": "pepper",
                    "type": "builtin.claude_code_mcp",
                    "params": {
                        "mount": "/mcp/pepper",
                        "briefs_orchestrator": "not-an-orchestrator",
                    },
                },
                "not-an-orchestrator": {
                    "name": "not-an-orchestrator",
                    "type": "builtin.claude_code_mcp",
                    "params": {"mount": "/mcp/other"},
                },
            },
            services=services,
        )
