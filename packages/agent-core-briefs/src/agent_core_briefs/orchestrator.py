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

T10 introduced :class:`agent_core_briefs.session.SessionRegistry` (TTL +
one-shot consume) and the agent-tool surface in
:mod:`agent_core_briefs.tools`. T13 adds a submit handler that consumes
the session via ``registry.consume`` and routes through destinations.
T14 wires the ComposeBrief envelope to a direct MCP-tool surface so
agents can invoke ``compose_brief`` themselves.
"""

from __future__ import annotations

import logging
import uuid
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
    resolve_colors_for_sections,
    resolve_conditional_sections,
)
from agent_core_briefs.protocol import Fetcher
from agent_core_briefs.session import ComposeSession, SessionRegistry

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


# Backward-compat re-export: existing T9 tests import ComposeSession from
# orchestrator. T10 moved the dataclass to session.py — keep the import
# alias so consumers don't break.
__all__ = ["BriefsOrchestratorEndpoint", "ComposeSession"]


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
        session_ttl_seconds: float = 1800.0,
    ):
        self.name = name
        self._playbooks_path = Path(playbooks_path)
        self._vars_map = dict(vars_map)
        self._fetcher_catalog = dict(fetcher_catalog)
        self._default_target_agent = default_target_agent
        self._default_timeout_seconds = default_timeout_seconds
        self._handle: BusHandle | None = None
        self._registry = SessionRegistry(ttl_seconds=session_ttl_seconds)

    @property
    def default_target_agent(self) -> str | None:
        """Read-only access to the default target agent.

        T14's ``compose_brief`` MCP tool reads this to fill in the
        target_agent on a self-launched session: there is no envelope
        metadata to consult, so the default is the only routing source.
        ``None`` means self-launch is not configured for this orchestrator.
        """
        return self._default_target_agent

    @property
    def registry(self) -> SessionRegistry:
        """Read-only access to the session registry.

        T13 (submit) and T14 (MCP) use this seam to look up sessions by
        token. Tests can also assert against ``registry.get(token)`` for
        the full session view (including the moved-here ``created_at``
        and ``consumed`` fields).
        """
        return self._registry

    @property
    def sessions(self) -> dict[str, ComposeSession]:
        """Deprecated. Returns a snapshot of active (non-consumed, non-expired) sessions.

        Preserved for T9 test API compatibility; T13+ should use
        :attr:`registry` directly. Routes through
        :meth:`SessionRegistry.items_active` so expired sessions are
        excluded — callers should never see a token the registry would
        refuse on lookup.
        """
        return dict(self._registry.items_active())

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

        # T14: gather + resolve + build-session is shared with the
        # self-launch path (compose_brief MCP tool). build_compose_session
        # creates and registers the ComposeSession and returns the
        # ComposeBrief data dict so we can pack it into the bus envelope.
        session_token, compose_data = await self.build_compose_session(
            brief_type=brief_type,
            scope=scope,
            when=when,
            target_agent=target_agent,
            correlation_id=envelope.correlation_id,
            request_metadata=dict(envelope.metadata),
        )

        compose_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=envelope.correlation_id,
            to=target_agent,
            kind="Event",
            payload=EventPayload(
                type="ComposeBrief",
                data=compose_data,
            ),
            # I4: Request metadata is preserved on the session for T13 (submit
            # handler) but intentionally not echoed onto the ComposeBrief
            # envelope — operator tags / trace ids belong on the operator-side
            # session, not in the agent-facing wake message. T14 (MCP) reads
            # from the session.
            metadata={"brief_request_id": envelope.id},
            created_at=datetime.now(UTC),
        )
        # I2: publish first, drop the registered session if publish raises.
        # The registry's create() already inserted the session; if we don't
        # rewind, a publish failure leaves a dangling session token the agent
        # never received. Drop on failure preserves the I2 invariant.
        try:
            await self._handle.publish(compose_env)
        except Exception:
            # Best-effort cleanup; delete() is idempotent so a no-op return
            # (e.g., cleanup_expired ran between create and publish) is fine.
            self._registry.delete(session_token)
            raise
        log.info(
            "BriefsOrchestrator: composed brief brief_type=%s session_token=%s target=%s",
            brief_type,
            session_token,
            target_agent,
        )

    # ---- T14: shared gather-and-build helper ----
    async def build_compose_session(
        self,
        *,
        brief_type: str,
        scope: str | None,
        when: datetime,
        target_agent: str,
        correlation_id: str,
        request_metadata: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Run gather → resolve → build-and-register a ComposeSession.

        Both the bus-driven ``BriefRequest`` path and the self-launch
        ``compose_brief`` MCP path share this helper. The bus path then
        wraps the returned data dict into a ``ComposeBrief`` envelope and
        publishes; the self-launch path returns the dict directly to the
        caller.

        The session is created and registered in :attr:`registry` before
        return — callers are responsible for rewinding via
        :meth:`SessionRegistry.delete` if a downstream step fails.

        Returns:
            ``(session_token, compose_data)`` where ``compose_data`` is the
            payload dict that goes into a ``ComposeBrief`` envelope's
            ``data`` field (brief_type, scope, when, session_token,
            playbook_path, voice, context, sections_*).

        Raises:
            FileNotFoundError: playbook for ``brief_type`` not found.
            Various ValueErrors: malformed gather config, unknown
                fetcher type ids, etc. — same surface as the bus-driven
                path.
        """
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

        # Resolve dynamic + palette-name colors at session-build time so the
        # T10 agent-tool surface (get_section_spec) doesn't have to evaluate
        # simpleeval on every call.
        resolved_sections = resolve_colors_for_sections(
            playbook.sections, playbook.colors, context=context
        )

        # T13/C1: also materialize conditional sections that gated truthy
        # for this brief — submit-time validation (T13 validators) needs
        # the full SectionSpec to enforce required-field/max-chars checks
        # against the agent's submitted conditional sections. Apply color
        # resolution here too so conditional sections with dynamic / palette
        # colors are treated symmetrically with static sections.
        active_conditional_ids = set(sections_active)
        active_conditional_specs = [
            s for s in playbook.conditional_sections if s.section_id in active_conditional_ids
        ]
        resolved_conditional = resolve_colors_for_sections(
            active_conditional_specs, playbook.colors, context=context
        )

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
            correlation_id=correlation_id,
            metadata=dict(request_metadata or {}),
            sections=resolved_sections,
            colors_palette=dict(playbook.colors),
            created_at=datetime.now(UTC),
            # T13: thread the playbook destinations through so the submit
            # handler can fan out without re-parsing the playbook. Each
            # entry is a dict like ``{"type": "discord_embed", "config": {...}}``.
            destinations=[dict(d) for d in playbook.destinations],
            conditional_sections=resolved_conditional,
        )
        session_token = self._registry.create(session)

        compose_data: dict[str, Any] = {
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
        }
        return session_token, compose_data

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
