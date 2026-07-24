"""Playbook parser: YAML-in-MD with simpleeval expressions.

A playbook is a Markdown file containing fenced ``yaml`` blocks. Each block is
classified by its top-level keys: metadata, destinations, colors, section, or
conditional section. The parser extracts blocks, applies ``${var}`` substitution
at parse time (via :func:`agent_core_briefs.config.substitute_vars`), and yields
a :class:`Playbook` dataclass.

Color resolution and conditional-section gating are deferred to runtime — both
expose simpleeval evaluation against a context dict (gathered data + ``now``).
``simpleeval`` does not natively support attribute access on dicts; we wrap the
context with :class:`_AttrDict` so expressions can use ``foo.bar`` instead of
``foo['bar']``. Comprehensions are enabled via ``EvalWithCompoundTypes``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

# simpleeval ships no py.typed marker / stubs (see [tool.mypy.overrides]).
from simpleeval import (
    AttributeDoesNotExist,
    EvalWithCompoundTypes,
    NameNotDefined,
)

from agent_core_briefs.config import substitute_vars
from agent_core_briefs.protocol import FieldSpec, SectionSpec

_YAML_BLOCK_PATTERN = re.compile(
    r"```yaml\s*\n(.*?)\n```",
    re.DOTALL,
)


class PlaybookParseError(ValueError):
    """Raised when a playbook is structurally invalid or references undefined names."""


@dataclass(frozen=True)
class Playbook:
    """Framework-loaded playbook: parsed YAML-in-MD blocks, var-substituted."""

    brief_type: str
    voice: str
    schedule_cron: str | None
    gather_config_path: str | None
    colors: dict[str, int]
    destinations: list[dict[str, Any]] = field(default_factory=list)
    sections: list[SectionSpec] = field(default_factory=list)
    conditional_sections: list[SectionSpec] = field(default_factory=list)


def parse_playbook(path: Path, *, vars_map: dict[str, str]) -> Playbook:
    """Parse a YAML-in-MD playbook file.

    Applies ``${var}`` substitution at parse time using ``vars_map`` (raises
    if a ``${var}`` reference is undefined). Raises :class:`PlaybookParseError`
    on missing ``brief_type`` or other structural errors.
    """
    text = Path(path).read_text(encoding="utf-8")
    raw_blocks = _extract_yaml_blocks(text)

    metadata: dict[str, Any] | None = None
    destinations: list[dict[str, Any]] = []
    colors: dict[str, int] = {}
    sections: list[SectionSpec] = []
    conditional_sections: list[SectionSpec] = []

    for raw in raw_blocks:
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise PlaybookParseError(f"invalid YAML block in {path}: {exc}") from exc

        if not isinstance(parsed, dict):
            raise PlaybookParseError(
                f"expected mapping at top level of YAML block in {path}, got {type(parsed).__name__}"
            )

        substituted = substitute_vars(parsed, vars_map)
        kind = _classify_block(substituted)

        if kind == "metadata":
            if metadata is not None:
                raise PlaybookParseError(f"duplicate metadata block in {path}")
            metadata = substituted
        elif kind == "destinations":
            destinations = list(substituted["destinations"] or [])
        elif kind == "colors":
            colors = dict(substituted["colors"] or {})
        elif kind == "conditional_section":
            conditional_sections.append(_to_section_spec(substituted))
        elif kind == "section":
            sections.append(_to_section_spec(substituted))
        else:
            # Reachable: any block whose top-level keys don't match the four
            # classifiers (e.g., a misspelled ``section-id``). Surface loudly
            # rather than silently dropping the block.
            raise PlaybookParseError(f"unrecognized YAML block in {path}: keys={list(substituted)}")

    if metadata is None or "brief_type" not in metadata:
        raise PlaybookParseError(f"playbook {path} is missing required 'brief_type' metadata")

    # Validate color palette types early — a YAML author writing ``RED: "ff0000"``
    # would otherwise typed-lie in Playbook.colors: dict[str, int].
    for name, val in colors.items():
        if not isinstance(val, int) or isinstance(val, bool):
            raise PlaybookParseError(
                f"color {name!r} must be int decimal, got {type(val).__name__}"
            )

    schedule = metadata.get("schedule")
    schedule_cron: str | None = None
    if isinstance(schedule, dict):
        cron = schedule.get("cron")
        schedule_cron = cron if isinstance(cron, str) else None

    gather_config = metadata.get("gather_config")
    gather_config_path = gather_config if isinstance(gather_config, str) else None

    return Playbook(
        brief_type=str(metadata["brief_type"]),
        voice=str(metadata.get("voice", "")),
        schedule_cron=schedule_cron,
        gather_config_path=gather_config_path,
        colors=colors,
        destinations=destinations,
        sections=sections,
        conditional_sections=conditional_sections,
    )


def resolve_colors_for_sections(
    sections: list[SectionSpec],
    colors_palette: dict[str, int],
    *,
    context: dict[str, Any],
) -> list[SectionSpec]:
    """Return new SectionSpec list with ``color`` resolved to int decimal.

    Static color (str): looks up palette by name. Raises if undefined.
    Dynamic color (dict): evaluates ``expr`` via simpleeval against context;
    picks ``if_true``/``if_false`` palette name; resolves to int.
    """
    resolved: list[SectionSpec] = []
    for section in sections:
        decimal = _resolve_color_value(section, colors_palette, context)
        resolved.append(replace(section, color=decimal))
    return resolved


def resolve_conditional_sections(
    conditional_sections: list[SectionSpec],
    context: dict[str, Any],
) -> list[str]:
    """Evaluate each conditional section's ``when.expr`` via simpleeval.

    Returns a list of ``section_id`` values whose expression evaluated truthy,
    in document order.
    """
    active: list[str] = []
    for section in conditional_sections:
        if section.when is None or "expr" not in section.when:
            raise PlaybookParseError(
                f"conditional section {section.section_id!r} missing when.expr"
            )
        if _eval_expr(section.when["expr"], context):
            active.append(section.section_id)
    return active


# -- private helpers --------------------------------------------------------


def _extract_yaml_blocks(text: str) -> list[str]:
    """Return the raw YAML body of every fenced ``yaml`` block in ``text``."""
    return _YAML_BLOCK_PATTERN.findall(text)


def _classify_block(block: dict[str, Any]) -> str:
    """Discriminate a parsed YAML block by its keys.

    Order matters: metadata is detected first because a stray ``brief_type``
    inside a section block would be a structural error we want to surface
    elsewhere, but in practice no other block carries that key.
    """
    if "brief_type" in block:
        return "metadata"
    if "destinations" in block:
        return "destinations"
    if "section_id" in block:
        return "conditional_section" if "when" in block else "section"
    if "colors" in block:
        return "colors"
    return "unknown"


def _to_section_spec(block: dict[str, Any]) -> SectionSpec:
    """Convert a substituted YAML mapping into a :class:`SectionSpec`."""
    section_id = block.get("section_id")
    if not isinstance(section_id, str) or not section_id:
        raise PlaybookParseError(f"section block missing section_id: {block!r}")

    title = block.get("title", "")
    color = block.get("color", "")
    fields_raw = block.get("fields") or []
    field_specs = [_to_field_spec(f, section_id) for f in fields_raw]

    return SectionSpec(
        section_id=section_id,
        title=str(title),
        color=color,
        required=bool(block.get("required", False)),
        required_context=list(block.get("required_context") or []),
        allow_compression=bool(block.get("allow_compression", False)),
        fields=field_specs,
        when=block.get("when") if isinstance(block.get("when"), dict) else None,
        required_when_active=bool(block.get("required_when_active", False)),
    )


def _to_field_spec(raw: Any, section_id: str) -> FieldSpec:
    if not isinstance(raw, dict):
        raise PlaybookParseError(f"field in section {section_id!r} is not a mapping: {raw!r}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise PlaybookParseError(f"field in section {section_id!r} missing name")
    return FieldSpec(
        name=name,
        required=bool(raw.get("required", False)),
        max_chars=raw.get("max_chars") if isinstance(raw.get("max_chars"), int) else None,
        guidance=raw.get("guidance"),
    )


def _resolve_color_value(
    section: SectionSpec,
    palette: dict[str, int],
    context: dict[str, Any],
) -> int:
    color = section.color
    if isinstance(color, str):
        if color not in palette:
            raise PlaybookParseError(f"undefined color {color!r} in section {section.section_id!r}")
        return palette[color]
    if isinstance(color, dict):
        if not color.get("dynamic"):
            raise PlaybookParseError(
                f"dynamic color in section {section.section_id!r} missing dynamic: true"
            )
        expr = color.get("expr")
        if not isinstance(expr, str):
            raise PlaybookParseError(
                f"dynamic color in section {section.section_id!r} missing expr"
            )
        # Strict evaluation — authoring bugs (typos in YAML expressions) must
        # fail loudly to be discoverable. Runtime fetcher failures degrade
        # through the gather context's _errors channel; mixing the two failure
        # modes silently would make color expressions the only place in the
        # parser where typos are invisible. _eval_expr wraps simpleeval errors
        # in PlaybookParseError so callers don't import simpleeval to catch.
        truthy = bool(_eval_expr(expr, context))
        chosen_name = color.get("if_true") if truthy else color.get("if_false")
        if not isinstance(chosen_name, str):
            raise PlaybookParseError(
                f"dynamic color in section {section.section_id!r} "
                f"missing {'if_true' if truthy else 'if_false'} palette name"
            )
        if chosen_name not in palette:
            raise PlaybookParseError(
                f"undefined color {chosen_name!r} in section {section.section_id!r}"
            )
        return palette[chosen_name]
    raise PlaybookParseError(
        f"section {section.section_id!r} color is neither string nor dict: {color!r}"
    )


def _eval_expr(expr: str, context: dict[str, Any]) -> Any:
    """Evaluate ``expr`` via simpleeval against ``context``.

    Wraps top-level dict values in :class:`_AttrDict` so expressions can use
    attribute access (``projects.active``) rather than item access. Enables
    ``any``/``all``/``len`` so conditional gating expressions stay readable.

    Wraps simpleeval's native exceptions in :class:`PlaybookParseError` so
    consumers (orchestrator, destinations, submit handler) don't have to
    import simpleeval to catch authoring-bug exceptions.
    """
    try:
        evaluator = EvalWithCompoundTypes()
        evaluator.functions = {**evaluator.functions, "any": any, "all": all, "len": len}
        evaluator.names = _wrap_context(context)
        return evaluator.eval(expr)
    except (NameNotDefined, AttributeDoesNotExist) as exc:
        raise PlaybookParseError(f"expression failed: {expr!r}: {exc}") from exc


def _wrap_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Top-level keys map directly to names; values are wrapped recursively."""
    wrapped: dict[str, Any] = {}
    for key, value in ctx.items():
        if isinstance(value, dict):
            wrapped[key] = _AttrDict(value)
        elif isinstance(value, list):
            wrapped[key] = [_AttrDict(v) if isinstance(v, dict) else v for v in value]
        else:
            wrapped[key] = value
    return wrapped


class _AttrDict:
    """Dict wrapper that exposes keys as attributes for simpleeval expressions.

    Recursively wraps nested dicts; lists of dicts become lists of ``_AttrDict``.
    Iteration walks the underlying mapping's keys.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        # __slots__ stores _data in the instance; __getattr__ only fires for
        # missing names. Anything not in the wrapped dict is a real AttributeError.
        data = object.__getattribute__(self, "_data")
        if name not in data:
            raise AttributeError(name)
        value = data[name]
        if isinstance(value, dict):
            return _AttrDict(value)
        if isinstance(value, list):
            return [_AttrDict(v) if isinstance(v, dict) else v for v in value]
        return value

    def __getitem__(self, key: str) -> Any:
        data = object.__getattribute__(self, "_data")
        value = data[key]
        if isinstance(value, dict):
            return _AttrDict(value)
        if isinstance(value, list):
            return [_AttrDict(v) if isinstance(v, dict) else v for v in value]
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(object.__getattribute__(self, "_data"))

    def __contains__(self, key: object) -> bool:
        return key in object.__getattribute__(self, "_data")

    def __repr__(self) -> str:
        return f"_AttrDict({object.__getattribute__(self, '_data')!r})"
