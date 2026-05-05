"""Submit handler — atomic validate + format + send + audit.

The close of the deterministic-LLM seam: the agent calls
``submit_brief`` once it's done composing, and the framework runs the
end-to-end deliver pipeline:

1. Look up the session by token (raises if unknown / expired /
   already consumed).
2. Validate the submitted sections against the session's spec via
   :func:`agent_core_briefs.validators.validate_submission`. If any
   issues are found, write a ``submit.validate.fail`` audit event and
   return — the session token stays active so the agent can retry.
3. Consume the token (one-shot) before any delivery attempt.
4. Fan out to every destination configured on the session
   (best-effort: one destination's failure does not stop the others).
   Each delivery's outcome is captured in a ``submit.deliver`` audit
   event.
5. Write a ``submit.complete`` audit event summarizing totals.

``SubmitResult.success`` is true iff validation passed AND at least
one destination delivered successfully. The agent should treat any
non-success as a partial failure worth surfacing — the audit log
carries the full breakdown.

This module is intentionally separate from
:mod:`agent_core_briefs.engine` (which owns the gather pipeline) to
keep each module focused on one direction of the seam: engine =
inbound (gather), submit = outbound (deliver).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_core_briefs.audit import AuditEvent, AuditLog
from agent_core_briefs.playbook import resolve_colors_for_sections
from agent_core_briefs.protocol import (
    DeliveryResult,
    Destination,
    PlaybookRef,
    SectionSpec,
)
from agent_core_briefs.session import SessionRegistry
from agent_core_briefs.validators import ValidationIssue, validate_submission

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)

# Number of session_token chars surfaced into the audit log. Full
# tokens are 32-char hex; the first 8 are enough to disambiguate in a
# single operator's log without leaking the full credential into a
# file that may be shared.
_TOKEN_AUDIT_PREFIX_LEN = 8


@dataclass(frozen=True)
class DestinationOutcome:
    """One destination's delivery outcome on a submit_brief call."""

    destination_type: str
    success: bool
    ref: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SubmitResult:
    """Aggregate result of a submit_brief call.

    ``success`` is true iff validation passed AND at least one
    destination delivered successfully. Validation-failure case has
    ``deliveries=[]``; delivery-failure case has the per-destination
    breakdown in ``deliveries``.
    """

    success: bool
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    deliveries: list[DestinationOutcome] = field(default_factory=list)


async def submit_brief(
    *,
    registry: SessionRegistry,
    token: str,
    sections: list[dict],
    destination_factories: dict[str, Callable[[], Destination]],
    bus_handle: BusHandle,
    audit_log: AuditLog,
) -> SubmitResult:
    """Atomic validate + format + send + audit.

    Args:
        registry: The orchestrator's session registry. Must contain
            an active session under ``token``.
        token: The agent's session token (32-char hex from T9
            ComposeBrief).
        sections: The agent's filled sections (one dict per section
            with ``section_id``, ``title``, ``color``, ``fields``).
        destination_factories: Map ``type_id`` →
            zero-arg factory returning a Destination instance. T16
            wires this from the plugin loader.
        bus_handle: BusHandle threaded into each ``destination.deliver``
            call (destinations like ``discord_embed`` publish through
            the bus; ``markdown_file`` ignores it).
        audit_log: The audit log to write events to.

    Returns:
        :class:`SubmitResult` summarizing outcome. Validation failures
        leave the session token active for retry; validation success
        consumes the token before fanning out (one-shot regardless of
        delivery outcome).

    Raises:
        SessionNotFoundError, SessionExpiredError, SessionConsumedError:
            From :meth:`SessionRegistry.get` — the agent gave a bad
            token. These are not caught here; the MCP wrapper (T14)
            surfaces them to the agent.
    """
    session = registry.get(token)
    token_short = token[:_TOKEN_AUDIT_PREFIX_LEN]

    # 1. Validate. Fail-fast: don't consume the token, don't deliver.
    issues = validate_submission(session=session, sections=sections)
    if issues:
        await audit_log.write(
            AuditEvent(
                timestamp=datetime.now(UTC),
                event_type="submit.validate.fail",
                session_token=token_short,
                brief_type=session.brief_type,
                target_agent=session.target_agent,
                data={
                    "issue_count": len(issues),
                    "issues": [
                        {
                            "section_id": issue.section_id,
                            "code": issue.code,
                            "message": issue.message,
                        }
                        for issue in issues
                    ],
                },
            )
        )
        return SubmitResult(success=False, validation_issues=list(issues), deliveries=[])

    # 2. Validation passed: consume the session token (one-shot)
    # BEFORE fanning out. Delivery outcomes do not affect consumption —
    # submit is one-shot regardless of how many destinations succeed
    # (per the framework spec).
    registry.consume(token)

    playbook_ref = PlaybookRef(
        brief_type=session.brief_type,
        path=session.playbook_path,
        sections_required=list(session.sections_required),
        sections_optional=list(session.sections_optional),
        sections_conditional_active=list(session.sections_conditional_active),
    )

    # 3. Enrich submitted sections with title + resolved color from the
    # spec. The playbook is single-source-of-truth for section metadata
    # (title, color) — the agent's submission is content-only (fields).
    # Without this enrichment, destinations render empty ``## `` headers
    # and lose all visual structure (caught on testbot 2026-05-05 in
    # the first live morning_brief — agent had no reason to retype
    # spec metadata, the framework should derive it).
    enriched_sections = _enrich_sections_with_spec(
        sections,
        session_specs=list(session.sections)
        + list(session.extension_sections)
        + list(session.conditional_sections),
        palette=session.colors_palette,
        context=session.context,
    )

    # 4. Fan out to destinations. One destination's failure does NOT
    # stop the others; capture every outcome.
    deliveries: list[DestinationOutcome] = []
    for dest_config in session.destinations:
        if not isinstance(dest_config, dict):
            outcome = DestinationOutcome(
                destination_type="<malformed>",
                success=False,
                ref=None,
                error=f"destination entry is not a mapping: {dest_config!r}",
            )
            deliveries.append(outcome)
            await _audit_deliver(audit_log, session, token_short, outcome)
            continue

        dest_type = dest_config.get("type")
        if not isinstance(dest_type, str) or not dest_type:
            outcome = DestinationOutcome(
                destination_type="<missing-type>",
                success=False,
                ref=None,
                error=f"destination entry missing 'type': {dest_config!r}",
            )
            deliveries.append(outcome)
            await _audit_deliver(audit_log, session, token_short, outcome)
            continue

        factory = destination_factories.get(dest_type)
        if factory is None:
            outcome = DestinationOutcome(
                destination_type=dest_type,
                success=False,
                ref=None,
                error=(
                    f"unknown destination type {dest_type!r} "
                    f"(catalog: {sorted(destination_factories)})"
                ),
            )
            deliveries.append(outcome)
            await _audit_deliver(audit_log, session, token_short, outcome)
            continue

        try:
            destination = factory()
        except Exception as exc:
            outcome = DestinationOutcome(
                destination_type=dest_type,
                success=False,
                ref=None,
                error=f"destination factory raised: {exc}",
            )
            deliveries.append(outcome)
            await _audit_deliver(audit_log, session, token_short, outcome)
            continue

        try:
            result = await destination.deliver(
                sections=enriched_sections,
                playbook=playbook_ref,
                scope=session.scope,
                when=session.when,
                config=dict(dest_config.get("config") or {}),
                bus_handle=bus_handle,
            )
        except Exception as exc:
            # Even though Destination.deliver is supposed to be
            # best-effort and capture errors itself, defend against
            # plugin authors who forget — a raised exception in one
            # destination must not block the others.
            log.warning(
                "submit_brief: destination %s.deliver raised; treating as failure: %s",
                dest_type,
                exc,
            )
            result = DeliveryResult(success=False, error=f"destination raised: {exc}")

        outcome = DestinationOutcome(
            destination_type=dest_type,
            success=bool(result.success),
            ref=result.ref,
            error=result.error,
        )
        deliveries.append(outcome)
        await _audit_deliver(audit_log, session, token_short, outcome)

    # 4. Audit complete summary.
    success_count = sum(1 for d in deliveries if d.success)
    failure_count = len(deliveries) - success_count
    overall_success = success_count > 0
    await audit_log.write(
        AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="submit.complete",
            session_token=token_short,
            brief_type=session.brief_type,
            target_agent=session.target_agent,
            data={
                "total_destinations": len(deliveries),
                "successful": success_count,
                "failed": failure_count,
                "overall_success": overall_success,
            },
        )
    )

    return SubmitResult(
        success=overall_success,
        validation_issues=[],
        deliveries=deliveries,
    )


async def _audit_deliver(
    audit_log: AuditLog,
    session,
    token_short: str,
    outcome: DestinationOutcome,
) -> None:
    """Write a ``submit.deliver`` audit event for a single destination outcome.

    Pulled out of ``submit_brief`` so the multiple early-continue paths
    in the destination loop don't each repeat the audit-event boilerplate.
    """
    await audit_log.write(
        AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="submit.deliver",
            session_token=token_short,
            brief_type=session.brief_type,
            target_agent=session.target_agent,
            data={
                "destination_type": outcome.destination_type,
                "success": outcome.success,
                "ref": outcome.ref,
                "error": outcome.error,
            },
        )
    )


def _enrich_sections_with_spec(
    submitted: list[dict],
    *,
    session_specs: list[SectionSpec],
    palette: dict[str, int],
    context: dict,
) -> list[dict]:
    """Override title + color on each submitted section with spec authority.

    Title and color are owned by the playbook, not the agent. The agent's
    submit_brief call carries content (section_id + fields); spec metadata
    is filled in here so destinations always render a complete header.
    Sections whose ``section_id`` doesn't match any spec pass through
    unchanged — defensive (validation should already reject these).
    """
    spec_by_id = {spec.section_id: spec for spec in session_specs}
    enriched: list[dict] = []
    for raw in submitted:
        section_id = raw.get("section_id")
        spec = spec_by_id.get(section_id) if isinstance(section_id, str) else None
        if spec is None:
            enriched.append(dict(raw))
            continue
        # ``resolve_colors_for_sections`` handles palette names and
        # dynamic ``{dynamic, expr, if_true, if_false}`` shapes; an
        # already-resolved int (from test fixtures or extension sections
        # with literal colors) bypasses resolution.
        if isinstance(spec.color, int):
            color: int = spec.color
        else:
            [resolved_spec] = resolve_colors_for_sections(
                [spec], palette, context=context
            )
            color = resolved_spec.color  # type: ignore[assignment]
        out = dict(raw)
        out["title"] = spec.title
        out["color"] = color
        enriched.append(out)
    return enriched


__all__ = ["DestinationOutcome", "SubmitResult", "submit_brief"]
