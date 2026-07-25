"""Agent-facing tools for the brief framework.

Five tools the agent calls between ``ComposeBrief`` (T9 wake-up) and
``submit_brief`` (T13). Each tool takes the in-flight session token plus
keyword arguments and returns a JSON-serializable dict; T14 wraps these as
MCP tool calls so the agent can invoke them from inside the compose loop.

Tools are ``async def`` for protocol consistency even though v1 has no
I/O — future variants (e.g., a guidance-file fetcher inside
``get_section_spec``) are easier to add this way without breaking the
surface. Each function delegates session lookup to
:meth:`SessionRegistry.get`, so unknown / expired / consumed token errors
propagate up unchanged.

Tool surface
------------
- :func:`list_sections` — what sections the brief expects
- :func:`get_section_spec` — full spec for one section, with color resolved
- :func:`validate_section` — agent's draft for one section, returns issues
- :func:`compress_sections` — proportional budget allocation across sections
- :func:`add_extension_section` — append an ad-hoc section to the session
"""

from __future__ import annotations

from typing import Any

from agent_core_briefs.playbook import resolve_colors_for_sections
from agent_core_briefs.protocol import FieldSpec, SectionSpec
from agent_core_briefs.session import ComposeSession, SessionRegistry

# Default per-section budget when computing proportional allocations for
# sections that have no explicit ``max_chars`` cap. 500 is a reasonable
# midpoint for a Discord-style embed field; the value is documented in
# :func:`compress_sections` so callers know the implicit weighting.
_DEFAULT_BUDGET_WEIGHT = 500


async def list_sections(registry: SessionRegistry, token: str) -> dict[str, Any]:
    """Return the section IDs the brief expects.

    Returns:
        ``{"required": [...], "optional": [...],
        "conditional_active": [...], "brief_type": "...", "voice": "..."}``
    """
    session = registry.get(token)
    return {
        "required": list(session.sections_required),
        "optional": list(session.sections_optional),
        "conditional_active": list(session.sections_conditional_active),
        "brief_type": session.brief_type,
        "voice": session.voice,
    }


async def get_section_spec(
    registry: SessionRegistry, token: str, *, section_id: str
) -> dict[str, Any]:
    """Return the section's spec (title, color, fields, guidance).

    Color is resolved to int decimal (looked up against the playbook's
    palette, or evaluated for dynamic colors). Raises ``ValueError`` if
    ``section_id`` isn't present in the session's sections, extensions,
    or active conditional sections.
    """
    session = registry.get(token)
    section = _find_section(session, section_id)

    # Resolve dynamic / palette-name colors to int decimal so the agent
    # doesn't have to know the palette. Static-int colors (already
    # resolved at orchestrator-time or set by add_extension_section) pass
    # through via the int branch in _resolve_color.
    color = _resolve_color(section, session.colors_palette, session.context)

    return {
        "section_id": section.section_id,
        "title": section.title,
        "color": color,
        "required": bool(section.required),
        "fields": [_field_to_dict(f) for f in section.fields],
    }


async def validate_section(
    registry: SessionRegistry,
    token: str,
    *,
    section_id: str,
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the agent's draft for one section.

    fields shape: ``[{"name": "Today", "value": "..."}]``

    Validation rules (v1):

    - Every required field is present and non-empty
    - Each field's value <= ``max_chars`` (if set)
    - Field names match the spec's field names exactly (extras flagged)
    """
    session = registry.get(token)
    section = _find_section(session, section_id)

    issues: list[str] = []

    # Build name -> value lookup. Field name collisions in the input are
    # silently last-write-wins; the spec doesn't address dup field names
    # because the playbook author shouldn't define duplicates either.
    submitted: dict[str, str] = {}
    extra_names: list[str] = []
    spec_names = {f.name for f in section.fields}
    for entry in fields:
        name = entry.get("name")
        value = entry.get("value", "")
        if not isinstance(name, str):
            issues.append(f"field entry missing string 'name': {entry!r}")
            continue
        if name not in spec_names:
            extra_names.append(name)
            continue
        submitted[name] = "" if value is None else str(value)

    # Required-field check + max_chars check, in spec order.
    for field_spec in section.fields:
        if field_spec.required:
            value = submitted.get(field_spec.name, "")
            if not value:
                issues.append(f"field {field_spec.name!r} is required")
        if field_spec.max_chars is not None:
            value = submitted.get(field_spec.name, "")
            if len(value) > field_spec.max_chars:
                issues.append(
                    f"field {field_spec.name!r} over max_chars "
                    f"({len(value)} > {field_spec.max_chars})"
                )

    # Unknown field names: flagged after required/max_chars so the more
    # actionable issues come first in the list.
    for name in extra_names:
        issues.append(f"field {name!r} is not in section spec")

    return {
        "section_id": section.section_id,
        "valid": not issues,
        "issues": issues,
    }


async def compress_sections(
    registry: SessionRegistry,
    token: str,
    *,
    section_ids: list[str],
    target_total_chars: int,
) -> dict[str, Any]:
    """Suggest compression budgets for the given sections.

    Strategy: each section's weight is the sum of ``max_chars`` across
    *all* of its fields that have a ``max_chars`` set (not just required
    fields — capping at the spec's stated bound is the right per-section
    weight). Sections with no max-chars-bounded fields fall back to a
    default weight of ``_DEFAULT_BUDGET_WEIGHT`` (500). Budgets are split
    proportionally to weight so ``sum(allocations) == target_total_chars``
    exactly — the last section absorbs the rounding remainder.

    A ``target_total_chars`` of 0 returns 0-budgets across all sections.

    Returns:
        ``{"target_total_chars": 1500,
        "allocations": [{"section_id": "...", "budget": 200}, ...]}``

    The agent does the actual compression — this tool only allocates.
    """
    session = registry.get(token)

    # Resolve sections (extensions + active conditionals allowed) and compute
    # per-section weights.
    weights: list[tuple[str, int]] = []
    for sid in section_ids:
        section = _find_section(session, sid)
        weight = sum(
            f.max_chars for f in section.fields if isinstance(f.max_chars, int) and f.max_chars > 0
        )
        if weight <= 0:
            weight = _DEFAULT_BUDGET_WEIGHT
        weights.append((sid, weight))

    total_weight = sum(w for _, w in weights)

    allocations: list[dict[str, Any]] = []
    if target_total_chars <= 0 or total_weight <= 0:
        # Edge case: zero budget or zero weight → all zeros. Keeps the
        # tool's output shape identical so callers don't branch on it.
        for sid, _ in weights:
            allocations.append({"section_id": sid, "budget": 0})
        return {
            "target_total_chars": int(target_total_chars),
            "allocations": allocations,
        }

    running_total = 0
    for idx, (sid, weight) in enumerate(weights):
        if idx == len(weights) - 1:
            # Last section absorbs rounding remainder so the sum is exact.
            budget = target_total_chars - running_total
        else:
            budget = (target_total_chars * weight) // total_weight
            running_total += budget
        allocations.append({"section_id": sid, "budget": int(budget)})

    return {
        "target_total_chars": int(target_total_chars),
        "allocations": allocations,
    }


async def add_extension_section(
    registry: SessionRegistry,
    token: str,
    *,
    section_id: str,
    title: str,
    color: int | str,
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add an ad-hoc extension section to the in-flight session.

    Used when the agent decides a brief needs a section the playbook
    didn't predefine (e.g., a one-off "today's blockers" callout). The
    section appends to ``session.extension_sections``; T11/T12
    destinations include extensions in the rendered output.

    Raises ``ValueError`` if ``section_id`` collides with any existing
    section (playbook-defined, active conditional, OR previously added
    extension).
    """
    session = registry.get(token)

    # Collision check covers playbook sections (active set), conditional
    # sections that gated truthy for this brief, and previously-added
    # extensions on the same session.
    existing_ids = {s.section_id for s in session.sections}
    existing_ids.update(s.section_id for s in session.extension_sections)
    existing_ids.update(s.section_id for s in session.conditional_sections)
    if section_id in existing_ids:
        raise ValueError(
            f"section_id {section_id!r} already present in session "
            "(playbook section or prior extension)"
        )

    field_specs = [_field_from_dict(f, section_id) for f in fields]

    # Color: int passes through (already resolved). String stays as a
    # palette name; the renderer will resolve it via the session's
    # palette at deliver-time. Anything else is a programmer bug —
    # extension authors are agent code, not playbook YAML.
    if not isinstance(color, (int, str)):
        raise ValueError(
            f"extension section {section_id!r} color must be int or str, got {type(color).__name__}"
        )

    section = SectionSpec(
        section_id=section_id,
        title=str(title),
        color=color,
        required=False,
        fields=field_specs,
    )
    session.extension_sections.append(section)

    return {
        "section_id": section.section_id,
        "title": section.title,
        "color": color,
        "required": False,
        "fields": [_field_to_dict(f) for f in field_specs],
    }


# -- private helpers --------------------------------------------------------


def _find_section(session: ComposeSession, section_id: str) -> SectionSpec:
    """Look up a section by id across playbook + conditional + extension lists.

    Walks ``session.sections`` (playbook required/optional), then
    ``session.conditional_sections`` (only the conditionals that gated
    truthy at session-build time — the orchestrator pre-filters this
    list), then ``session.extension_sections`` (agent-added).

    Raises ``ValueError`` if the section_id isn't present anywhere — the
    tools.py public surface promises ValueError, not KeyError, so callers
    don't have to catch both.
    """
    for section in session.sections:
        if section.section_id == section_id:
            return section
    for section in session.conditional_sections:
        if section.section_id == section_id:
            return section
    for section in session.extension_sections:
        if section.section_id == section_id:
            return section
    raise ValueError(f"section {section_id!r} is not in the brief")


def _resolve_color(
    section: SectionSpec,
    palette: dict[str, int],
    context: dict[str, Any],
) -> int:
    """Resolve a section's color to int decimal.

    Static-int colors pass through (extension sections may carry these
    pre-resolved). Strings and dynamic-color dicts go through the
    playbook helper, which raises PlaybookParseError for undefined
    palette names.
    """
    if isinstance(section.color, int) and not isinstance(section.color, bool):
        return section.color
    # Reuse the playbook helper for palette-name + dynamic resolution.
    # Returns a single-section list; we just need its color.
    [resolved] = resolve_colors_for_sections([section], palette, context=context)
    color = resolved.color
    if not isinstance(color, int):
        # Defensive — resolve_colors_for_sections always returns int, but
        # mypy and future refactors benefit from the explicit guard.
        raise ValueError(f"section {section.section_id!r} color did not resolve to int: {color!r}")
    return color


def _field_to_dict(spec: FieldSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "required": spec.required,
        "max_chars": spec.max_chars,
        "guidance": spec.guidance,
    }


def _field_from_dict(raw: Any, section_id: str) -> FieldSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"extension field in section {section_id!r} is not a mapping: {raw!r}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"extension field in section {section_id!r} missing 'name'")
    max_chars = raw.get("max_chars")
    if max_chars is not None and not isinstance(max_chars, int):
        raise ValueError(
            f"extension field {name!r} in section {section_id!r} "
            f"max_chars must be int, got {type(max_chars).__name__}"
        )
    return FieldSpec(
        name=name,
        required=bool(raw.get("required", False)),
        max_chars=max_chars,
        guidance=raw.get("guidance"),
    )


__all__ = [
    "add_extension_section",
    "compress_sections",
    "get_section_spec",
    "list_sections",
    "validate_section",
]
