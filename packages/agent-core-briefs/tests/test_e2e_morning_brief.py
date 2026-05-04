"""End-to-end: BriefRequest -> gather -> wake -> submit -> destinations -> audit.

Drives a stub agent through the complete morning_brief flow with a
real in-process :class:`agent_core.bus.core.Bus`, the production
:class:`agent_core_briefs.orchestrator.BriefsOrchestratorEndpoint`,
real ``filesystem_read`` plus the test-only ``fake_calendar`` fetcher
for the gather step, and both production destinations
(``discord_embed`` and ``markdown_file``) wired through. A fake
DiscordEndpoint catches the embed envelopes the discord_embed
destination publishes; the markdown destination writes a file under
``tmp_path``; the audit log is redirected to ``tmp_path`` to keep the
test hermetic.

The "stub agent" is not an LLM — the test code drives the compose-loop
tool calls (``list_sections``, ``get_section_spec``, ``submit_brief``)
on the stub's behalf using the session_token it received in the
ComposeBrief envelope. T17's value is the integration assertion across
the whole chain, not exercising agent reasoning.
"""

from __future__ import annotations

import json
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.audit import AuditLog
from agent_core_briefs.destinations.discord_embed import DiscordEmbedDestination
from agent_core_briefs.destinations.markdown_file import MarkdownFileDestination
from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint
from agent_core_briefs.submit import submit_brief
from agent_core_briefs.tools import get_section_spec, list_sections

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import Envelope, EventPayload
from agent_core.bus.handle import BusHandle

# ----------------------------------------------------------------------
# Test endpoints — stub agent + fake Discord
# ----------------------------------------------------------------------


class _StubAgentEndpoint:
    """Minimal Endpoint that records ComposeBrief envelopes for the test driver.

    A real agent would feed the envelope into an LLM compose loop and
    eventually call ``submit_brief`` itself. The e2e test bypasses the
    LLM: it pulls the session_token off the recorded envelope and
    drives the compose-loop tools directly.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.compose_briefs: list[Envelope] = []
        self._handle: BusHandle | None = None

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")
        # Always ack first — bus delivery is at-least-once and we don't
        # want this endpoint to ever requeue. Failures past this point
        # are programmer errors, not bus-level concerns.
        await self._handle.ack(envelope.id)
        if envelope.kind == "Event" and envelope.payload.type == "ComposeBrief":
            self.compose_briefs.append(envelope)

    async def stop(self) -> None:
        self._handle = None


class _FakeDiscordEndpoint:
    """Endpoint that records the embed envelopes ``discord_embed`` publishes.

    The real DiscordEndpoint deserializes ``metadata.discord.embeds``
    into ``discord.Embed`` objects and calls ``channel.send``. The e2e
    test only needs to verify the destination correctly enqueued an
    envelope of the right shape — the actual Discord API call is out
    of scope for an in-process integration test.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.received: list[Envelope] = []
        self._handle: BusHandle | None = None

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")
        await self._handle.ack(envelope.id)
        self.received.append(envelope)

    async def stop(self) -> None:
        self._handle = None


# ----------------------------------------------------------------------
# Fixture wiring helpers
# ----------------------------------------------------------------------


# The e2e playbook: declares both production destinations, plus enough
# sections to exercise required + optional + conditional gating. The
# ``calendar`` namespace below is populated by the fake_calendar
# fetcher; the conditional ``next_event_callout`` activates when the
# calendar payload reports an event coming up soon. ``${...}`` slots
# are filled by the orchestrator's vars_map at parse time.
_E2E_PLAYBOOK = textwrap.dedent(
    """\
    # Morning Brief — End-to-End Test Playbook

    ## Metadata
    ```yaml
    brief_type: morning_brief
    voice: "Pepper, warm"
    gather_config: ${gather_config_path}
    ```

    ## Destinations
    ```yaml
    destinations:
      - type: discord_embed
        config:
          channel_id: "999000111"
          discord_endpoint_name: discord
      - type: markdown_file
        config:
          path: ${markdown_output_path}
    ```

    ## Colors
    ```yaml
    colors:
      RED: 15548997
      GREEN: 5763719
    ```

    ## Sections

    ### greeting
    ```yaml
    section_id: greeting
    title: "Morning"
    color: RED
    required: true
    fields:
      - name: "Today"
        required: true
        max_chars: 200
    ```

    ### calendar_today
    ```yaml
    section_id: calendar_today
    title: "Today's calendar"
    color: RED
    required: true
    fields:
      - name: "Schedule"
        required: true
        max_chars: 500
    ```

    ### footer_optional
    ```yaml
    section_id: footer_optional
    title: "Footer"
    color: GREEN
    fields:
      - name: "Note"
    ```

    ## Conditional sections

    ### next_event_callout
    ```yaml
    section_id: next_event_callout
    title: "Next up"
    color: GREEN
    when:
      expr: "calendar.next_event_in_min < 60"
    required_when_active: true
    fields:
      - name: "Event"
        required: true
        max_chars: 200
    ```
    """
)


# Gather config used by the e2e playbook. The ``priorities`` namespace
# is populated by ``filesystem_read`` against a small JSON fixture
# written into tmp_path so the test exercises the real fetcher (not a
# stub), and ``calendar`` comes from the fake_calendar fetcher.
_E2E_GATHER_TEMPLATE = textwrap.dedent(
    """\
    fetchers:
      - type: test.fake_calendar
        namespace: calendar
        timeout_seconds: 30
        config: {{}}
      - type: filesystem_read
        namespace: priorities
        timeout_seconds: 10
        config:
          path: {priorities_path}
          format: json
    """
)


@dataclass
class _E2EHarness:
    """Bundle of objects the test driver needs to assert on."""

    bus: Bus
    orchestrator: BriefsOrchestratorEndpoint
    stub: _StubAgentEndpoint
    discord: _FakeDiscordEndpoint
    audit_path: Path
    markdown_path: Path
    submit_bus_handle: BusHandle = field(init=False)


@pytest.fixture
async def briefs_e2e(tmp_path):
    """Build a real Bus + register orchestrator, stub agent, fake discord.

    The fetcher_paths point at a directory containing both the e2e
    fake_calendar fixture and the framework's built-in
    ``filesystem_read`` fetcher source — that way the orchestrator's
    fetcher catalog is built the same way the production runner does
    (filesystem-discovered), with no in-test wiring shortcuts.
    """
    # Layout:
    #   tmp_path/playbooks/morning_brief.md      (e2e playbook)
    #   tmp_path/gather/morning.yaml             (gather config)
    #   tmp_path/priorities.json                 (filesystem_read source)
    #   tmp_path/fetchers/fake_calendar.py       (test fetcher copy)
    #   tmp_path/fetchers/filesystem_read.py     (real fetcher copy)
    #   tmp_path/markdown/{{when.date}}.md       (destination output)
    #   tmp_path/audit.jsonl                     (audit log)
    #   tmp_path/bus.sqlite                      (bus persistence)
    playbooks_dir = tmp_path / "playbooks"
    playbooks_dir.mkdir()
    gather_dir = tmp_path / "gather"
    gather_dir.mkdir()
    fetcher_dir = tmp_path / "fetchers"
    fetcher_dir.mkdir()
    markdown_dir = tmp_path / "markdown"
    markdown_dir.mkdir()

    # ``priorities.json`` exercises the real filesystem_read fetcher.
    priorities_path = tmp_path / "priorities.json"
    priorities_path.write_text(
        json.dumps({"items": ["ship cutover #09", "merge T17"]}),
        encoding="utf-8",
    )

    gather_path = gather_dir / "morning.yaml"
    gather_path.write_text(
        _E2E_GATHER_TEMPLATE.format(priorities_path=str(priorities_path).replace("\\", "/")),
        encoding="utf-8",
    )

    markdown_template = str(markdown_dir / "{{when.date}}.md").replace("\\", "/")
    playbook_path = playbooks_dir / "morning_brief.md"
    playbook_path.write_text(_E2E_PLAYBOOK, encoding="utf-8")

    # Copy fake_calendar.py + filesystem_read.py into a single fetcher
    # discovery directory. The loader skips ``__init__.py``-style files
    # (anything starting with ``_``), so we only need the implementation
    # modules themselves. Copying (rather than referencing the source
    # tree directly) keeps the discovery deterministic — if the source
    # tree gains another fetcher, this test won't accidentally pick it
    # up and shift the catalog.
    repo_fixtures = Path(__file__).parent / "fixtures"
    repo_fetchers = Path(__file__).parent.parent / "src" / "agent_core_briefs" / "fetchers"
    (fetcher_dir / "fake_calendar.py").write_text(
        (repo_fixtures / "fake_calendar.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fetcher_dir / "filesystem_read.py").write_text(
        (repo_fetchers / "filesystem_read.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Build the bus.
    bus = Bus(
        BusConfig(
            storage_path=tmp_path / "bus.sqlite",
            redelivery_timeout_seconds=60,
            max_delivery_attempts=2,
            ttl_sweep_seconds=3600,
            redelivery_sweep_seconds=3600,
        )
    )

    orchestrator = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbooks_dir,
        vars_map={
            "gather_config_path": str(gather_path),
            "markdown_output_path": markdown_template,
        },
        fetcher_paths=[fetcher_dir],
        default_target_agent="stub-agent",
    )
    stub = _StubAgentEndpoint("stub-agent")
    discord = _FakeDiscordEndpoint("discord")

    bus.register(EndpointSpec(endpoint=orchestrator, description="briefs orchestrator"))
    bus.register(EndpointSpec(endpoint=stub, description="stub agent for e2e"))
    bus.register(EndpointSpec(endpoint=discord, description="fake discord"))

    await bus.start()

    harness = _E2EHarness(
        bus=bus,
        orchestrator=orchestrator,
        stub=stub,
        discord=discord,
        audit_path=tmp_path / "audit.jsonl",
        markdown_path=markdown_dir,
    )
    # The submit handler needs a BusHandle so destinations like
    # discord_embed can publish through it. Build one bound to a
    # dedicated "test-driver" identity so the from_ stamp on the
    # Discord envelope is honest about who posted it.
    bus.register(
        EndpointSpec(
            endpoint=_StubAgentEndpoint("test-driver"),
            description="test driver identity for submit_brief",
        )
    )
    # The driver endpoint is registered just to give the submit handle
    # a valid identity; we don't need to start it because we never
    # publish *to* it. BusHandle only consults the bus internals.
    harness.submit_bus_handle = BusHandle(bus, "test-driver")

    try:
        yield harness
    finally:
        await bus.stop()


# ----------------------------------------------------------------------
# The single end-to-end test
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_morning_brief_full_flow(briefs_e2e: _E2EHarness, tmp_path: Path) -> None:
    """Drive the full morning_brief chain through a real in-process bus.

    Steps:
        1. Publish a BriefRequest event addressed to the orchestrator.
        2. Orchestrator runs gather (fake_calendar + filesystem_read),
           registers a session, publishes ComposeBrief to the stub agent.
        3. Test driver pulls the session_token off the stub's recorded
           envelope and exercises ``list_sections`` / ``get_section_spec``
           for each required + conditional section.
        4. Test driver builds a sections list and calls ``submit_brief``
           with both production destination factories + an audit log
           pointed at tmp_path.
        5. Assert: submit_brief returns success, the markdown file
           exists with the expected sections, the fake Discord endpoint
           captured an envelope with the right embed metadata, and the
           audit log has the expected event chain.
    """
    when = datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC)

    # 1. Publish the BriefRequest. We borrow the orchestrator's bus
    # handle by going through a freshly-bound BusHandle for the
    # "scheduler" identity — using the orchestrator's own handle would
    # mean the from_ stamp is the orchestrator publishing to itself,
    # which is misleading.
    request_id = uuid.uuid4().hex
    correlation_id = uuid.uuid4().hex
    request_env = Envelope(
        id=request_id,
        correlation_id=correlation_id,
        to="briefs.orchestrator",
        kind="Event",
        payload=EventPayload(
            type="BriefRequest",
            data={
                "brief_type": "morning_brief",
                "scope": "today",
                "when": when.isoformat(),
            },
        ),
        metadata={"target_agent": "stub-agent"},
        created_at=when,
    )
    # Directly enqueue via the bus (synchronous dispatch happens inside
    # _enqueue, so by the time it returns the orchestrator has produced
    # the ComposeBrief and the stub has had deliver() called). The
    # "test-driver" endpoint registered in the fixture stamps from_ on
    # this publish so the bus has a valid registered sender.
    scheduler_handle = BusHandle(briefs_e2e.bus, "test-driver")
    await scheduler_handle.publish(request_env)

    # 2. The stub should now have exactly one ComposeBrief envelope.
    assert len(briefs_e2e.stub.compose_briefs) == 1, (
        f"expected 1 ComposeBrief, got {len(briefs_e2e.stub.compose_briefs)}"
    )
    compose_env = briefs_e2e.stub.compose_briefs[0]
    assert compose_env.kind == "Event"
    assert compose_env.payload.type == "ComposeBrief"
    assert compose_env.to == "stub-agent"
    assert compose_env.correlation_id == correlation_id

    compose_data = compose_env.payload.data
    session_token = compose_data["session_token"]
    assert isinstance(session_token, str) and len(session_token) == 32
    # Gather actually ran: both fetchers landed in the context.
    ctx = compose_data["context"]
    assert ctx["calendar"]["next_event_in_min"] == 30
    assert ctx["calendar"]["events"][0]["title"] == "Standup"
    assert ctx["priorities"]["items"] == ["ship cutover #09", "merge T17"]
    assert "_errors" not in ctx
    # Conditional gated truthy because next_event_in_min < 60.
    assert compose_data["sections_conditional_active"] == ["next_event_callout"]
    assert compose_data["sections_required"] == ["greeting", "calendar_today"]

    # 3. Drive the compose-loop tools the way an LLM agent would. The
    # plan calls for "stub calls list_sections, get_section_spec for each"
    # — we exercise list_sections + get_section_spec across the required
    # static sections AND the active conditional sections.
    sections_view = await list_sections(briefs_e2e.orchestrator.registry, session_token)
    assert sections_view["required"] == ["greeting", "calendar_today"]
    assert sections_view["conditional_active"] == ["next_event_callout"]
    assert sections_view["voice"] == "Pepper, warm"

    section_specs: dict[str, dict] = {}
    for sid in [*sections_view["required"], *sections_view["conditional_active"]]:
        spec = await get_section_spec(
            briefs_e2e.orchestrator.registry, session_token, section_id=sid
        )
        section_specs[sid] = spec
        # Sanity: color resolved to int decimal at session-build time.
        assert isinstance(spec["color"], int)

    # 4. Build a filled-in sections list mirroring what an agent would
    # produce, then call submit_brief with the production destination
    # factories and an audit log under tmp_path.
    sections_payload = [
        {
            "section_id": "greeting",
            "title": section_specs["greeting"]["title"],
            "color": section_specs["greeting"]["color"],
            "fields": [{"name": "Today", "value": "Bright morning. Two events on the calendar."}],
        },
        {
            "section_id": "calendar_today",
            "title": section_specs["calendar_today"]["title"],
            "color": section_specs["calendar_today"]["color"],
            "fields": [
                {
                    "name": "Schedule",
                    "value": "09:00 Standup (15m)\n10:00 Pairing on cutover #09 (90m)",
                }
            ],
        },
        {
            "section_id": "next_event_callout",
            "title": section_specs["next_event_callout"]["title"],
            "color": section_specs["next_event_callout"]["color"],
            "fields": [{"name": "Event", "value": "Standup in ~30 minutes."}],
        },
    ]
    audit_log = AuditLog(briefs_e2e.audit_path)

    destination_factories = {
        "discord_embed": DiscordEmbedDestination,
        "markdown_file": MarkdownFileDestination,
    }
    submit_result = await submit_brief(
        registry=briefs_e2e.orchestrator.registry,
        token=session_token,
        sections=sections_payload,
        destination_factories=destination_factories,
        bus_handle=briefs_e2e.submit_bus_handle,
        audit_log=audit_log,
    )

    # 5. Submit completed successfully against both destinations.
    assert submit_result.success is True
    assert submit_result.validation_issues == []
    assert len(submit_result.deliveries) == 2
    by_type = {d.destination_type: d for d in submit_result.deliveries}
    assert by_type["markdown_file"].success is True
    assert by_type["discord_embed"].success is True

    # Markdown file lands at the templated path.
    expected_md = briefs_e2e.markdown_path / "2026-05-04.md"
    assert expected_md.exists(), f"expected markdown file at {expected_md}"
    md_text = expected_md.read_text(encoding="utf-8")
    assert "# morning_brief —" in md_text
    assert "## Morning" in md_text
    assert "## Today's calendar" in md_text
    assert "## Next up" in md_text
    assert "Bright morning" in md_text
    assert "Standup in ~30 minutes" in md_text

    # Discord endpoint received an envelope with embed metadata.
    assert len(briefs_e2e.discord.received) == 1
    discord_env = briefs_e2e.discord.received[0]
    assert discord_env.kind == "TextMessage"
    discord_meta = discord_env.metadata.get("discord")
    assert discord_meta is not None
    assert discord_meta["channel_id"] == "999000111"
    embeds = discord_meta["embeds"]
    assert len(embeds) == 3
    # Footer goes on the LAST embed only.
    assert "footer" in embeds[-1]
    assert embeds[-1]["footer"]["text"].startswith("morning_brief")
    # Each embed carries title + fields built from the agent's submission.
    titles = [e["title"] for e in embeds]
    assert titles == ["Morning", "Today's calendar", "Next up"]

    # 6. Audit log carries the expected event chain.
    assert briefs_e2e.audit_path.exists()
    audit_lines = [
        json.loads(line)
        for line in briefs_e2e.audit_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    event_types = [e["event_type"] for e in audit_lines]
    # Two destinations -> two submit.deliver events, then submit.complete.
    assert event_types == ["submit.deliver", "submit.deliver", "submit.complete"]
    # Token is truncated to 8 chars in every event for privacy.
    for event in audit_lines:
        assert event["session_token"] == session_token[:8]
        assert event["brief_type"] == "morning_brief"
        assert event["target_agent"] == "stub-agent"
    final = audit_lines[-1]
    assert final["data"]["total_destinations"] == 2
    assert final["data"]["successful"] == 2
    assert final["data"]["failed"] == 0
    assert final["data"]["overall_success"] is True

    # Session token is one-shot consumed: the registry must refuse a
    # second lookup. (consume happens inside submit_brief after
    # validation passes, before the destination fan-out.)
    from agent_core_briefs.session import SessionConsumedError

    with pytest.raises(SessionConsumedError):
        briefs_e2e.orchestrator.registry.get(session_token)
