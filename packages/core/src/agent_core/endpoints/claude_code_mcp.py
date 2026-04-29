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
deliver() raises EndpointUnavailable so the bus queues the envelope.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import anyio
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.session import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification

from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import EndpointUnavailable

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


class SessionRegistry(Middleware):
    """Middleware that captures the connected ServerSession on first message.

    Mirrors FastMCP's official PingMiddleware pattern: spawn a long-lived
    coroutine into session._subscription_task_group; the coroutine registers
    the session with the endpoint, awaits forever, and runs cleanup in
    finally: when the session task group is cancelled (which fires when the
    SSE stream closes).

    Dedup key is `mcp-session-id` (string), not `id(session)`. The header is
    the spec-defined stable identifier across HTTP requests in a streamable-
    HTTP session; `id(session)` happens to also be stable in stateful mode
    but isn't load-bearing — and using the string makes us robust to clients
    that genuinely open new logical sessions (most-recent-wins).
    """

    def __init__(self, endpoint: "ClaudeCodeMCPEndpoint") -> None:
        self._endpoint = endpoint
        self._spawned_for: set[str] = set()
        self._lock = anyio.Lock()

    async def on_message(self, context: MiddlewareContext, call_next) -> Any:
        if context.fastmcp_context is None or context.fastmcp_context.request_context is None:
            return await call_next(context)

        ctx = context.fastmcp_context
        session = ctx.session
        # `session_id` is the mcp-session-id header (stable across the SSE
        # stream's lifetime in stateful mode). Fall back to `id(session)` for
        # in-memory transports that don't have a session id.
        sid = getattr(ctx, "session_id", None) or f"obj:{id(session)}"
        log.info(
            "endpoint '%s': on_message session_id=%s id(session)=%d",
            self._endpoint.name,
            sid,
            id(session),
        )

        async with self._lock:
            if sid not in self._spawned_for:
                tg = getattr(session, "_subscription_task_group", None)
                if tg is not None:
                    self._spawned_for.add(sid)
                    tg.start_soon(self._claim_session, session, sid)

        return await call_next(context)

    async def _claim_session(self, session: Any, sid: str) -> None:
        try:
            self._endpoint._register_session(session)
            await anyio.sleep_forever()
        finally:
            self._endpoint._unregister_session(session)
            self._spawned_for.discard(sid)


class ClaudeCodeMCPEndpoint:
    """Bus endpoint backed by a FastMCP server, served on the shared HTTP host."""

    _URGENCY_RANK = {"red": 0, "yellow": 1, "green": 2}
    _URGENCY_ORDER = ["red", "yellow", "green"]

    def __init__(self, *, name: str, mount: str):
        self.name = name
        self.mount = mount
        self._mcp: FastMCP = FastMCP(
            name,
            instructions=(
                "You are agent '{name}'. The bus pushes you notifications with method "
                '"notifications/claude/channel" when envelopes arrive in your mailbox. '
                'Each notification\'s params contain "content" (a brief summary) and '
                '"meta" (count, urgency_max, urgency_counts, by_sender, endpoint, '
                "fired_at). On receipt: call list_pending() to read the actual "
                "envelopes (set batch_window_seconds=30 to fold human-paced bursts "
                "from the same sender), process them, then call handle(envelope_id) "
                "on each to ack and remove from the queue. Send replies via the "
                "send tool. Treat the notification's content as a hint, not the "
                "message itself — list_pending is authoritative."
            ).format(name=name),
        )
        self._handle: "BusHandle | None" = None
        self._pending: list[Envelope] = []
        self._session_active: bool = False  # set true when an MCP session is attached
        self._active_session: Any = None  # ServerSession, when connected
        self._notify_debounce_seconds: float = 0.05
        self._debounce_task: asyncio.Task | None = None
        self._mcp.add_middleware(SessionRegistry(self))
        self._register_tools()

    def _register_session(self, session: Any) -> None:
        """Capture the active ServerSession.

        Most-recent-wins: a new session replaces any prior one. The previous
        session's `_claim_session.finally:` will eventually run (when its
        task group cancels) and call `_unregister_session(old_session)`,
        which is identity-checked and will no-op against the new slot.
        """
        replaced = self._active_session is not None and self._active_session is not session
        self._active_session = session
        self._session_active = True
        if replaced:
            log.info(
                "endpoint '%s': replaced active session with newer connection",
                self.name,
            )
        else:
            log.debug("endpoint '%s' captured active session", self.name)

    def _unregister_session(self, session: Any) -> None:
        """Clear the slot if the session matches the one we hold."""
        if self._active_session is session:
            self._active_session = None
            self._session_active = False
            log.debug("endpoint '%s' released active session", self.name)

    async def _call_list_pending(self, batch_window_seconds: int = 0) -> list[dict]:
        """Mailbox view sorted by urgency, optionally batched by sender.

        When batch_window_seconds == 0: returns a flat list of envelope dicts
        (today's behavior). When > 0: consecutive envelopes (within urgency
        tier and same `from_`) whose created_at fall within the window collapse
        into one {"type": "batch", ...} entry; standalone entries are wrapped
        as {"type": "single", "envelope": {...}}.
        """
        sorted_pending = sorted(
            self._pending,
            key=lambda e: (self._URGENCY_RANK[e.urgency], e.created_at),
        )
        if batch_window_seconds <= 0:
            return [self._envelope_to_dict(env) for env in sorted_pending]

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
        return groups

    @staticmethod
    def _envelope_to_dict(env: Envelope) -> dict:
        return {
            "id": env.id,
            "from": env.from_,
            "to": env.to,
            "kind": env.kind,
            "correlation_id": env.correlation_id,
            "in_reply_to": env.in_reply_to,
            "payload": env.payload.model_dump(),
            "metadata": env.metadata,
            "urgency": env.urgency,
            "created_at": env.created_at.isoformat(),
        }

    # --- Endpoint Protocol ---

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        log.info("ClaudeCodeMCPEndpoint(name=%s) started at mount=%s", self.name, self.mount)

    async def deliver(self, envelope: Envelope) -> None:
        """Push the envelope to the connected agent.

        If a session is active: queue and send a mail-arrived notification hint.
        If no session: queue locally and raise EndpointUnavailable so the bus
        retries when the session reconnects."""
        if not self._session_active:
            self.queue_for_pickup(envelope)
            raise EndpointUnavailable(f"no MCP session connected for {self.name}")

        # Active session path — queue then notify.
        self.queue_for_pickup(envelope)
        await self._notify_mail_arrived()

    async def stop(self) -> None:
        self._handle = None
        self._session_active = False
        self._active_session = None
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except (asyncio.CancelledError, Exception):
                pass
        self._debounce_task = None
        log.info("ClaudeCodeMCPEndpoint(name=%s) stopped", self.name)

    # --- MCPHostable Protocol ---

    def asgi_app(self):
        """Return the ASGI app for this endpoint's FastMCP server."""
        return self._mcp.http_app(path="/")

    # --- Public helpers ---

    def queue_for_pickup(self, envelope: Envelope) -> None:
        """Add an envelope to this endpoint's pending pickup queue.

        Used by deliver() when no session is connected, and by tests."""
        self._pending.append(envelope)

    # --- Internal ---

    def _build_summary(self) -> dict:
        """Snapshot the current mailbox into a notification summary."""
        pending = list(self._pending)
        count = len(pending)
        # urgency counts
        urg_counts = Counter(e.urgency for e in pending)
        urg_full = {tier: int(urg_counts.get(tier, 0)) for tier in self._URGENCY_ORDER}
        # urgency_max — highest tier present
        urgency_max = "green"
        for tier in self._URGENCY_ORDER:
            if urg_full[tier] > 0:
                urgency_max = tier
                break
        # by_sender
        sender_index: dict[str, dict] = {}
        for env in pending:
            entry = sender_index.setdefault(env.from_, {"from": env.from_, "count": 0, "kinds": []})
            entry["count"] += 1
            if env.kind not in entry["kinds"]:
                entry["kinds"].append(env.kind)
        by_sender = list(sender_index.values())
        # Headline content — terse, useful for triage.
        if count == 0:
            content = "INBOX: 0 pending"
        else:
            sender_summary = ", ".join(
                f"{e['count']} from {e['from']} ({'/'.join(e['kinds'])})" for e in by_sender
            )
            content = f"INBOX: {count} pending — {sender_summary}"
        return {
            "content": content,
            "meta": {
                "count": count,
                "urgency_max": urgency_max,
                "urgency_counts": urg_full,
                "by_sender": by_sender,
                "endpoint": self.name,
                "fired_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def snapshot(self) -> dict:
        """Public wrapper around _build_summary; used by Bus.snapshot_for_agent.

        Returns the same dict shape as the push pipeline produces, so a
        snapshot emitted on relay connect looks identical to a real push.
        """
        return self._build_summary()

    def _make_channel_notification(self, summary: dict) -> SessionMessage:
        """Wrap the summary into a JSON-RPC notification SessionMessage."""
        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/claude/channel",
            params=summary,
        )
        return SessionMessage(message=JSONRPCMessage(notification))

    async def _notify_mail_arrived(self) -> None:
        """Schedule a debounced push summarizing the current mailbox.

        Called by `deliver()` on each arrival. Cancels any pending debounce
        task and schedules a fresh one so a burst of arrivals coalesces into
        one notification. The summary is rebuilt from `_pending` at fire time;
        the per-envelope id is therefore not needed here.
        """
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._fire_after_debounce())

    async def _fire_after_debounce(self) -> None:
        try:
            await asyncio.sleep(self._notify_debounce_seconds)
        except asyncio.CancelledError:
            return
        session = self._active_session
        if session is None:
            log.info(
                "endpoint '%s': debounce fired; no active session, skipping push",
                self.name,
            )
            return  # mailbox is authoritative; agent picks up on connect
        summary = self._build_summary()
        try:
            message = self._make_channel_notification(summary)
            log.info(
                "endpoint '%s': pushing notifications/claude/channel to session %d (count=%d)",
                self.name,
                id(session),
                summary["meta"]["count"],
            )
            await session.send_message(message)
            log.info("endpoint '%s': push to session %d returned", self.name, id(session))
        except Exception:
            log.warning(
                "endpoint '%s': push to active session failed; clearing slot",
                self.name,
                exc_info=True,
            )
            # Best-effort cleanup; the session is presumed dead.
            if self._active_session is session:
                self._active_session = None
                self._session_active = False

    def _register_tools(self) -> None:
        """Register the bus's MCP tool surface on the FastMCP server."""

        @self._mcp.tool()
        async def send(
            to: str,
            kind: str,
            payload: dict[str, Any],
            correlation_id: str | None = None,
            in_reply_to: str | None = None,
            metadata: dict[str, Any] | None = None,
            urgency: str = "green",
            expires_at: str | None = None,
        ) -> dict:
            """Publish an envelope. Bus stamps `from:` to this endpoint's name.

            urgency: 'green' (default), 'yellow', or 'red'. Schema-validated.
            """
            if self._handle is None:
                raise RuntimeError(f"endpoint '{self.name}' is not started")
            env = Envelope(
                id=uuid.uuid4().hex,
                correlation_id=correlation_id or uuid.uuid4().hex,
                in_reply_to=in_reply_to,
                to=to,
                kind=kind,  # type: ignore[arg-type]
                payload=payload,  # type: ignore[arg-type]  # discriminated by kind
                metadata=metadata or {},
                urgency=urgency,  # type: ignore[arg-type]  # validated by Pydantic
                expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
                created_at=datetime.now(timezone.utc),
            )
            await self._handle.publish(env)
            return {"status": "published", "id": env.id}

        @self._mcp.tool()
        async def list_endpoints() -> list[dict]:
            """Return the directory of registered bus endpoints."""
            if self._handle is None:
                return []
            return [
                {"name": e.name, "description": e.description} for e in self._handle.endpoints()
            ]

        @self._mcp.tool()
        async def describe_endpoint(name: str) -> dict | None:
            """Return one endpoint's directory entry, or None if unknown."""
            if self._handle is None:
                return None
            for e in self._handle.endpoints():
                if e.name == name:
                    return {"name": e.name, "description": e.description}
            return None

        @self._mcp.tool()
        async def list_pending(batch_window_seconds: int = 0) -> list[dict]:
            """Return a snapshot of envelopes in this agent's pickup queue,
            sorted by urgency (red → yellow → green) with FIFO within tier.

            When batch_window_seconds > 0, consecutive same-sender same-urgency
            same-kind envelopes whose arrival times fall within the window are
            collapsed into a single {"type": "batch", ...} entry. Each
            underlying envelope retains its own id and ack semantics — call
            handle(envelope_id) per envelope.
            """
            return await self._call_list_pending(batch_window_seconds=batch_window_seconds)

        @self._mcp.tool()
        async def handle(envelope_id: str) -> dict:
            """Acknowledge an envelope and remove it from the pickup queue."""
            if self._handle is None:
                return {"status": "error", "message": "endpoint not started"}
            await self._handle.ack(envelope_id)
            self._pending = [e for e in self._pending if e.id != envelope_id]
            return {"status": "handled", "id": envelope_id}

        @self._mcp.tool()
        async def ack(envelope_id: str) -> dict:
            """Direct ack via the BusHandle."""
            if self._handle is None:
                return {"status": "error", "message": "endpoint not started"}
            await self._handle.ack(envelope_id)
            self._pending = [e for e in self._pending if e.id != envelope_id]
            return {"status": "acked", "id": envelope_id}

        @self._mcp.tool()
        async def nack(envelope_id: str, requeue: bool = True) -> dict:
            """Direct nack via the BusHandle."""
            if self._handle is None:
                return {"status": "error", "message": "endpoint not started"}
            await self._handle.nack(envelope_id, requeue)
            self._pending = [e for e in self._pending if e.id != envelope_id]
            return {"status": "nacked", "id": envelope_id, "requeue": requeue}
