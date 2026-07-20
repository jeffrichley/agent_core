"""MCP tool implementations for ClaudeCodeMCPEndpoint.

``register_tools(ep)`` is called once in ``ClaudeCodeMCPEndpoint.__init__``
to register all bus tools on the FastMCP server instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import Envelope

if TYPE_CHECKING:
    from ._endpoint import ClaudeCodeMCPEndpoint

# Inbound-only Discord metadata fields that must be stripped from reply() outbounds.
# The Discord adapter's outbound validator only accepts channel_id (and a small set
# of canonical send args); these five fields are receive-side only and fail
# shape validation on TextMessage, producing a silent-drop Acknowledgment (issue #222).
_DISCORD_INBOUND_ONLY_KEYS: frozenset[str] = frozenset(
    {"author_display_name", "author_id", "guild_id", "is_bot", "is_dm"}
)


def _not_started_error() -> dict[str, str]:
    """Standard not-started response for dict-returning MCP tool functions."""
    return {"status": "error", "message": "endpoint not started"}


def register_tools(ep: ClaudeCodeMCPEndpoint) -> None:
    """Register all bus MCP tools on ep._mcp."""

    @ep._mcp.tool()
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
        handle = ep._require_handle()
        env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=correlation_id or uuid.uuid4().hex,
            in_reply_to=in_reply_to,
            to=to,
            kind=kind,
            payload=payload,  # discriminated by kind
            metadata=metadata or {},
            urgency=urgency,  # type: ignore[arg-type]  # validated by Pydantic
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
            created_at=datetime.now(UTC),
        )
        # Issue #69: register the outbound BEFORE publishing. The bus is
        # single-loop and dispatches in-process, so an in-flight routine
        # ack from the recipient adapter can reach our deliver() while
        # we're still awaiting publish — the ack's auto-clear path needs
        # something concrete to cancel, otherwise it pops nothing and a
        # phantom missing-ack timer scheduled afterward fires a wake.
        if not ep.wake_on_all_acknowledgments:
            ep._register_outbound_sent(env.id, env.metadata)
        try:
            await handle.publish(env)
        except Exception:
            if not ep.wake_on_all_acknowledgments:
                ep._recent_outbound_ids.pop(env.id, None)
                ep._cancel_missing_ack(env.id)
            raise
        return {"status": "published", "id": env.id}

    @ep._mcp.tool()
    async def list_endpoints() -> list[dict]:
        """Return the directory of registered bus endpoints."""
        if ep._handle is None:
            return []
        return [
            {"name": e.name, "description": e.description} for e in ep._handle.endpoints()
        ]

    @ep._mcp.tool()
    async def describe_endpoint(name: str) -> dict | None:
        """Return one endpoint's directory entry, or None if unknown."""
        if ep._handle is None:
            return None
        for e in ep._handle.endpoints():
            if e.name == name:
                return {"name": e.name, "description": e.description}
        return None

    @ep._mcp.tool()
    async def list_pending(batch_window_seconds: int = 0) -> dict:
        """Return a snapshot of envelopes in this agent's pickup queue,
        sorted by urgency (red → yellow → green) with FIFO within tier.

        Returns {"meta": {...}, "items": [...]}. meta carries count,
        urgency_max, urgency_counts, by_sender, endpoint, fetched_at —
        computed atomically with items, so the aggregate cannot drift
        from the actual queue contents (see issue #33).

        When batch_window_seconds > 0, consecutive same-sender same-urgency
        same-kind envelopes whose arrival times fall within the window are
        collapsed into a single {"type": "batch", ...} entry inside items.
        Each underlying envelope retains its own id and ack semantics —
        call handle(envelope_id) per envelope.
        """
        return await ep._call_list_pending(batch_window_seconds=batch_window_seconds)

    @ep._mcp.tool()
    async def handle(envelope_id: str) -> dict:
        """Acknowledge an envelope and remove it from the pickup queue."""
        if ep._handle is None:
            return _not_started_error()
        env_before = next((e for e in ep._pending if e.id == envelope_id), None)
        await ep._handle.ack(envelope_id)
        if env_before is not None:
            ep._release_outbound_registry_for_ack_envelope(env_before)
        ep._pending = [e for e in ep._pending if e.id != envelope_id]
        return {"status": "handled", "id": envelope_id}

    @ep._mcp.tool()
    async def ack(envelope_id: str) -> dict:
        """Direct ack via the BusHandle."""
        if ep._handle is None:
            return _not_started_error()
        env_before = next((e for e in ep._pending if e.id == envelope_id), None)
        await ep._handle.ack(envelope_id)
        if env_before is not None:
            ep._release_outbound_registry_for_ack_envelope(env_before)
        ep._pending = [e for e in ep._pending if e.id != envelope_id]
        return {"status": "acked", "id": envelope_id}

    @ep._mcp.tool()
    async def nack(envelope_id: str, requeue: bool = True) -> dict:
        """Direct nack via the BusHandle."""
        if ep._handle is None:
            return _not_started_error()
        env_before = next((e for e in ep._pending if e.id == envelope_id), None)
        await ep._handle.nack(envelope_id, requeue)
        if env_before is not None:
            ep._release_outbound_registry_for_ack_envelope(env_before)
        ep._pending = [e for e in ep._pending if e.id != envelope_id]
        return {"status": "nacked", "id": envelope_id, "requeue": requeue}

    @ep._mcp.tool()
    async def consume(
        batch_window_seconds: int = 30,
        auto_ack: bool = True,
        max_items: int | None = None,
    ) -> dict:
        """Read pending envelopes and ack them in one call (issue #67).

        Same return shape as ``list_pending``: ``{"meta": {...}, "items": [...]}``.
        ``meta`` always reflects the current full queue (the ``max_items``
        cap trims ``items`` only — meta is presentational-of-total, not
        of-the-trimmed-slice).

        When ``auto_ack=True`` (default), every envelope referenced in the
        returned ``items`` is ack'd via the bus before this call returns;
        the items are also dropped from the pickup queue. When False,
        ``consume`` is a pure read — ack with ``handle()`` later. Set
        False if you might bail before processing and need redelivery.

        Composes with ``reply()``: a typical Discord round-trip is
        ``consume()`` → ``reply(in_reply_to=item_id, payload=...)`` (2
        calls).
        """
        handle = ep._require_handle()
        result = await ep._call_list_pending(batch_window_seconds=batch_window_seconds)
        items = result["items"]
        if max_items is not None and max_items >= 0:
            items = items[:max_items]
            result = {"meta": result["meta"], "items": items}

        if auto_ack:
            ids: list[str] = []
            for item in items:
                if "envelope" in item:  # batched single
                    ids.append(item["envelope"]["id"])
                elif "envelopes" in item:  # batched run
                    ids.extend(e["id"] for e in item["envelopes"])
                elif "id" in item:  # flat envelope dict
                    ids.append(item["id"])
            # Ack everything FIRST; only mutate _pending after the bus has
            # accepted every ack. If a mid-walk ack raises, _pending is
            # untouched and the caller can retry the whole consume safely
            # (bus acks are idempotent). Without this ordering, a partial
            # failure would drop items from _pending whose ids never made
            # it back to the caller — a silent data-loss path.
            for env_id in ids:
                await handle.ack(env_id)
            drop = set(ids)
            envs_dropped = [e for e in ep._pending if e.id in drop]
            ep._pending = [e for e in ep._pending if e.id not in drop]
            for env in envs_dropped:
                ep._release_outbound_registry_for_ack_envelope(env)
        return result

    @ep._mcp.tool()
    async def reply(
        in_reply_to: str,
        payload: dict[str, Any],
        urgency: str = "green",
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Publish an outbound and ack the inbound it answers in one call.

        Routing inherits from the inbound envelope (``to`` ← inbound.from_,
        ``correlation_id`` ← inbound.correlation_id, ``metadata`` ← inbound
        metadata, then top-level keys overridden by the ``metadata``
        argument). The metadata merge is **shallow** — supplying
        ``{"discord": {"channel_id": "X"}}`` replaces the entire
        ``discord`` dict, dropping sibling keys like ``guild_id`` from the
        inbound. Override fully or not at all when touching transport
        metadata.

        ``urgency`` defaults to ``"green"`` (matching ``send()``); a
        reply is its own message and inherits no urgency by default.
        Pass ``"auto"`` to inherit the inbound's urgency (escalating
        error chains, etc.) or an explicit ``"red"|"yellow"|"green"``
        override.

        ``in_reply_to`` is looked up in the pickup queue first, then in
        the recent-inbounds cache (so it works after
        ``consume(auto_ack=True)``). Raises if the id isn't known.

        The reply's ``kind`` is ``TextMessage`` — for non-text replies,
        use ``send()`` + ``handle()`` instead.

        Returns ``{"published_envelope_id": ..., "acked_envelope_id": ...}``.
        """
        handle = ep._require_handle()

        inbound_pending = next((e for e in ep._pending if e.id == in_reply_to), None)
        if inbound_pending is not None:
            from_ = inbound_pending.from_
            inbound_metadata = inbound_pending.metadata
            inbound_urgency = inbound_pending.urgency
            inbound_correlation = inbound_pending.correlation_id
        else:
            cached = ep._recent_inbounds.get(in_reply_to)
            if cached is None:
                raise ValueError(
                    f"reply: no inbound found for in_reply_to={in_reply_to!r}"
                )
            from_ = cached["from"]
            inbound_metadata = cached["metadata"]
            inbound_urgency = cached["urgency"]
            inbound_correlation = cached["correlation_id"]

        out_urgency: str = inbound_urgency if urgency == "auto" else urgency
        out_metadata = {**inbound_metadata, **(metadata or {})}
        discord_meta = out_metadata.get("discord")
        if isinstance(discord_meta, dict) and (discord_meta.keys() & _DISCORD_INBOUND_ONLY_KEYS):
            out_metadata = {
                **out_metadata,
                "discord": {
                    k: v for k, v in discord_meta.items() if k not in _DISCORD_INBOUND_ONLY_KEYS
                },
            }

        env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=inbound_correlation,
            in_reply_to=in_reply_to,
            to=from_,
            kind="TextMessage",
            payload=payload,  # discriminated by kind
            metadata=out_metadata,
            urgency=out_urgency,  # type: ignore[arg-type]  # validated by Pydantic
            created_at=datetime.now(UTC),
        )
        # Issue #69: register before publishing — see send() for the full
        # rationale. An in-flight routine ack would otherwise leave the
        # registry empty on auto-clear and a phantom timer fires later.
        if not ep.wake_on_all_acknowledgments:
            ep._register_outbound_sent(env.id, env.metadata)
        try:
            await handle.publish(env)
        except Exception:
            if not ep.wake_on_all_acknowledgments:
                ep._recent_outbound_ids.pop(env.id, None)
                ep._cancel_missing_ack(env.id)
            raise

        await handle.ack(in_reply_to)
        if inbound_pending is not None:
            ep._release_outbound_registry_for_ack_envelope(inbound_pending)
        ep._pending = [e for e in ep._pending if e.id != in_reply_to]
        ep._recent_inbounds.pop(in_reply_to, None)

        return {"published_envelope_id": env.id, "acked_envelope_id": in_reply_to}

    @ep._mcp.tool()
    async def peek(envelope_id: str) -> dict:
        """Return one specific envelope from the pickup queue without acking.

        Used to hydrate a truncated inline preview into the full payload
        (issue #70). Also useful for power-use cases (manual triage of a
        specific envelope without disturbing other queue state).

        Pure read: does NOT ack, does NOT remove from the pickup queue.
        Idempotent — multiple calls return identical data.

        Looks only at the live pickup queue. Does NOT consult the
        recent-inbounds routing cache (which holds metadata only, not
        full payload — the cache cannot satisfy peek's contract).

        Raises if ``envelope_id`` is not in the queue.
        """
        env = next((e for e in ep._pending if e.id == envelope_id), None)
        if env is None:
            raise ValueError(
                f"peek: envelope_id={envelope_id!r} not in queue"
            )
        return {"envelope": ep._envelope_to_dict(env)}

    @ep._mcp.tool()
    async def show_my_day(
        date: str | None = None,
        projected: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Return today's bus traffic for this agent.

        Use for self-introspection ('what just happened') or feeding
        into a reflection summary. Projected output is Tool 3-shaped
        rows; ``projected=False`` returns full envelope JSON.

        The agent identity is the name this endpoint was constructed
        with — no ``agent`` parameter is exposed to prevent cross-agent
        queries. ``date`` defaults to today in the configured timezone
        (default ``US/Eastern``), matching the writer's local-midnight
        rollover so an evening invocation reads the same day's file.
        ``limit`` must be >= 1 if provided.
        """
        return await ep._show_my_day_impl(date=date, projected=projected, limit=limit)
