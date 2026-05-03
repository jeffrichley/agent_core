"""Projector protocol + registry + fallback.

A Projector renders a bus envelope into a Tool 3 summary row, or returns
None to skip the envelope from the projected stream (e.g., heartbeat noise
that should not appear in daily summaries).

Lookup priority:
1. ``payload.type`` for ``Event`` envelopes (e.g., "HandoffReady")
2. ``envelope.kind`` for non-Events (e.g., "TextMessage")
3. fallback_projector — never returns None; renders generic content
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.bus.envelope import Envelope, EventPayload


@runtime_checkable
class Projector(Protocol):
    """Render a bus envelope into a Tool 3 row, or skip it."""

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None: ...


_REGISTRY: dict[str, Projector] = {}


def register_projector(key: str, projector: Projector) -> None:
    """Register a projector for an Event payload type or envelope kind.

    Re-registering the same key replaces the prior projector. This is
    intentional: pluggy entry points populate defaults at import time;
    application code may override programmatically (e.g., a test).
    """
    _REGISTRY[key] = projector


def reset_registry() -> None:
    """Clear all registrations. For tests + bootstrap re-init."""
    _REGISTRY.clear()


def get_projector(envelope: Envelope) -> Projector:
    """Resolve the projector for an envelope per the lookup priority.

    Returns the fallback projector if no specific projector is registered;
    never returns None — every envelope has a projector that will render
    *something*, possibly via the generic fallback shape.
    """
    if isinstance(envelope.payload, EventPayload):
        by_type = _REGISTRY.get(envelope.payload.type)
        if by_type is not None:
            return by_type
    by_kind = _REGISTRY.get(envelope.kind)
    if by_kind is not None:
        return by_kind
    return fallback_projector


class _FallbackProjector:
    """Last-resort projector — renders any envelope into a generic row.

    Never returns None: keeps unknown envelope kinds visible in summaries
    instead of silently dropping them. Concrete projectors should override
    by registering against a specific kind / type id.
    """

    def render(
        self,
        envelope: Envelope,
        *,
        perspective: str,
        timezone: str,
    ) -> dict | None:
        # Real body lands in Task 2 (TextMessage + real fallback).
        # Raise instead of returning a placeholder dict so any caller that
        # tries to render via the fallback before Task 2 ships fails loudly
        # rather than producing a wrong-shaped summary row silently.
        raise NotImplementedError(
            "fallback_projector.render lands in Task 2 of the bus log pipeline plan"
        )


fallback_projector: Projector = _FallbackProjector()
