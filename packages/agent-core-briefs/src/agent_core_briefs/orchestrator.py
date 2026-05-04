"""BriefsOrchestratorEndpoint — wake-step orchestration for the brief framework.

The orchestrator is a bus endpoint (same shape as
:class:`agent_core.endpoints.handoff_jobs.HandoffJobsEndpoint`) that listens
for ``BriefRequest`` events, runs the playbook's gather config against the
registered fetcher catalog, and publishes a ``ComposeBrief`` event back to the
target agent. The agent then composes the brief content and submits it via T13.

Wire shapes
-----------
**Consumed** — ``BriefRequest`` event envelope::

    Envelope(
        kind="Event",
        payload=EventPayload(
            type="BriefRequest",
            data={
                "brief_type": "morning_brief",   # required
                "scope": "today",                # optional
                "when": "<ISO timestamp>",       # optional, defaults to now()
            },
        ),
        to="<orchestrator endpoint name>",
        metadata={"target_agent": "<agent>"},   # optional
        ...
    )

**Produced** — ``ComposeBrief`` event envelope::

    Envelope(
        kind="Event",
        payload=EventPayload(
            type="ComposeBrief",
            data={
                "brief_type": str,
                "scope": str | None,
                "when": str,                              # ISO timestamp
                "session_token": str,                     # 32-char hex
                "playbook_path": str,                     # absolute
                "voice": str,                             # from playbook.voice
                "context": dict,                          # from gather_context
                "sections_required": list[str],
                "sections_optional": list[str],
                "sections_conditional_active": list[str],
            },
        ),
        to="<target_agent>",
        correlation_id=<request.correlation_id>,
        ...
    )

Gather config YAML shape
------------------------
The playbook's ``gather_config`` metadata points at a YAML file with this
shape (var-substituted via :func:`agent_core_briefs.config.substitute_vars`)::

    fetchers:
      - type: stub.simple        # type_id key in the fetcher catalog
        namespace: calendar      # routing namespace in the gathered context
        timeout_seconds: 30      # optional; falls back to default_timeout_seconds
        config:                  # passed verbatim to fetcher.fetch
          key: value

Each fetcher entry is resolved against ``fetcher_catalog`` (built externally
via T3's ``discover_implementations``); the namespace from the gather config
overrides the fetcher class's ``namespace`` attribute via T4's
``FetcherInvocation.namespace_override``.

T10 will replace the in-memory ``_sessions`` dict with a TTL-aware
``SessionRegistry``; T13 will add a submit handler that consumes the session
and routes through destinations. T14 wires the ComposeBrief envelope to a
direct MCP-tool surface so agents can invoke ``compose_brief`` themselves.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agent_core.bus.envelope import Envelope, EventPayload
from agent_core_briefs.config import substitute_vars
from agent_core_briefs.engine import FetcherInvocation, gather_context
from agent_core_briefs.playbook import (
    Playbook,
    parse_playbook,
    resolve_conditional_sections,
)
from agent_core_briefs.protocol import Fetcher

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


@dataclass
class ComposeSession:
    """In-flight compose session tracked by the orchestrator.

    T10 will move this to a dedicated session registry with TTL + agent-tool
    integration. For v1 the orchestrator keeps sessions in a plain dict
    keyed by ``session_token`` — see :attr:`BriefsOrchestratorEndpoint.sessions`.
    """

    brief_type: str
    playbook_path: Path
    voice: str
    scope: str | None
    when: datetime
    context: dict[str, Any]
    sections_required: list[str]
    sections_optional: list[str]
    sections_conditional_active: list[str]
    target_agent: str
    correlation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BriefsOrchestratorEndpoint:
    """Bus endpoint: receives ``BriefRequest`` events, runs gather, publishes
    ``ComposeBrief`` to the target agent.

    Constructor args:
        name: Bus endpoint name (e.g., ``"briefs.orchestrator"``).
        playbooks_path: Directory of playbook ``.md`` files. A request with
            ``brief_type=X`` loads ``<playbooks_path>/X.md``.
        vars_map: ``${var}`` substitutions for both the playbook (T2) and
            the gather config YAML.
        fetcher_catalog: Mapping of ``type_id`` → ``Fetcher`` class. Built
            externally via :func:`agent_core_briefs.loader.discover_implementations`.
        default_target_agent: Fallback when the request envelope's
            ``metadata.target_agent`` is absent. ``None`` means a request
            without metadata cannot be routed and is logged as a failure.
        default_timeout_seconds: Per-fetcher timeout when the gather config
            entry doesn't specify one. Defaults to 300s (5 minutes).
    """

    def __init__(
        self,
        *,
        name: str,
        playbooks_path: Path | str,
        vars_map: dict[str, str],
        fetcher_catalog: dict[str, type[Fetcher]],
        default_target_agent: str | None = None,
        default_timeout_seconds: float = 300.0,
    ):
        self.name = name
        self._playbooks_path = Path(playbooks_path)
        self._vars_map = dict(vars_map)
        self._fetcher_catalog = dict(fetcher_catalog)
        self._default_target_agent = default_target_agent
        self._default_timeout_seconds = default_timeout_seconds
        self._handle: BusHandle | None = None
        self._sessions: dict[str, ComposeSession] = {}

    @property
    def sessions(self) -> dict[str, ComposeSession]:
        """Read-only view of in-flight compose sessions.

        T10 replaces this with a real ``SessionRegistry`` (TTL, eviction,
        thread-safety). Tests use this to confirm session persistence across
        the publish boundary; production callers should NOT mutate it.
        """
        return self._sessions

    # ---- Bus endpoint protocol ----
    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        log.info("BriefsOrchestratorEndpoint(name=%s) started", self.name)

    async def deliver(self, envelope: Envelope) -> None:
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")

        # Always ack first — bus delivery is at-least-once; we don't want
        # redelivery for envelopes we've already inspected. Failures inside
        # the BriefRequest handler are logged but do not requeue.
        await self._handle.ack(envelope.id)

        # Only act on BriefRequest events. Everything else (TextMessage,
        # other Event types) is silently ignored beyond the ack.
        if envelope.kind != "Event" or envelope.payload.type != "BriefRequest":
            return

        try:
            await self._handle_brief_request(envelope)
        except Exception:
            # TODO(T9): consider publishing a BriefFailed event for visibility.
            # v1 logs only — T10/T13 introduce richer failure surfaces.
            log.exception(
                "BriefsOrchestrator: BriefRequest handling failed for envelope %s",
                envelope.id,
            )

    async def stop(self) -> None:
        self._handle = None
        log.info("BriefsOrchestratorEndpoint(name=%s) stopped", self.name)

    # ---- BriefRequest handling ----
    async def _handle_brief_request(self, envelope: Envelope) -> None:
        assert self._handle is not None  # caller guarantees via deliver()

        data = dict(envelope.payload.data or {})
        brief_type = data.get("brief_type")
        if not isinstance(brief_type, str) or not brief_type:
            raise ValueError(f"BriefRequest envelope {envelope.id} is missing 'brief_type'")

        # I1: distinguish "absent" from "explicitly empty". A caller setting
        # metadata.target_agent="" (client bug or attempted force-default)
        # must fail loud — not silently fall through to default_target_agent.
        metadata_target = envelope.metadata.get("target_agent")
        if metadata_target is not None:
            if not isinstance(metadata_target, str) or not metadata_target.strip():
                raise ValueError(
                    f"BriefRequest envelope {envelope.id}: metadata.target_agent must be "
                    "a non-empty string when present"
                )
            target_agent = metadata_target
        elif self._default_target_agent:
            target_agent = self._default_target_agent
        else:
            raise ValueError(
                f"BriefRequest envelope {envelope.id}: no target_agent in metadata and "
                "no default_target_agent configured"
            )

        scope = data.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ValueError(
                f"BriefRequest envelope {envelope.id} has non-string 'scope': {scope!r}"
            )

        when = self._resolve_when(data.get("when"), envelope.id)

        playbook_path = self._playbooks_path / f"{brief_type}.md"
        if not playbook_path.exists():
            raise FileNotFoundError(
                f"playbook for brief_type={brief_type!r} not found at {playbook_path}"
            )
        playbook = parse_playbook(playbook_path, vars_map=self._vars_map)

        invocations = self._build_invocations(playbook)
        context = await gather_context(invocations, when=when)

        sections_active = resolve_conditional_sections(playbook.conditional_sections, context)
        required, optional = self._split_sections(playbook)

        session_token = secrets.token_hex(16)
        session = ComposeSession(
            brief_type=brief_type,
            playbook_path=playbook_path,
            voice=playbook.voice,
            scope=scope,
            when=when,
            context=context,
            sections_required=required,
            sections_optional=optional,
            sections_conditional_active=sections_active,
            target_agent=target_agent,
            correlation_id=envelope.correlation_id,
            metadata=dict(envelope.metadata),
        )

        compose_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=envelope.correlation_id,
            to=target_agent,
            kind="Event",
            payload=EventPayload(
                type="ComposeBrief",
                data={
                    "brief_type": brief_type,
                    "scope": scope,
                    "when": when.isoformat(),
                    "session_token": session_token,
                    "playbook_path": str(playbook_path),
                    "voice": playbook.voice,
                    "context": context,
                    "sections_required": required,
                    "sections_optional": optional,
                    "sections_conditional_active": sections_active,
                },
            ),
            # I4: Request metadata is preserved on the session for T13 (submit
            # handler) but intentionally not echoed onto the ComposeBrief
            # envelope — operator tags / trace ids belong on the operator-side
            # session, not in the agent-facing wake message. T14 (MCP) reads
            # from the session.
            metadata={"brief_request_id": envelope.id},
            created_at=datetime.now(UTC),
        )
        # I2: publish first, then store. If publish raises (MailboxFull,
        # unregistered recipient, etc.), the session is never stored — the
        # agent never receives the token and could never submit anyway, so
        # storing the session would just leak in-flight state.
        await self._handle.publish(compose_env)
        self._sessions[session_token] = session
        log.info(
            "BriefsOrchestrator: composed brief brief_type=%s session_token=%s target=%s",
            brief_type,
            session_token,
            target_agent,
        )

    # ---- helpers ----
    def _build_invocations(self, playbook: Playbook) -> list[FetcherInvocation]:
        """Load + var-substitute the gather config; build invocations.

        Raises ``FileNotFoundError`` if the playbook references a gather
        config that doesn't exist; raises ``ValueError`` for unknown
        ``type`` ids or malformed entries. Both surface as the
        ``log.exception`` in :meth:`deliver`.
        """
        gather_path_str = playbook.gather_config_path
        if not gather_path_str:
            # Brief without a gather config is legal — empty context is fine.
            return []

        gather_path = Path(gather_path_str)
        if not gather_path.exists():
            raise FileNotFoundError(f"gather_config not found: {gather_path}")

        raw = yaml.safe_load(gather_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"gather_config {gather_path} must be a mapping at top level")
        substituted = substitute_vars(raw, self._vars_map)
        entries = substituted.get("fetchers") or []
        if not isinstance(entries, list):
            raise ValueError(f"gather_config {gather_path} 'fetchers' must be a list")

        invocations: list[FetcherInvocation] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(f"gather_config {gather_path} fetcher #{idx} is not a mapping")
            type_id = entry.get("type")
            if not isinstance(type_id, str) or not type_id:
                raise ValueError(f"gather_config {gather_path} fetcher #{idx} missing 'type'")
            namespace = entry.get("namespace")
            if not isinstance(namespace, str) or not namespace:
                raise ValueError(
                    f"gather_config {gather_path} fetcher #{idx} (type={type_id!r}) "
                    "missing 'namespace'"
                )
            cls = self._fetcher_catalog.get(type_id)
            if cls is None:
                raise ValueError(
                    f"gather_config {gather_path} fetcher #{idx} references unknown "
                    f"type_id={type_id!r} (catalog keys: {sorted(self._fetcher_catalog)})"
                )
            cfg = entry.get("config") or {}
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"gather_config {gather_path} fetcher #{idx} 'config' must be a mapping"
                )
            timeout = entry.get("timeout_seconds", self._default_timeout_seconds)
            try:
                timeout_f = float(timeout)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"gather_config {gather_path} fetcher #{idx} 'timeout_seconds' "
                    f"must be numeric, got {timeout!r}"
                ) from exc

            invocations.append(
                FetcherInvocation(
                    fetcher=cls(),
                    config=cfg,
                    timeout_seconds=timeout_f,
                    namespace_override=namespace,
                )
            )
        return invocations

    @staticmethod
    def _resolve_when(when_raw: Any, envelope_id: str) -> datetime:
        """Normalize the ``when`` field on a BriefRequest. Missing → now(UTC).

        Accepts ISO-8601 strings (with or without timezone) and ``datetime``
        instances. Naive datetimes are assumed UTC — the brief framework
        operates on absolute time, never local time.
        """
        if when_raw is None:
            return datetime.now(UTC)
        if isinstance(when_raw, datetime):
            return when_raw if when_raw.tzinfo else when_raw.replace(tzinfo=UTC)
        if isinstance(when_raw, str):
            try:
                parsed = datetime.fromisoformat(when_raw)
            except ValueError as exc:
                raise ValueError(
                    f"BriefRequest envelope {envelope_id} has unparseable 'when': {when_raw!r}"
                ) from exc
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        raise ValueError(
            f"BriefRequest envelope {envelope_id} has 'when' of unsupported "
            f"type {type(when_raw).__name__}: {when_raw!r}"
        )

    @staticmethod
    def _split_sections(playbook: Playbook) -> tuple[list[str], list[str]]:
        """Partition the playbook's static sections into required + optional id lists.

        Conditional sections are NOT included here; they go through the
        separate ``sections_conditional_active`` channel produced by
        :func:`resolve_conditional_sections`.
        """
        required = [s.section_id for s in playbook.sections if s.required]
        optional = [s.section_id for s in playbook.sections if not s.required]
        return required, optional
