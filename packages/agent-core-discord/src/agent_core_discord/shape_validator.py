"""shape_validator — pure-function recognized-shape catalog for the Discord
adapter's strict-mode envelope validation.

Closes the silent-drop class on the discord-send surface (#114). The
adapter calls validate(envelope) at the top of deliver(): on
Unrecognized, a yellow failed-delivery Acknowledgment is published to
the sender with the canonical equivalent named; on Recognized + a
deprecation_log_line, a structured log fires and dispatch proceeds; on
Recognized + None (the canonical shape), dispatch proceeds silently.

PURE module: no I/O, no Discord client, no global state. Unit-testable
in isolation. See docs/superpowers/specs/2026-05-23-issue-114-discord-
send-unified-envelope-design.md for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.bus.envelope import Envelope


@dataclass(frozen=True)
class Recognized:
    """Validator outcome: the envelope matches a known shape.

    shape_name: stable identifier for the matched shape, used as the
        aggregation key in deprecation-readiness telemetry.
    deprecation_log_line: human-readable message for the structured
        deprecation log when the shape is legacy. None for the canonical
        shape (no log emitted for the happy path).
    """

    shape_name: str
    deprecation_log_line: str | None


@dataclass(frozen=True)
class Unrecognized:
    """Validator outcome: the envelope carries fields the adapter does
    not route.

    fields: the unrecognized field path(s). For nested-path inputs
        ('metadata.discord.foo.bar.baz' with 'foo' unknown), this is
        the first unknown prefix ('metadata.discord.foo'), not the
        leaves.
    canonical_equivalent: human-readable hint for the sender's failed-
        delivery Acknowledgment note, naming the canonical way to send
        the same intent.
    """

    fields: list[str]
    canonical_equivalent: str


ShapeValidation = Recognized | Unrecognized


def validate(envelope: Envelope) -> ShapeValidation:
    """Validate that the Discord adapter has routing for every field on
    envelope. Returns Recognized(shape_name, deprecation_log_line_or_None)
    or Unrecognized(fields, canonical_equivalent).

    Stub: real implementation lands in Tasks 2-4.
    """
    raise NotImplementedError("validate() stub — implementation in Tasks 2-4")
