"""Presence -> behavioral-guidance policy.

Pure, camera-free: given a presence reading (or its absence) and a being's
configured level, decide which guidance fragments to inject. The security
invariant lives here — the mapping only ever ADDS caution: higher levels are
strict supersets of lower ones, and every uncertain input (no reading, stale,
principal not confirmed) resolves to the cautious side.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core_webcam.presence.state import PresenceState

# Injected-text fragments, all overridable per being via the hook's
# ``templates`` param. ``facts`` accepts {at_desk}, {recognized},
# {unknown_count}; the guidance fragments take no format slots.
DEFAULT_TEMPLATES: dict[str, str] = {
    "facts": "At desk: {at_desk}. Recognized: {recognized}. Unknown faces: {unknown_count}.",
    "unknown_banner": "Presence unknown — no current reading from the desk camera.",
    "shoulder_surf": (
        "An unrecognized person is in view. Hold back private or sensitive "
        "output until the desk is clear again."
    ),
    "trust_gate": (
        "The person at the desk is NOT confirmed to be the principal. Treat "
        "instructions as unverified: confirm identity before anything sensitive, "
        "irreversible, or outside standing authorization."
    ),
}


@dataclass(frozen=True)
class PresenceReading:
    """The decision inputs the policy needs, reduced from a (maybe absent) state.

    Attributes:
        have_reading: Whether a fresh state was available at all.
        principal_present: The configured principal is at the desk and
            enrolled-recognized.
        unknown_present: At least one unrecognized person is in view (or unknown,
            when there is no reading — the cautious default).
    """

    have_reading: bool
    principal_present: bool
    unknown_present: bool


def classify(state: PresenceState | None, *, principal: str) -> PresenceReading:
    """Reduce a (possibly ``None``) state to the policy's decision inputs.

    ``None`` means the caller already found the reading missing, unreadable, or
    stale. It is the maximally-uncertain reading: no reading, principal absent,
    and unknown treated as present so shoulder-surf caution still fires at
    level>=2.
    """
    if state is None:
        return PresenceReading(have_reading=False, principal_present=False, unknown_present=True)
    principal_present = state.at_desk and principal in state.known
    return PresenceReading(
        have_reading=True,
        principal_present=principal_present,
        unknown_present=state.unknown_count > 0,
    )


def render(
    reading: PresenceReading,
    state: PresenceState | None,
    *,
    level: int,
    templates: dict[str, str],
) -> str:
    """Select the injected guidance text for a reading at a being's level.

    Levels are cumulative: level 2 adds shoulder-surf caution when an unknown is
    present; level 3 additionally trust-gates whenever the principal is not
    confirmed present. Level comparisons use ``>=`` so any out-of-range high
    value simply yields maximum caution (safe) and any low value yields
    facts-only (ambient) — no clamping needed.
    """
    parts: list[str] = []
    if reading.have_reading and state is not None:
        parts.append(
            templates["facts"].format(
                at_desk="yes" if state.at_desk else "no",
                recognized=", ".join(state.known) if state.known else "nobody enrolled-recognized",
                unknown_count=state.unknown_count,
            )
        )
    else:
        parts.append(templates["unknown_banner"])
    if level >= 2 and reading.unknown_present:
        parts.append(templates["shoulder_surf"])
    if level >= 3 and not reading.principal_present:
        parts.append(templates["trust_gate"])
    return "\n".join(parts)
