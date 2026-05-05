"""BriefsOrchestratorEndpoint — receives BriefRequest, runs gather, publishes ComposeBrief.

Wire shapes consumed/produced are documented in the orchestrator module.
These tests stub the bus with a minimal recording handle (same pattern as
``test_scheduler_endpoint._RecordingHandle``) and use simple in-memory
stub fetchers.
"""

from __future__ import annotations

import asyncio
import textwrap
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.orchestrator import (
    BriefsOrchestratorEndpoint,
    ComposeSession,
)
from agent_core_briefs.session import SessionRegistry

from agent_core.bus.envelope import Envelope, EventPayload, TextMessagePayload

FIXTURES = Path(__file__).parent / "fixtures"
PLAYBOOKS_DIR = FIXTURES / "playbooks"
GATHER_DIR = FIXTURES / "gather"


# ---- test doubles ----


class _RecordingHandle:
    """Stub BusHandle that records publish + ack calls."""

    def __init__(self):
        self.published: list[Envelope] = []
        self.acks: list[str] = []
        self.nacks: list[str] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        if to is not None:
            envelope = envelope.model_copy(update={"to": to if isinstance(to, str) else to[0]})
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None:
        self.acks.append(envelope_id)

    async def nack(self, envelope_id: str, requeue: bool = True) -> None:
        self.nacks.append(envelope_id)

    def endpoints(self):
        return []


class _StubFetcher:
    """Minimal Fetcher that returns a fixed payload. Class-level namespace
    is intentionally empty — built-in fetchers (CliFetcher etc.) work the
    same way, with the gather-config YAML supplying the namespace.
    """

    type_id = "stub.simple"
    namespace = ""

    def __init__(self):
        # The orchestrator instantiates fetchers from the catalog at
        # request time; instances must be cheap to build with no args.
        self._payload = {"events": [{"id": "a"}]}

    async def fetch(self, config: dict, when: datetime) -> dict:
        # Echo the config back so tests can assert the per-fetcher config
        # was threaded through correctly. Falls back to a deterministic
        # default payload when the test gather config is empty.
        if config:
            return dict(config)
        return dict(self._payload)


class _StubEchoFetcher:
    type_id = "stub.echo"
    namespace = ""

    def __init__(self):
        pass

    async def fetch(self, config: dict, when: datetime) -> dict:
        # Return whatever 'payload' the gather config declares; default to
        # a sentinel so the test can detect missing wiring.
        return dict(config.get("payload") or {"echo": True})


class _BoomFetcher:
    type_id = "stub.boom"
    namespace = ""

    def __init__(self):
        pass

    async def fetch(self, config: dict, when: datetime) -> dict:
        raise RuntimeError("boom in fetcher")


# ---- fixtures ----


@pytest.fixture
def gather_yaml(tmp_path) -> Path:
    """Return the path to the canonical orchestrator gather config fixture.

    The fixture lives under ``tests/fixtures/gather/morning-test.yaml`` and
    references two stub fetchers by type_id with explicit namespaces.
    """
    return GATHER_DIR / "morning-test.yaml"


@pytest.fixture
def playbook_path(tmp_path, gather_yaml) -> Path:
    """Copy the orchestrator playbook fixture into ``tmp_path`` named after
    the brief_type so the orchestrator's ``<playbooks_path>/<brief_type>.md``
    convention works.
    """
    src = PLAYBOOKS_DIR / "morning-orchestrator.md"
    dst = tmp_path / "morning_brief.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


@pytest.fixture
def fetcher_catalog():
    return {
        "stub.simple": _StubFetcher,
        "stub.echo": _StubEchoFetcher,
        "stub.boom": _BoomFetcher,
    }


def _make_brief_request(
    *,
    to: str,
    brief_type: str | None = "morning_brief",
    scope: str | None = "today",
    when: str | None = "2026-05-04T07:00:00+00:00",
    target_agent: str | None = None,
    env_id: str | None = None,
) -> Envelope:
    """Build a BriefRequest envelope. Pass ``brief_type=None`` to test the
    missing-key path; pass ``target_agent=...`` to set request metadata.
    """
    data: dict = {}
    if brief_type is not None:
        data["brief_type"] = brief_type
    if scope is not None:
        data["scope"] = scope
    if when is not None:
        data["when"] = when

    metadata: dict = {}
    if target_agent is not None:
        metadata["target_agent"] = target_agent

    return Envelope(
        id=env_id or uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        from_="scheduler",
        to=to,
        kind="Event",
        payload=EventPayload(type="BriefRequest", data=data),
        metadata=metadata,
        created_at=datetime.now(UTC),
    )


# ---- tests ----


@pytest.mark.asyncio
async def test_happy_path_publishes_compose_brief(
    playbook_path, gather_yaml, fetcher_catalog, tmp_path
):
    """BriefRequest → orchestrator runs gather → ComposeBrief published with
    all fields populated correctly."""
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator")
        await ep.deliver(req)
    finally:
        await ep.stop()

    # The request was acked.
    assert req.id in handle.acks

    # Exactly one ComposeBrief was published.
    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 1
    env = composed[0]

    # Envelope-level: routed to default agent, kind=Event, correlation_id preserved.
    assert env.kind == "Event"
    assert env.to == "pepper"
    assert env.correlation_id == req.correlation_id

    data = env.payload.data
    assert data["brief_type"] == "morning_brief"
    assert data["scope"] == "today"
    assert data["when"] == "2026-05-04T07:00:00+00:00"
    assert data["voice"] == "Pepper, warm"
    assert data["playbook_path"] == str(playbook_path)
    assert isinstance(data["session_token"], str)
    assert len(data["session_token"]) == 32  # secrets.token_hex(16) → 32 hex chars
    # Gather context: namespaces from gather config.
    assert "calendar" in data["context"]
    assert data["context"]["calendar"] == {"key": "value"}
    assert data["context"]["priorities"] == {"items": ["a", "b"]}
    # No errors on happy path.
    assert "_errors" not in data["context"]
    # Sections: required/optional split + conditional active.
    assert data["sections_required"] == ["greeting", "calendar_today"]
    assert data["sections_optional"] == ["footer_optional"]
    # weekly_digest activates because calendar.key == 'value'; never_active stays inactive.
    assert data["sections_conditional_active"] == ["weekly_digest"]


@pytest.mark.asyncio
async def test_non_brief_request_envelopes_are_ignored(playbook_path, gather_yaml, fetcher_catalog):
    """Envelopes that aren't BriefRequest events get ack'd but produce no
    ComposeBrief. This keeps the orchestrator a polite citizen on the bus."""
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        # TextMessage is non-Event → ignored.
        text_env = Envelope(
            id="text-1",
            correlation_id="c1",
            from_="someone",
            to="briefs.orchestrator",
            kind="TextMessage",
            payload=TextMessagePayload(text="hello"),
            created_at=datetime.now(UTC),
        )
        await ep.deliver(text_env)
        # Event with non-BriefRequest type → ignored.
        other_event = Envelope(
            id="evt-1",
            correlation_id="c2",
            from_="someone",
            to="briefs.orchestrator",
            kind="Event",
            payload=EventPayload(type="SomeOtherEvent", data={"x": 1}),
            created_at=datetime.now(UTC),
        )
        await ep.deliver(other_event)
    finally:
        await ep.stop()

    # Both envelopes were ack'd.
    assert "text-1" in handle.acks
    assert "evt-1" in handle.acks
    # No ComposeBrief published.
    assert [e for e in handle.published if e.payload.type == "ComposeBrief"] == []


@pytest.mark.asyncio
async def test_missing_brief_type_does_not_publish_and_does_not_raise(
    playbook_path, gather_yaml, fetcher_catalog
):
    """A BriefRequest with no ``brief_type`` is a malformed request. The
    orchestrator logs the failure (caught by the outer handler) and does
    NOT publish a ComposeBrief. It still acks the envelope so the bus
    doesn't redeliver forever."""
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator", brief_type=None, env_id="bad-1")
        # Must not raise out of deliver().
        await ep.deliver(req)
    finally:
        await ep.stop()

    assert "bad-1" in handle.acks
    assert [e for e in handle.published if e.payload.type == "ComposeBrief"] == []


@pytest.mark.asyncio
async def test_unknown_brief_type_does_not_publish(playbook_path, gather_yaml, fetcher_catalog):
    """When ``<playbooks_path>/<brief_type>.md`` doesn't exist, the orchestrator
    cannot compose. It logs and does not publish; the envelope is still
    acked."""
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(
            to="briefs.orchestrator", brief_type="nonexistent_brief", env_id="unk-1"
        )
        await ep.deliver(req)
    finally:
        await ep.stop()

    assert "unk-1" in handle.acks
    assert [e for e in handle.published if e.payload.type == "ComposeBrief"] == []


@pytest.mark.asyncio
async def test_gather_errors_are_captured_and_brief_still_composes(
    playbook_path, fetcher_catalog, tmp_path
):
    """Per-spec graceful degradation: a fetcher that raises lands in
    ``context._errors`` keyed by its type_id, but the brief STILL composes."""
    # Build a gather config that uses the boom fetcher alongside a working one.
    gather = tmp_path / "gather.yaml"
    gather.write_text(
        "fetchers:\n"
        "  - type: stub.simple\n"
        "    namespace: calendar\n"
        "    timeout_seconds: 30\n"
        "    config:\n"
        "      key: value\n"
        "  - type: stub.boom\n"
        "    namespace: priorities\n"
        "    timeout_seconds: 5\n"
        "    config: {}\n",
        encoding="utf-8",
    )

    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator")
        await ep.deliver(req)
    finally:
        await ep.stop()

    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 1
    ctx = composed[0].payload.data["context"]
    assert ctx["calendar"] == {"key": "value"}
    assert "_errors" in ctx
    assert "stub.boom" in ctx["_errors"]
    assert "boom" in ctx["_errors"]["stub.boom"]["error"]


@pytest.mark.asyncio
async def test_scope_and_when_default_when_omitted(playbook_path, gather_yaml, fetcher_catalog):
    """``scope`` and ``when`` are optional. Missing scope → None; missing
    when → server-side now() (we just check it parses to a datetime that's
    sane)."""
    before = datetime.now(UTC)
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator", scope=None, when=None)
        await ep.deliver(req)
    finally:
        await ep.stop()
    after = datetime.now(UTC)

    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 1
    data = composed[0].payload.data
    assert data["scope"] is None
    # ``when`` is an ISO string; parsing it must yield a datetime in [before, after].
    parsed = datetime.fromisoformat(data["when"])
    assert before <= parsed <= after


@pytest.mark.asyncio
async def test_target_agent_from_metadata_overrides_default(
    playbook_path, gather_yaml, fetcher_catalog
):
    """The request's ``metadata.target_agent`` wins over the orchestrator's
    ``default_target_agent``. With no default and no metadata, the orchestrator
    refuses to publish."""
    # Case 1: metadata wins over default.
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator", target_agent="agent-bob")
        await ep.deliver(req)
    finally:
        await ep.stop()
    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 1
    assert composed[0].to == "agent-bob"

    # Case 2: no metadata, no default → no ComposeBrief published.
    handle2 = _RecordingHandle()
    ep2 = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent=None,
    )
    await ep2.start(handle2)
    try:
        req2 = _make_brief_request(to="briefs.orchestrator", env_id="no-target")
        await ep2.deliver(req2)
    finally:
        await ep2.stop()
    assert "no-target" in handle2.acks
    assert [e for e in handle2.published if e.payload.type == "ComposeBrief"] == []


@pytest.mark.asyncio
async def test_empty_string_target_agent_metadata_is_rejected(
    playbook_path, gather_yaml, fetcher_catalog
):
    """I1: ``metadata.target_agent=""`` (or whitespace-only) is a malformed
    request — it must NOT silently fall through to ``default_target_agent``.
    The orchestrator catches the ValueError at the outer try/except so deliver
    returns normally, but no ComposeBrief is published."""
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",  # would silently win in the buggy version
    )
    await ep.start(handle)
    try:
        # Empty string.
        req_empty = _make_brief_request(
            to="briefs.orchestrator", target_agent="", env_id="empty-target"
        )
        await ep.deliver(req_empty)
        # Whitespace-only.
        req_blank = _make_brief_request(
            to="briefs.orchestrator", target_agent="   ", env_id="blank-target"
        )
        await ep.deliver(req_blank)
    finally:
        await ep.stop()

    # Both envelopes ack'd; neither produced a ComposeBrief.
    assert "empty-target" in handle.acks
    assert "blank-target" in handle.acks
    assert [e for e in handle.published if e.payload.type == "ComposeBrief"] == []
    # The session registry stays empty — nothing was stored.
    assert ep.sessions == {}


@pytest.mark.asyncio
async def test_session_not_stored_when_publish_raises(playbook_path, gather_yaml, fetcher_catalog):
    """I2: if ``bus.publish`` raises (MailboxFull, unregistered recipient,
    etc.), the session must NOT be stored — the agent never receives the
    token, so a stored session would just leak in-flight state."""

    class _PublishOnceFails(_RecordingHandle):
        """Recording handle whose first publish raises and second succeeds."""

        def __init__(self):
            super().__init__()
            self._failed_once = False

        async def publish(self, envelope, to=None):
            if not self._failed_once:
                self._failed_once = True
                raise RuntimeError("MailboxFull simulation")
            await super().publish(envelope, to)

    handle = _PublishOnceFails()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        # First request: publish raises → session must not be stored.
        req1 = _make_brief_request(to="briefs.orchestrator", env_id="boom-1")
        await ep.deliver(req1)
        assert ep.sessions == {}, "session must not leak when publish raises"

        # Second request: publish succeeds → session is stored as normal.
        req2 = _make_brief_request(to="briefs.orchestrator", env_id="ok-1")
        await ep.deliver(req2)
    finally:
        await ep.stop()

    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 1
    token = composed[0].payload.data["session_token"]
    assert token in ep.sessions
    # Only the successful session is stored — not two.
    assert len(ep.sessions) == 1


@pytest.mark.asyncio
async def test_session_tokens_unique_and_registered(playbook_path, gather_yaml, fetcher_catalog):
    """Each BriefRequest produces a fresh 32-char hex token; the same token
    appears in the published ComposeBrief; the session is queryable through
    the registry seam (``ep.sessions``)."""
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    await ep.start(handle)
    try:
        req1 = _make_brief_request(to="briefs.orchestrator", env_id="r1")
        req2 = _make_brief_request(to="briefs.orchestrator", env_id="r2")
        await ep.deliver(req1)
        await ep.deliver(req2)
    finally:
        await ep.stop()

    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 2
    token1 = composed[0].payload.data["session_token"]
    token2 = composed[1].payload.data["session_token"]
    assert token1 != token2

    # Both sessions queryable via the registry seam.
    assert token1 in ep.sessions
    assert token2 in ep.sessions
    s1 = ep.sessions[token1]
    assert isinstance(s1, ComposeSession)
    assert s1.brief_type == "morning_brief"
    assert s1.target_agent == "pepper"
    assert s1.playbook_path == playbook_path
    # Correlation_id stored on the session matches the originating request.
    assert s1.correlation_id == req1.correlation_id


@pytest.mark.asyncio
async def test_registry_seam_returns_session_for_token(playbook_path, gather_yaml, fetcher_catalog):
    """T10 seam: ``ep.registry`` is a SessionRegistry and ``registry.get(token)``
    returns the same in-flight ComposeSession that was published. T13 (submit
    handler) uses this seam to consume sessions one-shot.
    """
    handle = _RecordingHandle()
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
    )
    assert isinstance(ep.registry, SessionRegistry)

    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator", env_id="reg-1")
        await ep.deliver(req)
    finally:
        await ep.stop()

    composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
    assert len(composed) == 1
    token = composed[0].payload.data["session_token"]

    # Registry resolves the same session as the back-compat shim.
    via_registry = ep.registry.get(token)
    via_shim = ep.sessions[token]
    assert via_registry is via_shim
    assert via_registry.brief_type == "morning_brief"
    # T10 added these fields — confirm they're populated.
    assert via_registry.consumed is False
    assert via_registry.created_at is not None
    assert via_registry.colors_palette  # populated from playbook
    assert via_registry.sections  # resolved sections list


@pytest.mark.asyncio
async def test_sessions_shim_excludes_expired_sessions(playbook_path, gather_yaml, fetcher_catalog):
    """The deprecated ``sessions`` shim must filter expired sessions, not
    just consumed. Otherwise callers see tokens the registry would refuse
    on lookup. Routes through ``registry.items_active`` for honest output.
    """
    handle = _RecordingHandle()
    # Tiny TTL so a single asyncio.sleep ages the session past expiry.
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=playbook_path.parent,
        vars_map={"gather_config_path": str(gather_yaml)},
        fetcher_catalog=fetcher_catalog,
        default_target_agent="pepper",
        session_ttl_seconds=0.01,
    )
    await ep.start(handle)
    try:
        req = _make_brief_request(to="briefs.orchestrator", env_id="exp-1")
        await ep.deliver(req)
        # Confirm the session is briefly visible before expiring.
        composed = [e for e in handle.published if e.payload.type == "ComposeBrief"]
        token = composed[0].payload.data["session_token"]
        # Sleep past the TTL.
        await asyncio.sleep(0.05)
        # Shim must exclude the expired session — same contract as the registry.
        assert token not in ep.sessions
        assert ep.sessions == {}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_before_start_raises():
    """Calling deliver() without start() is a programmer error — fail loud
    so misuse is obvious in tests/integration."""
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=Path("/nonexistent"),
        vars_map={},
        fetcher_catalog={},
    )
    env = Envelope(
        id="x",
        correlation_id="c",
        from_="x",
        to="briefs.orchestrator",
        kind="Event",
        payload=EventPayload(type="BriefRequest", data={}),
        created_at=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError, match="not started"):
        await ep.deliver(env)


# ---- T16 constructor branches ----

# A self-contained stub fetcher module for the ``fetcher_paths`` happy-path.
# Kept inline (rather than imported from test_briefs_cli.py) so this test
# file remains independently runnable. The class layout matches the
# ``Fetcher`` protocol (``type_id``, ``namespace``, async ``fetch``) so
# ``discover_implementations`` picks it up.
STUB_FETCHER_SOURCE = textwrap.dedent(
    '''
    """Stub fetcher used by the orchestrator constructor tests."""

    from datetime import datetime


    class StubFetcher:
        type_id = "stub.ctor"
        namespace = "stub_ctor_ns"

        def __init__(self) -> None:
            pass

        async def fetch(self, config: dict, when: datetime) -> dict:
            return {"echo": config.get("echo", "default")}
    '''
).strip()


class TestBriefsOrchestratorEndpointConstruction:
    """T16 added two constructor aliases — ``vars=`` (alias for ``vars_map=``)
    and ``fetcher_paths=`` (alias for ``fetcher_catalog=``) — with
    exactly-one-of validation for each pair. These tests pin the runner-
    facing path so a future refactor that drops/renames either alias is
    caught directly rather than via downstream integration failure."""

    def test_vars_alias_constructs_and_populates_vars_map(self, tmp_path: Path) -> None:
        """Passing ``vars={...}`` (the yaml-idiomatic alias) instead of
        ``vars_map={...}`` constructs successfully, and the orchestrator's
        internal ``_vars_map`` reflects the value verbatim. ``fetcher_catalog``
        is still required, so we pass an empty catalog to satisfy that branch."""
        ep = BriefsOrchestratorEndpoint(
            name="briefs.orchestrator",
            playbooks_path=tmp_path,
            vars={"agent_root": "/tmp/x"},
            fetcher_catalog={},
        )
        assert ep._vars_map == {"agent_root": "/tmp/x"}

    def test_fetcher_paths_discovers_implementations(self, tmp_path: Path) -> None:
        """Passing ``fetcher_paths=[<dir>]`` runs ``discover_implementations``
        on construction and the resulting catalog is keyed by ``type_id``.
        The stub module is written to a fresh tmp directory so the discovery
        is deterministic and isolated from the rest of the test suite."""
        fetcher_dir = tmp_path / "fetchers"
        fetcher_dir.mkdir()
        (fetcher_dir / "stub_fetcher.py").write_text(STUB_FETCHER_SOURCE + "\n", encoding="utf-8")

        ep = BriefsOrchestratorEndpoint(
            name="briefs.orchestrator",
            playbooks_path=tmp_path,
            vars_map={},
            fetcher_paths=[fetcher_dir],
        )

        assert "stub.ctor" in ep._fetcher_catalog
        cls = ep._fetcher_catalog["stub.ctor"]
        # Round-trip: discovered class is the actual stub (matched on the
        # public protocol surface).
        assert getattr(cls, "type_id", None) == "stub.ctor"
        assert getattr(cls, "namespace", None) == "stub_ctor_ns"

    def test_fetcher_paths_auto_loads_built_ins(self, tmp_path: Path) -> None:
        """Built-in fetchers (``cli``, ``filesystem_read``, ``now``) ship
        inside the package and are auto-discovered for any ``fetcher_paths``
        caller. Operators don't have to copy package source into their own
        fetcher dir — mirrors the destinations contract. An empty
        ``fetcher_paths`` is enough to get the three built-ins."""
        fetcher_dir = tmp_path / "fetchers"
        fetcher_dir.mkdir()  # empty — no operator fetchers

        ep = BriefsOrchestratorEndpoint(
            name="briefs.orchestrator",
            playbooks_path=tmp_path,
            vars_map={},
            fetcher_paths=[fetcher_dir],
        )

        assert "cli" in ep._fetcher_catalog
        assert "filesystem_read" in ep._fetcher_catalog
        assert "now" in ep._fetcher_catalog

    def test_both_vars_and_vars_map_raises(self, tmp_path: Path) -> None:
        """Setting both ``vars=`` and ``vars_map=`` is ambiguous; the
        orchestrator must refuse rather than silently pick one. The error
        message names both flag identifiers so operators know what to drop."""
        with pytest.raises(
            ValueError,
            match=r"pass exactly one of 'vars_map' or 'vars', not both",
        ):
            BriefsOrchestratorEndpoint(
                name="briefs.orchestrator",
                playbooks_path=tmp_path,
                vars_map={"a": "1"},
                vars={"b": "2"},
                fetcher_catalog={},
            )

    def test_neither_vars_nor_vars_map_raises(self, tmp_path: Path) -> None:
        """Omitting both ``vars=`` and ``vars_map=`` is also rejected — the
        caller must pick exactly one. ``fetcher_catalog={}`` satisfies the
        sibling exactly-one-of so the error we catch is the vars one."""
        with pytest.raises(
            ValueError,
            match=r"'vars_map' \(or 'vars' alias\) is required",
        ):
            BriefsOrchestratorEndpoint(
                name="briefs.orchestrator",
                playbooks_path=tmp_path,
                fetcher_catalog={},
            )

    def test_both_fetcher_catalog_and_fetcher_paths_raises(self, tmp_path: Path) -> None:
        """Setting both ``fetcher_catalog=`` and ``fetcher_paths=`` is the
        same kind of ambiguity (would the paths *add* to the catalog or
        *shadow* it?). The orchestrator refuses with a message that names
        both flags."""
        fetcher_dir = tmp_path / "fetchers"
        fetcher_dir.mkdir()
        with pytest.raises(
            ValueError,
            match=r"pass exactly one of 'fetcher_catalog' or 'fetcher_paths', not both",
        ):
            BriefsOrchestratorEndpoint(
                name="briefs.orchestrator",
                playbooks_path=tmp_path,
                vars_map={},
                fetcher_catalog={},
                fetcher_paths=[fetcher_dir],
            )

    def test_neither_fetcher_catalog_nor_fetcher_paths_raises(self, tmp_path: Path) -> None:
        """Omitting both fetcher inputs leaves the orchestrator with nothing
        to dispatch against; the constructor refuses up-front instead of
        raising at first BriefRequest."""
        with pytest.raises(
            ValueError,
            match=r"one of 'fetcher_catalog' or 'fetcher_paths' is required",
        ):
            BriefsOrchestratorEndpoint(
                name="briefs.orchestrator",
                playbooks_path=tmp_path,
                vars_map={},
            )
