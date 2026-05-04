"""Core protocols + types for the brief framework.

A Fetcher provides deterministic data acquisition. A Destination provides
deterministic format + transport. A SectionSpec describes the shape of one
section in a playbook. A PlaybookRef is the framework-loaded playbook handle.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


@runtime_checkable
class Fetcher(Protocol):
    """Pluggable data-acquisition unit. Returns a JSON-serializable dict
    that gets merged under ``namespace`` in the gathered context."""

    type_id: str
    namespace: str

    async def fetch(self, config: dict, when: datetime) -> dict: ...


@dataclass(frozen=True)
class DeliveryResult:
    """Per-destination delivery outcome."""

    success: bool
    ref: str | None = None
    error: str | None = None


@runtime_checkable
class Destination(Protocol):
    """Pluggable format + transport unit. Renders sections to native shape
    and delivers; returns a DeliveryResult.

    Destinations are filesystem-discovered and instantiated parameterless,
    so the bus handle they need to publish through is threaded in at
    ``deliver`` time rather than at construction. Destinations that don't
    need to publish (e.g., ``markdown_file``) accept the parameter and
    ignore it.
    """

    type_id: str

    async def deliver(
        self,
        sections: list[dict],
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
        config: dict,
        bus_handle: BusHandle,
    ) -> DeliveryResult: ...


def validate_destination_signature(destination: object) -> None:
    """Verify a Destination instance's ``deliver()`` has the required parameter shape.

    The ``runtime_checkable`` Protocol check only confirms attribute
    presence; it does NOT validate parameter names or count. An old-style
    ``deliver(self, sections, playbook, scope, when, config)`` (without
    ``bus_handle``) passes ``isinstance(d, Destination)`` and then blows
    up at first publish with::

        TypeError: deliver() takes 6 positional arguments but 7 were given

    This helper closes that gap. T13's submit handler calls it once at
    orchestrator-config-load time for each registered destination so
    plugin authors discover signature drift before any brief delivery
    attempt.

    Required signature:
    ``deliver(self, sections, playbook, scope, when, config, bus_handle)``.

    Raises:
        TypeError: if any required parameter is missing from the
            ``deliver`` signature. The error names the destination class
            and the missing parameters so the fix is obvious.
    """
    sig = inspect.signature(destination.deliver)  # type: ignore[attr-defined]
    required = {"sections", "playbook", "scope", "when", "config", "bus_handle"}
    actual = set(sig.parameters.keys())
    missing = required - actual
    if missing:
        raise TypeError(
            f"{type(destination).__name__}.deliver() missing required "
            f"parameters: {sorted(missing)}. Required: "
            f"deliver(self, sections, playbook, scope, when, config, bus_handle)"
        )


@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool = False
    max_chars: int | None = None
    guidance: str | dict | None = None  # str or {"file": "path-relative-to-playbook"}


@dataclass(frozen=True)
class SectionSpec:
    section_id: str
    title: str
    color: str | dict  # palette name or {dynamic, expr, if_true, if_false}
    required: bool = False
    required_context: list[str] = field(default_factory=list)
    allow_compression: bool = False
    fields: list[FieldSpec] = field(default_factory=list)
    when: dict | None = None  # {expr: "..."} for conditional sections
    required_when_active: bool = False


@dataclass(frozen=True)
class PlaybookRef:
    """Framework-loaded handle for a playbook. Knows the file location
    so it can resolve guidance file references and find sibling configs."""

    brief_type: str
    path: Path
    sections_required: list[str]
    sections_optional: list[str]
    sections_conditional_active: list[str] = field(default_factory=list)
