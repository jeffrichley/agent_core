"""Presence -> behavioral-guidance policy.

Pure, camera-free: given a presence reading (or its absence) and a being's
configured level, decide which guidance fragments to inject. The security
invariant lives here — the mapping only ever ADDS caution: higher levels are
strict supersets of lower ones, and every uncertain input (no reading, stale,
principal not confirmed) resolves to the cautious side.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_core_webcam.presence.state import PresenceState


class Instrument(Enum):
    """Why there is (or is not) a usable reading — the SENSOR's own state.

    Orthogonal to what was observed. The observed axis answers "who is at the
    desk"; this answers "is the thing that would tell me still working". Before
    2026-08-16 these were one axis, and the result was that a watcher dead for
    56 hours produced output byte-identical to a camera that had merely not
    refreshed in the last 30 seconds. Nobody noticed, because nothing could
    have: the two states were literally the same string.

    Members:
        FRESH: a reading inside the staleness window. The only unlocking state.
        STALE: reading is old, but the watch loop is demonstrably still turning
            (recent heartbeat). The camera is failing; the process is fine.
        DEAD: reading is old AND no recent heartbeat. The watcher is gone and
            will not recover on its own. Loudest state.
        NEVER: no reading has ever been written. Distinct from DEAD because it
            means "never configured", which is a different repair entirely and
            must not be reported as a failure of a running system.
        UNKNOWN: liveness genuinely undeterminable — e.g. a state file from
            before heartbeats existed. Treated as cautiously as DEAD but must
            NOT claim the watcher is dead, because that has not been measured.
    """

    FRESH = "fresh"
    STALE = "stale"
    DEAD = "dead"
    NEVER = "never"
    UNKNOWN = "unknown"

# Injected-text fragments, all overridable per being via the hook's
# ``templates`` param. ``facts`` accepts {at_desk}, {recognized},
# {unknown_count}; the guidance fragments take no format slots.
DEFAULT_TEMPLATES: dict[str, str] = {
    "facts": "At desk: {at_desk}. Recognized: {recognized}. Unknown faces: {unknown_count}.",
    "unknown_banner": "Presence unknown — no current reading from the desk camera.",
    # The instrument banners. Each REPLACES `unknown_banner` and each states the
    # sensor's condition and the AGE of the last reading, because "no reading"
    # with no age attached is exactly what hid a 56-hour outage on 2026-08-14 —
    # it read as "not in the last 30 seconds" every single turn.
    #
    # {age} is a human string ("2d 8h"); {restarts} is a count or "unknown".
    "instrument_stale": (
        "DESK CAMERA FAILING — the watcher is running but has not produced a "
        "usable frame for {age}. Presence below is NOT current. Treat this as "
        "no reading, and note the sensor is degraded rather than merely quiet."
    ),
    "instrument_dead": (
        "DESK CAMERA WATCHER IS DEAD — no reading for {age} and the watch loop "
        "is not running. This is a broken sensor, not a quiet one; it will not "
        "recover on its own and presence cannot be established until it is "
        "restarted. Everything below assumes no reading."
    ),
    # Both of these LEAD with "Presence unknown" and only then explain. An
    # earlier draft opened the NEVER case with "never configured ... not a
    # failure of a running system", which is true and reads as REASSURANCE — at
    # level 1 that sentence is the only thing a being sees, and the one thing it
    # must carry is that nobody knows who is in the room. Explanation is allowed
    # to follow the uncertainty; it may not replace it.
    "instrument_never": (
        "Presence unknown — the desk camera has never produced a reading. "
        "Presence was never configured on this machine, so this is a gap in "
        "setup rather than a running system that broke — but nothing has been "
        "observed either way."
    ),
    "instrument_unknown_liveness": (
        "Presence unknown — no current reading from the desk camera (last: "
        "{age}). Whether the watcher is still running could not be determined, "
        "so this may be a slow sensor or a dead one; the difference has not "
        "been measured."
    ),
    "instrument_restarts": (
        "Note: the watcher has restarted {restarts} time(s) recently — it is "
        "being revived rather than staying up, which a bare 'running' would hide."
    ),
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
        instrument: Why the reading is (un)usable. Never affects how cautious
            the output is — ``have_reading`` alone still drives every gate — it
            only determines what the output is allowed to CLAIM about the
            sensor. Defaulted so existing callers keep working unchanged.
        age_seconds: Age of the last reading, or ``None`` if there has never
            been one. Rendered into the instrument banner so staleness is
            visible in the line rather than inferable only from a file mtime.
        restarts: Recent supervisor restarts, or ``None`` when unknown. A
            watcher revived four times an hour is not the same as one that
            never fell over, and a bare "running" hides the difference.
    """

    have_reading: bool
    principal_present: bool
    unknown_present: bool
    instrument: Instrument = Instrument.UNKNOWN
    age_seconds: float | None = None
    restarts: int | None = None


def humanize_age(seconds: float | None) -> str:
    """Render an age as a short human string (``"2d 8h"``, ``"14m"``, ``"9s"``).

    Returns ``"never"`` for ``None``. Deliberately coarse: the reader needs to
    tell "a moment ago" from "since Friday" at a glance, and false precision in
    a safety line invites arguing with the number instead of acting on it.
    """
    if seconds is None:
        return "never"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


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
        # WITHOUT a reading, say WHY and say HOW OLD. The pre-2026-08-16 code
        # emitted one fixed sentence here for every no-reading cause, so a dead
        # watcher and a 31-second-old reading were the same bytes. The caution
        # is identical across these branches — only the claim differs, which is
        # the same principle as `shoulder_surf_no_reading` one level down.
        age = humanize_age(reading.age_seconds)
        banner = {
            Instrument.STALE: "instrument_stale",
            Instrument.DEAD: "instrument_dead",
            Instrument.NEVER: "instrument_never",
            Instrument.UNKNOWN: "instrument_unknown_liveness",
        }.get(reading.instrument)
        if banner is not None and banner in templates:
            parts.append(templates[banner].format(age=age, restarts=reading.restarts))
        else:  # unrecognized instrument state => the original, always-safe line
            parts.append(templates["unknown_banner"])
    # Restarts are reported whether or not there is a reading: a watcher that is
    # being revived repeatedly is worth knowing about even while it is currently
    # healthy, because "currently fine" is exactly how a flapping process looks
    # at any given instant.
    if reading.restarts:
        parts.append(templates["instrument_restarts"].format(restarts=reading.restarts, age=""))
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
