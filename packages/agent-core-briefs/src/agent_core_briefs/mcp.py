"""Agent-facing MCP surface for the brief framework.

This module exposes the brief framework's seven agent-facing tools as
MCP tools that an agent's Claude Code session can invoke. T16 (plugin
loader) wires the orchestrator + audit log + destination factories
into :func:`register_briefs_tools` and registers the resulting mounter
on a :class:`agent_core.endpoints.claude_code_mcp.ClaudeCodeMCPEndpoint`.

The seven tools
---------------
- :func:`compose_brief` (this module) — self-launch path. Runs gather
  inline (synchronously) and returns the result directly to the agent,
  bypassing the bus-driven ``BriefRequest`` / ``ComposeBrief`` envelope
  pair. Useful for ad-hoc agent-initiated brief composition.
- :func:`agent_core_briefs.tools.list_sections` — what sections the
  brief expects.
- :func:`agent_core_briefs.tools.get_section_spec` — full spec for one
  section, with color resolved.
- :func:`agent_core_briefs.tools.validate_section` — agent's draft for
  one section, returns issues.
- :func:`agent_core_briefs.tools.compress_sections` — proportional
  budget allocation across sections.
- :func:`agent_core_briefs.tools.add_extension_section` — append an
  ad-hoc section to the session.
- :func:`agent_core_briefs.submit.submit_brief` — atomic validate +
  format + send + audit (the close of the deterministic-LLM seam).

Design notes
------------
- ``compose_brief`` is intentionally distinct from the bus-driven
  ``BriefRequest`` path: it skips envelope construction and returns
  the ComposeBrief data dict to the caller in-process. The session is
  still registered in the orchestrator's registry, so the same
  ``list_sections`` / ``get_section_spec`` / etc. tools work against
  the returned ``session_token``.
- ``register_briefs_tools`` is a thin closure factory: each MCP tool
  wrapper captures the framework state (orchestrator, audit log,
  destination factories, bus handle) so tools defined here don't have
  to thread that state through every call.
- The ``SubmitResult`` dataclass returned by ``submit_brief`` is
  flattened to a JSON-friendly dict for MCP transport (frozen
  dataclasses don't auto-serialize over JSON-RPC).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent_core_briefs.submit import submit_brief as _submit_brief
from agent_core_briefs.tools import (
    add_extension_section as _add_extension_section,
)
from agent_core_briefs.tools import (
    compress_sections as _compress_sections,
)
from agent_core_briefs.tools import (
    get_section_spec as _get_section_spec,
)
from agent_core_briefs.tools import (
    list_sections as _list_sections,
)
from agent_core_briefs.tools import (
    validate_section as _validate_section,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from agent_core.bus.handle import BusHandle
    from agent_core_briefs.audit import AuditLog
    from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint
    from agent_core_briefs.protocol import Destination


__all__ = ["compose_brief", "register_briefs_tools"]


async def compose_brief(
    *,
    orchestrator: BriefsOrchestratorEndpoint,
    brief_type: str,
    scope: str | None = None,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Self-launch entry point — run gather + build session in-process.

    Mirrors the bus-driven ``BriefRequest`` → ``ComposeBrief`` flow but
    returns the result directly to the caller (the agent) without
    going through the bus. The session is registered in the
    orchestrator's registry the same way the bus path does it, so all
    the other agent tools (``list_sections`` etc.) work against the
    returned ``session_token``.

    Args:
        orchestrator: The framework's orchestrator endpoint (provides
            the playbooks path, fetcher catalog, vars map, and session
            registry).
        brief_type: Playbook to launch (the orchestrator looks up
            ``<playbooks_path>/<brief_type>.md``).
        scope: Optional brief scope (e.g., ``"today"``); echoed back
            into the returned data dict and onto the session.
        when: Optional reference timestamp (UTC). Defaults to
            ``datetime.now(UTC)`` so a fresh self-launch always
            captures "now".

    Returns:
        The ComposeBrief data dict — same shape the bus-driven path
        would put on the ``ComposeBrief`` envelope's ``data`` field:
        ``brief_type``, ``scope``, ``when`` (ISO string),
        ``session_token``, ``playbook_path``, ``voice``, ``context``,
        ``sections_required``, ``sections_optional``,
        ``sections_conditional_active``.

    Raises:
        ValueError: orchestrator has no ``default_target_agent``
            configured (self-launch needs an implicit target — there
            is no envelope metadata to consult).
        FileNotFoundError: no playbook for ``brief_type``. Surfaced as
            ``ValueError`` for MCP-tool callers — see implementation.
    """
    target_agent = orchestrator.default_target_agent
    if not target_agent:
        raise ValueError(
            "compose_brief: orchestrator has no default_target_agent configured; "
            "self-launch requires an implicit target agent"
        )
    when_resolved = when or datetime.now(UTC)
    # The MCP tool surface promises ValueError for unknown brief types.
    # build_compose_session raises FileNotFoundError when the playbook
    # is missing — translate that into ValueError so agents only have
    # to catch one error class for "bad inputs".
    try:
        _, compose_data = await orchestrator.build_compose_session(
            brief_type=brief_type,
            scope=scope,
            when=when_resolved,
            target_agent=target_agent,
            # Each self-launch gets a fresh correlation_id so concurrent
            # invocations of the same brief_type produce distinguishable
            # audit events (sentinel strings would collide).
            correlation_id=uuid.uuid4().hex,
            request_metadata={},
        )
    except FileNotFoundError as exc:
        raise ValueError(f"unknown brief_type {brief_type!r}: {exc}") from exc
    return compose_data


def register_briefs_tools(
    *,
    mcp: FastMCP,
    orchestrator: BriefsOrchestratorEndpoint,
    bus_handle: BusHandle,
    audit_log: AuditLog,
    destination_factories: dict[str, Callable[[], Destination]],
) -> None:
    """Mount the seven brief-framework agent tools on a FastMCP server.

    Tools registered:

    - ``compose_brief(brief_type, scope=None)`` — self-launch.
    - ``list_sections(token)``
    - ``get_section_spec(token, section_id)``
    - ``validate_section(token, section_id, fields)``
    - ``compress_sections(token, section_ids, target_total_chars)``
    - ``add_extension_section(token, section_id, title, color, fields)``
    - ``submit_brief(token, sections)``

    Each tool is a thin closure that captures the framework state so
    the agent never has to thread orchestrator / audit log / etc.
    through tool calls.

    Args:
        mcp: Target FastMCP server. Tools are registered via
            ``mcp.tool(name=...)``.
        orchestrator: Provides the session registry + the playbook
            path / fetcher catalog used by ``compose_brief``.
        bus_handle: Threaded into each ``destination.deliver`` call by
            ``submit_brief``. Required (non-nullable) — destinations
            like ``discord_embed`` call ``bus_handle.publish`` to fan
            out, so a ``None`` here would crash on first delivery
            attempt rather than degrade gracefully.
        audit_log: Receives ``submit.*`` events from ``submit_brief``.
        destination_factories: Map ``type_id → factory``; T16 builds
            this from the plugin loader at runner-construction time.

    Raises:
        TypeError: if ``bus_handle`` is ``None``. Destinations may
            publish, so a ``None`` here is a programming error
            (caught at registration time, not at first delivery).
    """
    if bus_handle is None:
        raise TypeError("register_briefs_tools: bus_handle is required (destinations may publish)")
    registry = orchestrator.registry

    @mcp.tool(
        name="compose_brief",
        description=(
            "Self-launch a brief composition. Runs the gather pipeline "
            "(same as a bus-driven BriefRequest) and returns a session_token "
            "plus the gathered context. Call list_sections + get_section_spec "
            "next to learn what to fill in, then submit_brief once every "
            "required section is composed. Returns: {brief_type, scope, when, "
            "session_token (32-char hex), playbook_path, voice, context "
            "(namespaced gather output), sections_required, sections_optional, "
            "sections_conditional_active}."
        ),
    )
    async def _tool_compose_brief(brief_type: str, scope: str | None = None) -> dict[str, Any]:
        """Self-launch a brief composition.

        Runs the same gather flow as a ``BriefRequest`` event but returns
        the result directly. The returned ``session_token`` works with
        ``list_sections``, ``get_section_spec``, ``validate_section``,
        ``compress_sections``, ``add_extension_section``, and
        ``submit_brief`` exactly the same way as a bus-driven session.
        """
        return await compose_brief(
            orchestrator=orchestrator,
            brief_type=brief_type,
            scope=scope,
        )

    @mcp.tool(
        name="list_sections",
        description=(
            "List the section IDs the brief expects: required (must be "
            "submitted), optional (may be submitted), and conditional_active "
            "(gated truthy by the playbook's when expressions and treated as "
            "required if required_when_active). Returns: {required, optional, "
            "conditional_active, brief_type, voice}."
        ),
    )
    async def _tool_list_sections(token: str) -> dict[str, Any]:
        """List the section IDs the brief expects (required, optional, conditional)."""
        return await _list_sections(registry, token)

    @mcp.tool(
        name="get_section_spec",
        description=(
            "Return one section's full spec: title, resolved int color, "
            "required flag, and per-field specs (name, required, max_chars, "
            "guidance). Use this to learn what fields to populate before "
            "drafting content. Raises if section_id is not in the brief."
        ),
    )
    async def _tool_get_section_spec(token: str, section_id: str) -> dict[str, Any]:
        """Return the section's spec (title, color, fields, guidance)."""
        return await _get_section_spec(registry, token, section_id=section_id)

    @mcp.tool(
        name="validate_section",
        description=(
            "Validate a draft for one section before submit. Checks: every "
            "required field is present and non-empty, no field exceeds "
            "max_chars, no extra fields beyond the spec. fields shape: "
            "[{name, value}, ...]. Returns: {section_id, valid, issues "
            "(list of human-readable strings)}. Use this iteratively while "
            "composing — validation also runs at submit time."
        ),
    )
    async def _tool_validate_section(
        token: str, section_id: str, fields: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Validate the agent's draft for one section. Returns ``valid`` + ``issues``."""
        return await _validate_section(registry, token, section_id=section_id, fields=fields)

    @mcp.tool(
        name="compress_sections",
        description=(
            "Suggest per-section character budgets when the total brief is "
            "too long. Allocates target_total_chars proportionally to each "
            "section's max_chars-weighted size. Returns: {target_total_chars, "
            "allocations: [{section_id, budget}, ...]}. The agent does the "
            "actual rewriting — this tool only allocates."
        ),
    )
    async def _tool_compress_sections(
        token: str, section_ids: list[str], target_total_chars: int
    ) -> dict[str, Any]:
        """Suggest compression budgets across sections (proportional allocation)."""
        return await _compress_sections(
            registry,
            token,
            section_ids=section_ids,
            target_total_chars=target_total_chars,
        )

    @mcp.tool(
        name="add_extension_section",
        description=(
            "Append an ad-hoc section the playbook didn't predefine (e.g., "
            "a one-off 'today's blockers' callout). Color is int decimal or "
            "a palette name. Raises if section_id collides with a playbook "
            "section or a previously-added extension. Extensions render "
            "alongside playbook sections at submit time."
        ),
    )
    async def _tool_add_extension_section(
        token: str,
        section_id: str,
        title: str,
        color: int | str,
        fields: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append an ad-hoc extension section to the in-flight session."""
        return await _add_extension_section(
            registry,
            token,
            section_id=section_id,
            title=title,
            color=color,
            fields=fields,
        )

    @mcp.tool(
        name="submit_brief",
        description=(
            "Submit the composed brief for delivery. Validates all required "
            "sections + fields are present, then fans out to all configured "
            "destinations (Discord, markdown file, etc.) best-effort. Token "
            "is consumed one-shot — call only after composing every required "
            "section. Partial success is possible (some destinations succeed, "
            "others fail). Returns: {success: bool, validation_issues: "
            "[{section_id, code, message}, ...], deliveries: "
            "[{destination_type, success, ref, error}, ...]}."
        ),
    )
    async def _tool_submit_brief(token: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit the composed brief — validate, fan out to destinations, audit."""
        result = await _submit_brief(
            registry=registry,
            token=token,
            sections=sections,
            destination_factories=destination_factories,
            bus_handle=bus_handle,
            audit_log=audit_log,
        )
        # SubmitResult is a frozen dataclass — flatten to a JSON-friendly
        # dict so the MCP transport can ferry it across the wire.
        return {
            "success": result.success,
            "validation_issues": [
                {
                    "section_id": issue.section_id,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in result.validation_issues
            ],
            "deliveries": [
                {
                    "destination_type": delivery.destination_type,
                    "success": delivery.success,
                    "ref": delivery.ref,
                    "error": delivery.error,
                }
                for delivery in result.deliveries
            ],
        }
