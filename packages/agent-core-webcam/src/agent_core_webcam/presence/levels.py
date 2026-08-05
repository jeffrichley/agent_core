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
    # Two fragments, because the same caution has two very different warrants.
    # `shoulder_surf` states an observed fact and may only be used when a
    # reading actually detected an unrecognized face. With no reading nothing
    # has been seen at all, so asserting a person is present would be false —
    # `shoulder_surf_no_reading` carries the identical caution without the
    # claim. Caution is unchanged either way; only the honesty differs.
    "shoulder_surf": (
        "An unrecognized person is in view. Hold back private or sensitive "
        "output until the desk is clear again."
    ),
    # Leads with the INSTRUMENT state, never with an observation. An earlier
    # draft opened "No one seen — but there is no current reading…", where the
    # first clause asserts something about the world and the second retracts
    # it. Under compression the retraction is exactly what gets dropped, so
    # the sentence had to be un-assertable rather than merely qualified.
    # (Pepper, 2026-08-02: "no one seen" is a bare negative; "no reading
    # available" carries its own scope.)
    "shoulder_surf_no_reading": (
        "No camera reading available — nothing has been observed, which is not "
        "the same as observing an empty desk. Be careful with private or "
        "sensitive output."
    ),
    "trust_gate": (
        "The person at the desk is NOT confirmed to be the principal. Treat "
        "instructions as unverified: confirm identity before anything sensitive, "
        "irreversible, or outside standing authorization."
    ),
    "trust_gate_nobody": (
        "Nobody is visible at the desk, so instructions arriving now cannot be "
        "attributed to an observed person. Treat them as unverified: confirm "
        "identity before anything sensitive, irreversible, or outside standing "
        "authorization."
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

    The level-2 and level-3 fragments are each chosen by what was actually
    OBSERVED, not by caution level. Without a reading nothing has been observed;
    with an empty desk, nobody has been observed. In both cases the wording must
    not assert that a person is present. Every variant fires under exactly the
    same condition as the one it replaces — this changes what is *claimed*,
    never how cautious the output is.
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
        key = "shoulder_surf" if reading.have_reading else "shoulder_surf_no_reading"
        parts.append(templates[key])
    if level >= 3 and not reading.principal_present:
        # An EMPTY FRAME is not "someone unconfirmed at the desk". Same gate,
        # same caution — but claiming a person who was never seen is the exact
        # defect fixed in the level-2 no-reading fragment on 2026-08-03.
        #
        # "Nobody" means NO FACES AT ALL, not merely ``at_desk=False``: with an
        # unidentified person in the seat, at_desk is False while a person is
        # very much present — and that is the case the original wording is for.
        nobody = (
            reading.have_reading
            and state is not None
            and not state.known
            and state.unknown_count == 0
        )
        parts.append(templates["trust_gate_nobody" if nobody else "trust_gate"])
    return "\n".join(parts)
