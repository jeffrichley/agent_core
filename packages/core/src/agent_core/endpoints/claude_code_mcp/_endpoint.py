"""ClaudeCodeMCPEndpoint — bus endpoint that hosts a FastMCP server.

Each instance corresponds to one named agent on the bus. The agent's
Claude Code instance connects to `http://<bind_host>:<port><mount>` via
Streamable HTTP. Identity is path-based — the URL path *is* the agent's
name on the bus, set by the runner via the `name` kwarg.

Tools (per the channel-bus spec § MCP transport implementation):
    send, list_endpoints, describe_endpoint, list_pending,
    handle, ack, nack

Inbound envelopes flow to the connected Claude Code session via MCP
notifications on the SSE stream. If no session is currently connected,
deliver() usually raises EndpointUnavailable so the bus queues the envelope;
routine green Acknowledgments for recent agent outbounds are auto-handled
without requiring a session (see deliver() docstring).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from mcp.shared.session import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.bus.protocol import EndpointUnavailable
from agent_core.bus_log import bootstrap_default_projectors, iter_for_agent
from agent_core.bus_log.writer import default_log_root

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle
    from agent_core.mcp_audit.writer import MCPAuditWriter

from ._session import SessionRegistry
from ._tools import register_tools

log = logging.getLogger(__name__)
_META_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Missing-ack timers and registry TTL must stay bounded (YAML / metadata cannot DoS the loop).
_MISSING_ACK_DELAY_MAX_SECONDS = 86400.0  # 24 hours
_OUTBOUND_REGISTRY_TTL_MIN_SECONDS = 1.0
_OUTBOUND_REGISTRY_TTL_MAX_SECONDS = 86400.0 * 366  # ~one year


class ClaudeCodeMCPEndpoint:
    """Bus endpoint backed by a FastMCP server, served on the shared HTTP host.

    Public seams for plugin integration:

    - ``tool_mounters`` (constructor kwarg): list of callables invoked at
      construction time with this endpoint's ``FastMCP`` instance. Each
      mounter registers its tools via the standard ``mcp.tool(...)``
      decorator. Used by in-process callers that already know the tool
      surface at construction time (existing T14 path).
    - ``deferred_tool_mounters`` (public attribute): list of callables
      invoked once during :meth:`start` after this endpoint has been
      handed its ``BusHandle``. Each callable receives that handle as
      its sole argument and is responsible for wiring tools that need
      to publish onto the bus (e.g., the briefs framework's
      ``submit_brief`` tool calls into destinations that publish
      Discord embeds via the bus). The cross-endpoint wiring hook
      (``wire_endpoints_after_registration``) appends to this list during
      runner construction; see :mod:`agent_core_briefs.plugin` for the
      production caller.
    """

    _URGENCY_RANK = {"red": 0, "yellow": 1, "green": 2}
    _URGENCY_ORDER = ["red", "yellow", "green"]

    def __init__(
        self,
        *,
        name: str,
        mount: str,
        notify_broker: NotificationBroker | None = None,
        wake_on_all_acknowledgments: bool = False,
        outbound_registry_ttl_seconds: float = 900.0,
        missing_ack_default_seconds: float = 30.0,
        max_tracked_outbounds: int = 10_000,
        bus_log_root: Path | None = None,
        tool_mounters: list[Callable[[FastMCP], None]] | None = None,
    ):
        self.name = name
        self._bus_log_root = (
            Path(bus_log_root).expanduser() if bus_log_root is not None else default_log_root()
        )
        self._projectors_bootstrapped = False
        self.mount = mount
        self.wake_on_all_acknowledgments = wake_on_all_acknowledgments
        ttl = float(outbound_registry_ttl_seconds)
        self._outbound_registry_ttl_seconds = max(
            _OUTBOUND_REGISTRY_TTL_MIN_SECONDS,
            min(ttl, _OUTBOUND_REGISTRY_TTL_MAX_SECONDS),
        )
        self._missing_ack_default_seconds = max(
            0.0,
            min(float(missing_ack_default_seconds), _MISSING_ACK_DELAY_MAX_SECONDS),
        )
        self._max_tracked_outbounds = max(1, int(max_tracked_outbounds))
        self._mcp: FastMCP = FastMCP(
            name,
            instructions=(
                f"You are agent '{name}'. The bus pushes you notifications with method "
                '"notifications/claude/channel" when envelopes arrive in your mailbox. '
                'Each notification\'s params contain "content" (a fixed string of the form '
                '"INBOX: pending (<endpoint>)") and "meta" (endpoint, fired_at). The '
                "wake is purely a 'go look' signal — it carries no queue-state. "
                "On receipt: call list_pending() to read the actual envelopes (set "
                "batch_window_seconds=30 to fold human-paced bursts from the same "
                'sender). list_pending returns {"meta": {count, urgency_max, '
                "urgency_counts, by_sender, endpoint, fetched_at}, \"items\": [...]}; "
                "meta is the authoritative aggregate, computed atomically with items. "
                "Process each item, then call handle(envelope_id) on each to ack and "
                "remove from the queue. Send replies via the send tool. "
                "Two combined tools shave the round-trip floor to 2 calls: "
                "consume(batch_window_seconds=30, auto_ack=True) reads + acks "
                "in one call (same shape as list_pending); reply(in_reply_to, "
                "payload) publishes a TextMessage and acks the inbound in one "
                "call, inheriting routing/correlation_id from the inbound and "
                "shallow-merging any metadata override on top. The classic "
                "list_pending / handle / send tools remain available for "
                "power-use (manual triage, non-text replies, partial acks). "
                "Routine green Acknowledgments matching your outbounds (kind="
                "Acknowledgment, urgency=green, note not prefixed 'error:', "
                "in_reply_to set) are auto-cleared by the bus and never reach "
                "your queue or wake you. You see only failures (yellow/red acks, "
                "or notes prefixed 'error:'), other envelope kinds, and missing-ack "
                "timeouts. Auto-clear is shape-based, so it survives daemon restarts."
            ),
        )
        self._handle: BusHandle | None = None
        self._pending: list[Envelope] = []
        self._sessions: set[Any] = set()
        self._notify_debounce_seconds_by_urgency: dict[str, float] = {
            "red": 0.05,
            "yellow": 0.5,
            "green": 1.0,
        }
        self._debounce_task: asyncio.Task | None = None
        self._debounce_deadline: float | None = None
        self._notify_broker = notify_broker
        self._recent_outbound_ids: dict[str, float] = {}
        self._missing_ack_tasks: dict[str, asyncio.Task[None]] = {}
        # Issue #67: cache routing of recently-delivered inbounds so reply()
        # can derive `to`/`metadata`/`urgency`/`correlation_id` even after
        # the inbound has been acked (e.g., by consume(auto_ack=True)).
        # TTL'd alongside the outbound registry — same operational concern.
        self._recent_inbounds: dict[str, dict[str, Any]] = {}
        self._mcp.add_middleware(SessionRegistry(self))
        register_tools(self)
        # T14: plugin-provided tool mounters can register additional MCP
        # tools on this endpoint at construction time. Each mounter is a
        # callable that takes the FastMCP server and adds tools via the
        # standard ``mcp.tool(...)`` decorator. T16 wires the briefs
        # framework's tools through this seam.
        if tool_mounters:
            for mounter in tool_mounters:
                mounter(self._mcp)
        # T19 (cutover #09 follow-up): cross-endpoint wiring populates
        # this list before bus.start. Each callable runs once during
        # :meth:`start` after the BusHandle is set. Used for tools that
        # need to publish onto the bus (e.g., briefs ``submit_brief``).
        self.deferred_tool_mounters: list[Callable[[BusHandle], None]] = []

    def attach_notify_broker(self, broker: NotificationBroker) -> None:
        """Optional runner hook: attach broker after endpoint construction."""
        self._notify_broker = broker

    def attach_audit_writer(
        self,
        writer: MCPAuditWriter,
        skip_tools: frozenset[str],
    ) -> None:
        """Optional runner hook: install the audit middleware on this MCP server.

        Called once during ``configure_endpoint_instance``. Idempotent in
        practice because the runner only calls it when the runner has a
        writer to give and only does so once per endpoint instance.
        """
        from agent_core.mcp_audit.middleware import MCPAuditMiddleware

        self._mcp.add_middleware(
            MCPAuditMiddleware(
                endpoint_name=self.name,
                writer=writer,
                skip_tools=skip_tools,
            )
        )

    async def _show_my_day_impl(
        self,
        *,
        date: str | None = None,
        projected: bool = True,
        limit: int | None = None,
        timezone: str = "US/Eastern",
    ) -> list[dict]:
        """Return today's bus traffic touching this agent.

        Agent identity comes from ``self.name`` — there is no ``agent``
        parameter exposed via MCP. Each ClaudeCodeMCPEndpoint instance
        is bound to one agent at construction; cross-agent leakage is
        prevented by construction, not by trusting the caller.
        """
        if not self._projectors_bootstrapped:
            bootstrap_default_projectors()
            self._projectors_bootstrapped = True
        target = date or datetime.now(UTC).astimezone(ZoneInfo(timezone)).date().isoformat()
        path = self._bus_log_root / f"{target}.jsonl"
        if projected:
            rows = list(iter_for_agent(path, agent=self.name, projected=True, timezone=timezone))
        else:
            rows = [
                env.model_dump(by_alias=True, mode="json")
                for env in iter_for_agent(path, agent=self.name, projected=False)
            ]
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        if limit is not None:
            rows = rows[-limit:]
        return rows

    def _register_session(self, session: Any) -> None:
        """Register a connected ServerSession."""
        before = len(self._sessions)
        self._sessions.add(session)
        if len(self._sessions) != before:
            log.debug("endpoint '%s' registered session count=%d", self.name, len(self._sessions))

    def _unregister_session(self, session: Any) -> None:
        """Unregister a ServerSession."""
        before = len(self._sessions)
        self._sessions.discard(session)
        if len(self._sessions) != before:
            log.debug("endpoint '%s' unregistered session count=%d", self.name, len(self._sessions))

    def _require_handle(self) -> BusHandle:
        """Assert the endpoint has been started; raise RuntimeError otherwise.

        Returns the handle so callers can write:
            handle = self._require_handle()
        """
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")
        return self._handle

    def _clamp_missing_ack_delay(self, seconds: float) -> float:
        s = float(seconds)
        if s != s:  # NaN
            s = self._missing_ack_default_seconds
        return max(0.0, min(s, _MISSING_ACK_DELAY_MAX_SECONDS))

    def _ack_timeout_seconds(self, metadata: dict[str, Any]) -> float:
        ac = metadata.get("agent_core")
        if isinstance(ac, dict):
            raw = ac.get("ack_timeout_seconds")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return self._clamp_missing_ack_delay(float(raw))
        return self._clamp_missing_ack_delay(self._missing_ack_default_seconds)

    def _evict_stale_outbounds(self, now: float) -> None:
        ttl = self._outbound_registry_ttl_seconds
        stale = [k for k, t in self._recent_outbound_ids.items() if now - t > ttl]
        for k in stale:
            self._recent_outbound_ids.pop(k, None)
            self._cancel_missing_ack(k)
        while len(self._recent_outbound_ids) > self._max_tracked_outbounds:
            oldest_key = min(self._recent_outbound_ids.items(), key=lambda kv: kv[1])[0]
            self._recent_outbound_ids.pop(oldest_key)
            self._cancel_missing_ack(oldest_key)

    def _evict_stale_inbounds(self, now: float) -> None:
        ttl = self._outbound_registry_ttl_seconds
        stale = [
            k for k, info in self._recent_inbounds.items() if now - info["registered_at"] > ttl
        ]
        for k in stale:
            self._recent_inbounds.pop(k, None)
        while len(self._recent_inbounds) > self._max_tracked_outbounds:
            oldest_key = min(
                self._recent_inbounds.items(), key=lambda kv: kv[1]["registered_at"]
            )[0]
            self._recent_inbounds.pop(oldest_key)

    def _record_recent_inbound(self, envelope: Envelope, *, now: float) -> None:
        # Copy ``metadata`` so post-cache mutations of the original envelope's
        # metadata dict don't bleed into reply() lookups. The cache's whole
        # point is making routing safe to use after the inbound is gone.
        self._recent_inbounds[envelope.id] = {
            "from": envelope.from_,
            "to": envelope.to,
            "kind": envelope.kind,
            "metadata": dict(envelope.metadata),
            "urgency": envelope.urgency,
            "correlation_id": envelope.correlation_id,
            "registered_at": now,
        }

    def _is_routine_green_ack(self, envelope: Envelope) -> bool:
        """Decide whether to auto-clear an Acknowledgment by *shape*.

        Issue #54: the gate is shape-only — kind/urgency/note + the
        in_reply_to/payload.of integrity check. The earlier registry
        lookup leaked routine acks into the queue any time the daemon
        restarted between the send and the ack (registry empty until
        sends warmed it back up). Auto-clear now survives restarts.

        The ``_recent_outbound_ids`` registry stays — it still drives the
        missing-ack-timeout path. But it no longer gates auto-clear.
        """
        if self.wake_on_all_acknowledgments:
            return False
        if envelope.kind != "Acknowledgment":
            return False
        if not isinstance(envelope.payload, AcknowledgmentPayload):
            return False
        if envelope.urgency != "green":
            return False
        note = envelope.payload.note
        if note is not None and note.startswith("error:"):
            return False
        irt = envelope.in_reply_to
        if irt is None or envelope.payload.of != irt:
            return False
        return True

    def _register_outbound_sent(self, env_id: str, metadata: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._evict_stale_outbounds(now)
        self._recent_outbound_ids[env_id] = now
        self._schedule_missing_ack(env_id, metadata)

    def _cancel_missing_ack(self, outbound_id: str) -> None:
        t = self._missing_ack_tasks.pop(outbound_id, None)
        if t is not None and not t.done():
            t.cancel()

    def _schedule_missing_ack(self, outbound_id: str, metadata: dict[str, Any]) -> None:
        self._cancel_missing_ack(outbound_id)
        delay = self._ack_timeout_seconds(metadata)

        async def _fire() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            finally:
                self._missing_ack_tasks.pop(outbound_id, None)
            await self._missing_ack_outbound(outbound_id)

        self._missing_ack_tasks[outbound_id] = asyncio.create_task(_fire())

    async def _missing_ack_outbound(self, outbound_id: str) -> None:
        if outbound_id not in self._recent_outbound_ids:
            return
        self._recent_outbound_ids.pop(outbound_id, None)
        log.info(
            "endpoint '%s': missing-ack timer fired outbound_id=%s (wake)",
            self.name,
            outbound_id,
        )
        await self._notify_mail_arrived("yellow")

    def _release_outbound_registry_for_ack_envelope(self, env: Envelope) -> None:
        if env.kind != "Acknowledgment":
            return
        if not isinstance(env.payload, AcknowledgmentPayload):
            return
        refs: set[str] = set()
        if env.in_reply_to:
            refs.add(env.in_reply_to)
        of = env.payload.of
        if of:
            refs.add(of)
        for rid in refs:
            self._recent_outbound_ids.pop(rid, None)
            self._cancel_missing_ack(rid)

    def _compute_meta(self) -> dict:
        """Compute aggregate metadata from the current pending queue.

        Reads self._pending atomically (no awaits). Used by _call_list_pending
        so meta and items come from the same read — eliminates the wake-vs-
        list_pending drift that caused issue #33.
        """
        pending = list(self._pending)
        count = len(pending)
        urg_counts = Counter(e.urgency for e in pending)
        urg_full: dict[Literal["red", "yellow", "green"], int] = {}
        for tier in self._URGENCY_ORDER:
            urgency_key = cast(Literal["red", "yellow", "green"], tier)
            urg_full[urgency_key] = int(urg_counts.get(urgency_key, 0))
        urgency_max = "green"
        for tier in self._URGENCY_ORDER:
            urgency_key = cast(Literal["red", "yellow", "green"], tier)
            if urg_full[urgency_key] > 0:
                urgency_max = tier
                break
        sender_index: dict[str, dict] = {}
        for env in pending:
            entry = sender_index.setdefault(env.from_, {"from": env.from_, "count": 0, "kinds": []})
            entry["count"] += 1
            if env.kind not in entry["kinds"]:
                entry["kinds"].append(env.kind)
        by_sender = list(sender_index.values())
        return {
            "count": count,
            "urgency_max": urgency_max,
            "urgency_counts": urg_full,
            "by_sender": by_sender,
            "endpoint": self.name,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def _build_wake_summary(self) -> dict:
        """Minimal wake notification — pure 'go look' signal.

        Carries no queue-state metadata. The agent calls list_pending for
        authoritative count/urgency_max/by_sender, computed atomically with
        the items list. See issue #33.
        """
        return {
            "content": f"INBOX: pending ({self.name})",
            "meta": {
                "endpoint": self.name,
                "fired_at": datetime.now(UTC).isoformat(),
            },
        }

    async def _call_list_pending(self, batch_window_seconds: int = 0) -> dict:
        """Mailbox view sorted by urgency, optionally batched by sender.

        Returns {"meta": {...}, "items": [...]}. meta carries count, urgency_max,
        urgency_counts, by_sender, endpoint, fetched_at. items is the flat list
        of envelope dicts (when batch_window_seconds == 0) or batched/single
        entries (when > 0). meta and items are computed from the same atomic
        read of self._pending — see issue #33.
        """
        meta = self._compute_meta()
        sorted_pending = sorted(
            self._pending,
            key=lambda e: (self._URGENCY_RANK[e.urgency], e.created_at),
        )
        if batch_window_seconds <= 0:
            items: list[dict] = [self._envelope_to_dict(env) for env in sorted_pending]
            return {"meta": meta, "items": items}

        window = timedelta(seconds=batch_window_seconds)
        groups: list[dict] = []
        i = 0
        while i < len(sorted_pending):
            head = sorted_pending[i]
            j = i + 1
            run = [head]
            while j < len(sorted_pending):
                cand = sorted_pending[j]
                if (
                    cand.from_ == head.from_
                    and cand.urgency == head.urgency
                    and cand.kind == head.kind
                    and (cand.created_at - run[-1].created_at) <= window
                ):
                    run.append(cand)
                    j += 1
                else:
                    break
            if len(run) == 1:
                groups.append({"type": "single", "envelope": self._envelope_to_dict(head)})
            else:
                first_arrival = run[0].created_at
                last_arrival = run[-1].created_at
                groups.append(
                    {
                        "type": "batch",
                        "from": head.from_,
                        "kind": head.kind,
                        "urgency": head.urgency,
                        "envelopes": [self._envelope_to_dict(e) for e in run],
                        "first_arrival": first_arrival.isoformat(),
                        "total_age_seconds": int((last_arrival - first_arrival).total_seconds()),
                    }
                )
            i = j
        return {"meta": meta, "items": groups}

    @staticmethod
    def _envelope_to_dict(env: Envelope) -> dict:
        return {
            "id": env.id,
            "from": env.from_,
            "to": env.to,
            "kind": env.kind,
            "correlation_id": env.correlation_id,
            "in_reply_to": env.in_reply_to,
            "payload": env.payload.model_dump() if hasattr(env.payload, "model_dump") else dict(env.payload),
            "metadata": env.metadata,
            "urgency": env.urgency,
            "created_at": env.created_at.isoformat(),
        }

    # --- Endpoint Protocol ---

    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        # T19: drain deferred tool mounters now that the BusHandle exists.
        # Cross-endpoint wiring (the briefs plugin) appends to this list
        # before bus.start; each mounter performs the actual mcp.tool
        # registration with the now-available handle threaded in.
        #
        # Pop-as-you-go: drain the list one-shot. If start() is somehow
        # called again, the list is empty and re-registration is a no-op.
        # Each mounter typically registers permanent state on self._mcp
        # (FastMCP tools are additive), so re-firing them would either
        # raise on duplicate tool names or silently shadow the prior
        # registration — neither is what callers expect.
        any_mounter_fired = False
        while self.deferred_tool_mounters:
            mounter = self.deferred_tool_mounters.pop(0)
            mounter(bus)
            any_mounter_fired = True
        # Issue #37: after deferred mounters mutate the tool surface, push
        # one batched notifications/tools/list_changed to every connected
        # session so MCP clients re-enumerate their tool cache. Static
        # registries (no deferred mounters) skip the notification — no
        # spurious wakes.
        if any_mounter_fired:
            await self._notify_tools_list_changed()
        log.info("ClaudeCodeMCPEndpoint(name=%s) started at mount=%s", self.name, self.mount)

    async def _notify_tools_list_changed(self) -> None:
        """Push MCP ``notifications/tools/list_changed`` to all connected sessions.

        Standard server→client notification — supporting clients re-run
        ``tools/list`` on receipt and pick up the latest tool surface
        without an ``/exit + relaunch``. Fired once after deferred tool
        mounters drain in :meth:`start`.

        Any future code path that mutates the tool surface outside the
        deferred-mounter drain (none today) should call this method after
        the mutation is complete.

        Sessions that raise during the push are unregistered, mirroring the
        channel-push behaviour in :meth:`_fire_after_debounce`.
        """
        sessions = list(self._sessions)
        if not sessions:
            log.info(
                "endpoint '%s': tools/list_changed has no active session audience",
                self.name,
            )
            return
        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/tools/list_changed",
            params={},
        )
        message = SessionMessage(message=JSONRPCMessage(notification))
        for session in sessions:
            try:
                await session.send_message(message)
            except Exception:
                log.warning(
                    "endpoint '%s': tools/list_changed push to session %d failed; "
                    "unregistering",
                    self.name,
                    id(session),
                    exc_info=True,
                )
                self._unregister_session(session)

    async def deliver(self, envelope: Envelope) -> None:
        """Push the envelope to the connected agent.

        Most envelopes queue for pickup, fan out to the NotificationBroker, and
        (when no HTTP MCP session is attached) raise EndpointUnavailable so the
        bus redelivers. Routine green Acknowledgments matching a recent outbound
        from this agent are auto-acked in persistence without queueing or push,
        so bridges can confirm delivery without waking the agent (no session
        required for that path).

        The broker fan-out and HTTP push run only when ``_notify_mail_arrived``
        fires; auto-handled acks skip it entirely.
        """
        now = asyncio.get_running_loop().time()
        self._evict_stale_outbounds(now)
        self._evict_stale_inbounds(now)
        if self._is_routine_green_ack(envelope):
            handle = self._require_handle()
            irt = envelope.in_reply_to
            assert irt is not None
            await handle.ack(envelope.id)
            log.debug(
                "endpoint '%s': auto-acked routine green acknowledgment inbound_id=%s "
                "outbound_id=%s",
                self.name,
                envelope.id,
                irt,
            )
            self._recent_outbound_ids.pop(irt, None)
            self._cancel_missing_ack(irt)
            return
        self.queue_for_pickup(envelope)
        # queue_for_pickup also captures routing into _recent_inbounds for
        # reply() lookups after the inbound is acked (issue #67). Auto-cleared
        # acks above never reach this point.
        await self._notify_mail_arrived(envelope.urgency)
        if not self._sessions:
            raise EndpointUnavailable(f"no MCP session connected for {self.name}")

    async def stop(self) -> None:
        tasks = list(self._missing_ack_tasks.values())
        self._missing_ack_tasks.clear()
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._recent_outbound_ids.clear()
        self._recent_inbounds.clear()

        self._handle = None
        self._sessions.clear()
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except (asyncio.CancelledError, Exception):
                pass
        self._debounce_task = None
        self._debounce_deadline = None
        log.info("ClaudeCodeMCPEndpoint(name=%s) stopped", self.name)

    # --- MCPHostable Protocol ---

    def asgi_app(self):
        """Return the ASGI app for this endpoint's FastMCP server."""
        return self._mcp.http_app(path="/")

    # --- Public helpers ---

    def queue_for_pickup(self, envelope: Envelope) -> None:
        """Add an envelope to this endpoint's pending pickup queue.

        Idempotent on envelope id: the bus retries deliver() on
        EndpointUnavailable, and we'd otherwise grow stale duplicates.

        Used by deliver() when no session is connected, and by tests.
        When called from inside a running event loop, also captures the
        envelope's routing into the recent-inbounds cache (issue #67) so
        reply() can find it after the inbound is acked. Sync test paths
        without a loop skip the cache record to avoid mixing clock
        sources with the eviction path (which always runs under a loop).
        """
        if any(e.id == envelope.id for e in self._pending):
            return
        self._pending.append(envelope)
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        self._record_recent_inbound(envelope, now=now)

    # --- Internal ---

    def snapshot(self) -> dict:
        """Public wrapper used by Bus.snapshot_for_agent.

        Emits the same minimal wake shape as a regular debounced wake (see
        issue #33) — one contract for everything wake-shaped.
        """
        return self._build_wake_summary()

    def _make_channel_notification(self, summary: dict) -> SessionMessage:
        """Wrap the summary into a JSON-RPC notification SessionMessage."""
        params = {
            "content": str(summary.get("content", "")),
            "meta": self._coerce_channel_meta(summary.get("meta", {}) or {}),
        }
        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/claude/channel",
            params=params,
        )
        return SessionMessage(message=JSONRPCMessage(notification))

    @staticmethod
    def _coerce_channel_meta(meta: dict[str, Any]) -> dict[str, str]:
        """Claude Code channel meta must be Record<string, string>."""
        out: dict[str, str] = {}
        for key, value in meta.items():
            if not isinstance(key, str) or not _META_KEY_RE.match(key):
                continue
            if isinstance(value, str):
                out[key] = value
            elif isinstance(value, (int, float, bool)):
                out[key] = str(value)
            else:
                out[key] = json.dumps(value, default=str)
        return out

    async def _notify_mail_arrived(self, urgency: str = "green") -> None:
        """Schedule a debounced push announcing inbox activity.

        Called by deliver() on each arrival. Red arrivals wake promptly,
        yellow waits briefly, green waits long enough to collect bursts.
        A more urgent arrival shortens a pending timer; less urgent
        arrivals never delay an already-pending urgent push.

        The wake notification itself carries no queue-state — see issue #33.
        The urgency parameter only affects debounce timing, not wake content.
        """
        delay = self._notify_debounce_seconds_by_urgency.get(urgency, 1.0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay

        if self._debounce_task is not None and not self._debounce_task.done():
            if self._debounce_deadline is not None and deadline >= self._debounce_deadline:
                return
            self._debounce_task.cancel()
        self._debounce_deadline = deadline
        self._debounce_task = asyncio.create_task(self._fire_after_debounce(delay))

    async def _fire_after_debounce(self, delay: float) -> None:
        task = asyncio.current_task()
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if task is self._debounce_task:
            self._debounce_deadline = None
        summary = self._build_wake_summary()

        # Always publish to the broker so /notify/<agent> subscribers
        # (the channel relay) wake the agent regardless of whether the
        # daemon's HTTP MCP session is currently captured.
        if self._notify_broker is not None:
            try:
                await self._notify_broker.publish(self.name, summary)
            except Exception:
                log.warning("endpoint '%s': broker publish failed", self.name, exc_info=True)

        sessions = list(self._sessions)
        if not sessions:
            log.info(
                "endpoint '%s': debounce fired; no active session, skipping HTTP push",
                self.name,
            )
            return
        message = self._make_channel_notification(summary)
        for session in sessions:
            try:
                log.info(
                    "endpoint '%s': pushing notifications/claude/channel to session %d",
                    self.name,
                    id(session),
                )
                await session.send_message(message)
                log.info("endpoint '%s': push to session %d returned", self.name, id(session))
            except Exception:
                log.warning(
                    "endpoint '%s': push to session %d failed; unregistering",
                    self.name,
                    id(session),
                    exc_info=True,
                )
                self._unregister_session(session)
