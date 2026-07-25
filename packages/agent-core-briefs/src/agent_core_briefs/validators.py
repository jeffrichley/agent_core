"""Submission-level validators for the brief framework.

Where this fits
---------------
:mod:`agent_core_briefs.tools` carries a per-section validator
(:func:`agent_core_briefs.tools.validate_section`) that the agent calls
mid-compose to check one section's draft. The validator here runs at
submit time across the *entire* submission: every required section is
present, every conditional-active section is present, no unknown
sections sneak through, every required field is populated, every
``max_chars`` is respected, and the total section count fits Discord's
10-embed-per-message ceiling.

Returning structured :class:`ValidationIssue` objects (rather than raw
strings) lets the submit handler (T13) write the issues into the audit
log JSON without re-parsing them. The agent receives the same list as
part of the :class:`agent_core_briefs.submit.SubmitResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_core_briefs.session import ComposeSession


@dataclass(frozen=True)
class ValidationIssue:
    """One validation finding against a brief submission.

    ``section_id`` is ``None`` for cross-section issues (e.g., a
    playbook with no destinations configured). ``code`` is a short,
    stable string suitable for filtering/aggregation in the audit log;
    ``message`` is human-readable detail.
    """

    section_id: str | None
    code: str
    message: str


def validate_submission(
    *,
    session: ComposeSession,
    sections: list[dict[str, Any]],
) -> list[ValidationIssue]:
    """Validate the agent's submitted sections against the session's spec.

    Checks performed (in order; all checks run regardless of earlier
    failures so the agent sees the full set of issues at once):

    - Playbook has at least one destination configured
    - Every required + conditional-active section is present
    - No unknown sections (must be in ``session.sections``,
      ``session.conditional_sections``, or ``session.extension_sections``)
    - Per-section: required fields present and non-empty, max_chars
      respected, no unknown field names

    Destination-specific limits (e.g., Discord's 10-embed-per-message
    ceiling) are enforced inside the destination, not here — a brief
    targeting only ``markdown_file`` shouldn't be rejected for an
    embed-count rule that doesn't apply to it.

    Returns an empty list if the submission is valid; otherwise a list
    of :class:`ValidationIssue` in roughly best-actionable-first order
    (cross-cutting issues, then missing required sections, then
    unknown sections, then per-section field issues).
    """
    issues: list[ValidationIssue] = []

    # Cross-cutting: a playbook with no destinations is undeliverable. Surface
    # as a validation failure so the submit handler short-circuits BEFORE
    # consuming the session token — the agent can fix the playbook and retry.
    if not session.destinations:
        issues.append(
            ValidationIssue(
                section_id=None,
                code="no_destinations_configured",
                message="playbook has no destinations configured; brief cannot be delivered",
            )
        )

    submitted_by_id: dict[str, dict[str, Any]] = {}
    for entry in sections:
        sid = entry.get("section_id") if isinstance(entry, dict) else None
        if isinstance(sid, str) and sid:
            submitted_by_id[sid] = entry

    expected_required = set(session.sections_required) | set(session.sections_conditional_active)
    expected_optional = set(session.sections_optional)
    extension_ids = {s.section_id for s in session.extension_sections}
    # Conditional section ids that gated truthy come in via
    # sections_conditional_active (already in expected_required) — but listing
    # them in expected_all explicitly via conditional_sections covers the case
    # where a conditional section is in the spec dict without its id surfacing
    # in the active list (defensive; same set in practice).
    conditional_ids = {s.section_id for s in session.conditional_sections}
    expected_all = expected_required | expected_optional | extension_ids | conditional_ids

    # Required missing.
    for sid in sorted(expected_required):
        if sid not in submitted_by_id:
            issues.append(
                ValidationIssue(
                    section_id=sid,
                    code="missing_required_section",
                    message=f"required section {sid!r} not in submission",
                )
            )

    # Unknown sections (submitted but not in playbook + extensions).
    for sid in sorted(submitted_by_id):
        if sid not in expected_all:
            issues.append(
                ValidationIssue(
                    section_id=sid,
                    code="unknown_section",
                    message=f"submitted section {sid!r} not in playbook or extensions",
                )
            )

    # Per-section field validation. Iterate the submitted list in order so
    # error ordering tracks the agent's input shape; skip sections we already
    # flagged as unknown to avoid duplicate noise.
    spec_by_id = {s.section_id: s for s in session.sections}
    for ext_section in session.extension_sections:
        spec_by_id[ext_section.section_id] = ext_section
    # C1: conditional sections that gated active still need their fields
    # validated. Without this union, `submitted_by_id["weekly_digest"]`
    # would be recognized as expected (because the id is in
    # sections_conditional_active) but `spec_by_id` wouldn't carry the
    # SectionSpec, so per-section required-field/max-chars checks would
    # silently skip — Pepper's morning_brief weekly_digest depends on
    # the framework catching missing required fields here.
    for cond_section in session.conditional_sections:
        spec_by_id[cond_section.section_id] = cond_section

    for submitted in sections:
        if not isinstance(submitted, dict):
            continue
        sid = submitted.get("section_id")
        if not isinstance(sid, str) or sid not in spec_by_id:
            # Either malformed or already flagged as unknown above.
            continue
        spec = spec_by_id[sid]
        spec_field_names = {f.name for f in spec.fields}
        submitted_fields_raw = submitted.get("fields") or []
        # Build name -> value map; non-mapping field entries are ignored
        # (the per-section validator is the one that enforces field shape;
        # here we just want a tolerant mapping for required/max checks).
        submitted_fields: dict[str, str] = {}
        for entry in submitted_fields_raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            value = entry.get("value", "")
            submitted_fields[name] = "" if value is None else str(value)

        # Required-field check (empty / whitespace-only counts as missing).
        for field_spec in spec.fields:
            if not field_spec.required:
                continue
            value = submitted_fields.get(field_spec.name, "")
            if not value.strip():
                issues.append(
                    ValidationIssue(
                        section_id=sid,
                        code="missing_required_field",
                        message=f"section {sid!r} field {field_spec.name!r} is required",
                    )
                )

        # max_chars check.
        for field_spec in spec.fields:
            if field_spec.max_chars is None:
                continue
            value = submitted_fields.get(field_spec.name, "")
            if len(value) > field_spec.max_chars:
                issues.append(
                    ValidationIssue(
                        section_id=sid,
                        code="field_over_max_chars",
                        message=(
                            f"section {sid!r} field {field_spec.name!r} length "
                            f"{len(value)} > max_chars {field_spec.max_chars}"
                        ),
                    )
                )

        # Unknown fields (named in submission but not in spec).
        for fname in submitted_fields:
            if fname not in spec_field_names:
                issues.append(
                    ValidationIssue(
                        section_id=sid,
                        code="unknown_field",
                        message=(
                            f"section {sid!r} has unknown field {fname!r} "
                            f"(spec: {sorted(spec_field_names)})"
                        ),
                    )
                )

    return issues


__all__ = ["ValidationIssue", "validate_submission"]
