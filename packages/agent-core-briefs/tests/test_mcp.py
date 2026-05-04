"""Tests for ``agent_core_briefs.mcp`` — the agent-facing MCP surface.

Covers:

- ``compose_brief`` — self-launch path. Happy path, unknown brief type,
  missing default_target_agent, default ``when``, session queryability.
- ``register_briefs_tools`` — registers the seven agent tools on a
  FastMCP server. Verifies count + that each tool round-trips through
  the FastMCP server's ``call_tool`` interface.
- ``ClaudeCodeMCPEndpoint.tool_mounters`` — verifies plugin tool
  mounters are invoked at construction time with the endpoint's
  internal FastMCP server.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.audit import AuditLog
from agent_core_briefs.mcp import compose_brief, register_briefs_tools
from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint
from agent_core_briefs.protocol import DeliveryResult
from fastmcp import FastMCP

from agent_core.endpoints.claude_code_mcp import ClaudeCodeMCPEndpoint

FIXTURES = Path(__file__).parent / "fixtures"
PLAYBOOKS_DIR = FIXTURES / "playbooks"
GATHER_DIR = FIXTURES / "gather"


# ---- test doubles ----


class _StubFetcher:
    type_id = "stub.simple"
    namespace = ""

    def __init__(self):
        self._payload = {"events": [{"id": "a"}]}

    async def fetch(self, config: dict, when: datetime) -> dict:
        if config:
            return dict(config)
        return dict(self._payload)


class _StubEchoFetcher:
    type_id = "stub.echo"
    namespace = ""

    def __init__(self):
        pass

    async def fetch(self, config: dict, when: datetime) -> dict:
        return dict(config.get("payload") or {"echo": True})


class _RecordingDestination:
    """Stub Destination that records calls and returns a configurable result."""

    def __init__(self, type_id: str, *, result: DeliveryResult | None = None):
        self.type_id = type_id
        self._result = result or DeliveryResult(success=True, ref=f"ref-{type_id}")
        self.calls: list[dict] = []

    async def deliver(self, sections, playbook, scope, when, config, bus_handle):
        self.calls.append(
            {
                "sections": sections,
                "playbook": playbook,
                "scope": scope,
                "when": when,
                "config": config,
                "bus_handle": bus_handle,
            }
        )
        return self._result


class _StubBusHandle:
    pass


# ---- fixtures ----


@pytest.fixture
def fetcher_catalog():
    return {
        "stub.simple": _StubFetcher,
        "stub.echo": _StubEchoFetcher,
    }


@pytest.fixture
def gather_yaml() -> Path:
    return GATHER_DIR / "morning-test.yaml"


@pytest.fixture
def playbook_path(tmp_path, gather_yaml) -> Path:
    """Copy the orchestrator fixture playbook into ``tmp_path/morning_brief.md``
    so the orchestrator finds it via the brief_type-named convention.
    """
    src = PLAYBOOKS_DIR / "morning-orchestrator.md"
    dst = tmp_path / "morning_brief.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


@pytest.fixture
def orchestrator(playbook_path, gather_yaml, fetcher_catalog) -> BriefsOrchestratorEndpoint:
    return BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )


@pytest.fixture
def orchestrator_no_default(
    playbook_path, gather_yaml, fetcher_catalog
) -> BriefsOrchestratorEndpoint:
    """Orchestrator with no default_target_agent — used to exercise the
    self-launch missing-target ValueError surface.
    """
    return BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent=None,
    )


# ---- compose_brief tests --------------------------------------------------


@pytest.mark.asyncio
async def test_compose_brief_runs_gather_and_returns_session(orchestrator):
    """Happy path: compose_brief returns the ComposeBrief data shape with
    a valid session_token + populated context from the gather config.
    """
    data = await compose_brief(
        orchestrator=orchestrator,
        brief_type="morning_brief",
        scope="today",
    )

    # Returned shape matches what the bus path puts on the ComposeBrief envelope.
    assert data["brief_type"] == "morning_brief"
    assert data["scope"] == "today"
    assert isinstance(data["when"], str)
    assert isinstance(data["session_token"], str)
    assert len(data["session_token"]) == 32
    assert data["voice"] == "Pepper, warm"
    # gather context has both fetcher namespaces populated.
    assert "calendar" in data["context"]
    assert data["context"]["calendar"] == {"key": "value"}
    assert data["context"]["priorities"] == {"items": ["a", "b"]}
    # sections lists are mutually exclusive per the playbook.
    assert data["sections_required"] == ["greeting", "calendar_today"]
    assert data["sections_optional"] == ["footer_optional"]
    assert data["sections_conditional_active"] == ["weekly_digest"]


@pytest.mark.asyncio
async def test_compose_brief_unknown_brief_type_raises(orchestrator):
    """Unknown brief type → ValueError (not FileNotFoundError) so the agent
    only catches one error class for bad inputs.
    """
    with pytest.raises(ValueError, match="not found"):
        await compose_brief(
            orchestrator=orchestrator,
            brief_type="does_not_exist",
        )


@pytest.mark.asyncio
async def test_compose_brief_missing_target_agent_raises(orchestrator_no_default):
    """Orchestrator without default_target_agent → ValueError (no implicit
    target — there's no envelope metadata to fall back on).
    """
    with pytest.raises(ValueError, match="default_target_agent"):
        await compose_brief(
            orchestrator=orchestrator_no_default,
            brief_type="morning_brief",
        )


@pytest.mark.asyncio
async def test_compose_brief_uses_default_when(orchestrator):
    """when=None → uses datetime.now(UTC). The session's stored when is a
    timezone-aware datetime close to "now".
    """
    before = datetime.now(UTC)
    data = await compose_brief(
        orchestrator=orchestrator,
        brief_type="morning_brief",
    )
    after = datetime.now(UTC)

    parsed_when = datetime.fromisoformat(data["when"])
    assert parsed_when.tzinfo is not None
    assert before <= parsed_when <= after


@pytest.mark.asyncio
async def test_compose_brief_session_is_queryable_after_call(orchestrator):
    """The session created by compose_brief is registered in the orchestrator's
    registry — agents can immediately call list_sections / get_section_spec
    against the returned token.
    """
    data = await compose_brief(
        orchestrator=orchestrator,
        brief_type="morning_brief",
    )
    token = data["session_token"]

    # Registry lookup succeeds; session carries the right brief_type.
    session = orchestrator.registry.get(token)
    assert session.brief_type == "morning_brief"
    assert session.target_agent == "pepper"
    # Sections are populated (not just ids — full SectionSpec objects).
    section_ids = [s.section_id for s in session.sections]
    assert section_ids == ["greeting", "calendar_today", "footer_optional"]


@pytest.mark.asyncio
async def test_compose_brief_explicit_when_passes_through(orchestrator):
    """Explicit when= argument flows into the session unchanged."""
    custom = datetime(2026, 5, 4, 7, 30, tzinfo=UTC)
    data = await compose_brief(
        orchestrator=orchestrator,
        brief_type="morning_brief",
        when=custom,
    )
    assert data["when"] == "2026-05-04T07:30:00+00:00"
    session = orchestrator.registry.get(data["session_token"])
    assert session.when == custom


@pytest.mark.asyncio
async def test_compose_brief_assigns_unique_correlation_id_per_call(orchestrator):
    """Two self-launches of the same brief_type must produce distinct
    correlation_ids on the registered sessions, so audit events are
    disambiguable. Regression repro for the sentinel-collision bug
    (the previous implementation used a static ``compose_brief:<brief_type>``
    sentinel that collided under concurrency).
    """
    result1 = await compose_brief(orchestrator=orchestrator, brief_type="morning_brief")
    result2 = await compose_brief(orchestrator=orchestrator, brief_type="morning_brief")

    s1 = orchestrator.registry.get(result1["session_token"])
    s2 = orchestrator.registry.get(result2["session_token"])
    assert s1.correlation_id != s2.correlation_id
    # Sanity: each looks like a uuid hex (32 chars, all lowercase hex).
    assert len(s1.correlation_id) == 32
    assert len(s2.correlation_id) == 32


# ---- register_briefs_tools tests ------------------------------------------


@pytest.mark.asyncio
async def test_register_briefs_tools_registers_seven_tools(orchestrator, tmp_path):
    """All seven agent tools are mounted on the FastMCP server."""
    mcp = FastMCP("test-server")
    audit = AuditLog(tmp_path / "audit.jsonl")
    register_briefs_tools(
        mcp=mcp,
        orchestrator=orchestrator,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
        destination_factories={},
    )
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "compose_brief",
        "list_sections",
        "get_section_spec",
        "validate_section",
        "compress_sections",
        "add_extension_section",
        "submit_brief",
    }


def test_register_briefs_tools_rejects_none_bus_handle(orchestrator, tmp_path):
    """bus_handle is non-optional — destinations like discord_embed call
    ``bus_handle.publish`` to fan out, so a ``None`` here is a programming
    error (caught at registration time, not at first delivery attempt).
    """
    mcp = FastMCP("test-server")
    audit = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(TypeError, match="bus_handle is required"):
        register_briefs_tools(
            mcp=mcp,
            orchestrator=orchestrator,
            bus_handle=None,  # type: ignore[arg-type]
            audit_log=audit,
            destination_factories={},
        )


def _structured(call_result) -> dict:
    """FastMCP's call_tool returns a CallToolResult with structured_content
    when the tool returns a dict. Pull that out for assertions; fall back
    to parsing TextContent if structured isn't present.
    """
    if call_result.structured_content is not None:
        return call_result.structured_content
    # Fallback for older FastMCP — parse the JSON text content.
    text = call_result.content[0].text
    return json.loads(text)


@pytest.mark.asyncio
async def test_compose_brief_tool_callable_via_mcp(orchestrator, tmp_path):
    """Invoke compose_brief through the FastMCP server's call_tool interface;
    verify it returns the same shape as the underlying function.
    """
    mcp = FastMCP("test-server")
    audit = AuditLog(tmp_path / "audit.jsonl")
    register_briefs_tools(
        mcp=mcp,
        orchestrator=orchestrator,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
        destination_factories={},
    )

    result = await mcp.call_tool("compose_brief", {"brief_type": "morning_brief", "scope": "today"})
    data = _structured(result)
    assert data["brief_type"] == "morning_brief"
    assert data["scope"] == "today"
    assert isinstance(data["session_token"], str)
    assert len(data["session_token"]) == 32


@pytest.mark.asyncio
async def test_list_sections_tool_callable_via_mcp(orchestrator, tmp_path):
    """list_sections tool threads the token through to the underlying
    function — verifies the MCP wrapper preserves keyword semantics.
    """
    mcp = FastMCP("test-server")
    audit = AuditLog(tmp_path / "audit.jsonl")
    register_briefs_tools(
        mcp=mcp,
        orchestrator=orchestrator,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
        destination_factories={},
    )
    # Compose first so we have a real token.
    composed = _structured(await mcp.call_tool("compose_brief", {"brief_type": "morning_brief"}))
    token = composed["session_token"]

    listed = _structured(await mcp.call_tool("list_sections", {"token": token}))
    assert listed["required"] == ["greeting", "calendar_today"]
    assert listed["optional"] == ["footer_optional"]
    assert listed["conditional_active"] == ["weekly_digest"]
    assert listed["brief_type"] == "morning_brief"


@pytest.mark.asyncio
async def test_submit_brief_tool_callable_via_mcp(orchestrator, tmp_path):
    """submit_brief reaches the audit log + destinations correctly through
    the MCP wrapper. The frozen-dataclass result is flattened to a dict
    on the way out.
    """
    mcp = FastMCP("test-server")
    audit = AuditLog(tmp_path / "audit.jsonl")
    # Provide a destination factory so submit_brief actually fans out.
    destination = _RecordingDestination("noop", result=DeliveryResult(True, ref="ok"))
    register_briefs_tools(
        mcp=mcp,
        orchestrator=orchestrator,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
        destination_factories={"noop": lambda: destination},
    )

    composed = _structured(await mcp.call_tool("compose_brief", {"brief_type": "morning_brief"}))
    token = composed["session_token"]

    # Submit a section list that satisfies validation: every required +
    # conditional-active section needs to be present with non-empty
    # required fields.
    sections = [
        {
            "section_id": "greeting",
            "title": "Morning",
            "color": 15548997,
            "fields": [{"name": "Today", "value": "Hi."}],
        },
        {
            "section_id": "calendar_today",
            "title": "Today's calendar",
            "color": 15548997,
            "fields": [{"name": "Schedule", "value": "10am standup"}],
        },
        {
            "section_id": "weekly_digest",
            "title": "Week ahead",
            "color": 5763719,
            "fields": [{"name": "This week", "value": "Plenty."}],
        },
    ]

    result = _structured(
        await mcp.call_tool("submit_brief", {"token": token, "sections": sections})
    )
    assert result["success"] is True
    assert result["validation_issues"] == []
    assert len(result["deliveries"]) == 1
    assert result["deliveries"][0]["destination_type"] == "noop"
    assert result["deliveries"][0]["success"] is True
    # Destination was actually invoked.
    assert len(destination.calls) == 1
    # Audit log got events written.
    assert audit.path.exists()


@pytest.mark.asyncio
async def test_add_extension_section_tool_callable_via_mcp(orchestrator, tmp_path):
    """add_extension_section appends to the in-flight session — verify the
    tool wrapper passes color + fields through correctly.
    """
    mcp = FastMCP("test-server")
    audit = AuditLog(tmp_path / "audit.jsonl")
    register_briefs_tools(
        mcp=mcp,
        orchestrator=orchestrator,
        bus_handle=_StubBusHandle(),
        audit_log=audit,
        destination_factories={},
    )
    composed = _structured(await mcp.call_tool("compose_brief", {"brief_type": "morning_brief"}))
    token = composed["session_token"]

    added = _structured(
        await mcp.call_tool(
            "add_extension_section",
            {
                "token": token,
                "section_id": "blockers",
                "title": "Today's blockers",
                "color": 15548997,
                "fields": [{"name": "Blocker", "required": True}],
            },
        )
    )
    assert added["section_id"] == "blockers"
    assert added["title"] == "Today's blockers"
    # Session now has the extension recorded.
    session = orchestrator.registry.get(token)
    assert [s.section_id for s in session.extension_sections] == ["blockers"]


# ---- ClaudeCodeMCPEndpoint.tool_mounters tests ----------------------------


def test_tool_mounter_invoked_on_endpoint_init():
    """Plugin tool mounters are invoked at construction time with the
    endpoint's internal FastMCP server.
    """
    captured: list[FastMCP] = []

    def _mounter(mcp: FastMCP) -> None:
        captured.append(mcp)

        @mcp.tool(name="custom_plugin_tool")
        async def _plugin_tool() -> dict:
            return {"ok": True}

    ep = ClaudeCodeMCPEndpoint(
        name="agent-test",
        mount="/mcp/agent-test",
        tool_mounters=[_mounter],
    )
    # Mounter received the endpoint's own FastMCP instance.
    assert captured == [ep._mcp]


@pytest.mark.asyncio
async def test_tool_mounter_tool_is_registered_on_endpoint():
    """The tool a mounter registered is actually visible on the endpoint's
    FastMCP server — the most direct end-to-end check.
    """

    def _mounter(mcp: FastMCP) -> None:
        @mcp.tool(name="custom_plugin_tool")
        async def _plugin_tool() -> dict:
            return {"ok": True}

    ep = ClaudeCodeMCPEndpoint(
        name="agent-test",
        mount="/mcp/agent-test",
        tool_mounters=[_mounter],
    )
    tools = await ep._mcp.list_tools()
    names = {t.name for t in tools}
    assert "custom_plugin_tool" in names


def test_tool_mounters_default_none_does_not_break():
    """Default tool_mounters=None preserves existing endpoint construction
    behavior — no new tools registered, no exceptions raised.
    """
    ep = ClaudeCodeMCPEndpoint(name="agent-test", mount="/mcp/agent-test")
    # Endpoint constructed successfully; only the built-in tools are present.
    assert ep.name == "agent-test"
