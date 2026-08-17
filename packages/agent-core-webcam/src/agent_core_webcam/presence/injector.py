"""The ``PresenceInjector`` hook — reader half of the presence contract.

Runs in-session on each lifecycle event. It never computes presence itself
(no camera, no CV — the hook is invoked fresh every turn and must be instant);
it only reads the state file written out-of-band by the CV watcher, and turns
that reading into per-being guidance via the pure ``levels`` policy.

Safety-additive by construction: a missing, unreadable, stale, or malformed
reading — or any internal error — degrades to an explicit "unknown => be
cautious". The hook never blocks and never raises.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path

from agent_core.models import ToolResult
from agent_core_webcam.presence.levels import (
    DEFAULT_TEMPLATES,
    Instrument,
    classify,
    render,
)
from agent_core_webcam.presence.state import (
    PresenceState,
    WatcherHeartbeat,
    heartbeat_path_for,
    read_heartbeat,
    read_state,
)

log = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path.home() / ".agent-core" / "presence" / "state.json"
_DEFAULT_MAX_AGE_SECONDS = 30.0
_DEFAULT_HEADING = "Presence"
_DEFAULT_LEVEL = 1
_DEFAULT_PRINCIPAL = "jeff"
#: A heartbeat older than this means the loop is not turning. Generous relative
#: to the ~2s watch interval so a slow cycle or a paused box is not reported as
#: death — this decides whether we say "DEAD", and a false death claim is the
#: kind of alarm that trains its reader to ignore the channel.
_DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 120.0


class PresenceInjector:
    """Inject a staleness-guarded, level-appropriate presence tag into context.

    Params (from the ``params:`` block of the yaml registration):
        state_path (str): Path to the presence-state JSON. Default
            ``~/.agent-core/presence/state.json``.
        max_age_seconds (float): Readings older than this degrade to "unknown".
            Default ``30``.
        heading (str): Section heading for the injected context. Default
            ``"Presence"``.
        level (int): Behavioral level — 1 ambient, 2 +shoulder-surf,
            3 +trust-gating. Default ``1``. Cumulative; out-of-range highs just
            mean max caution.
        principal (str): Enrolled identity that counts as "trusted present".
            Default ``"jeff"``.
        templates (dict): Per-being overrides for any of the ``levels`` text
            fragments (``facts``, ``unknown_banner``, ``shoulder_surf``,
            ``shoulder_surf_no_reading``, ``trust_gate``).
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Return the current presence guidance, degrading any failure to "unknown"."""
        del event, hook_input  # presence depends on neither the event nor the prompt
        heading = str(params.get("heading", _DEFAULT_HEADING))
        templates = {**DEFAULT_TEMPLATES, **(params.get("templates") or {})}
        try:
            state_path = Path(params.get("state_path", _DEFAULT_STATE_PATH))
            max_age = float(params.get("max_age_seconds", _DEFAULT_MAX_AGE_SECONDS))
            level = int(params.get("level", _DEFAULT_LEVEL))
            principal = str(params.get("principal", _DEFAULT_PRINCIPAL))

            hb_max_age = float(
                params.get("heartbeat_max_age_seconds", _DEFAULT_HEARTBEAT_MAX_AGE_SECONDS)
            )

            now = time.time()
            state = read_state(state_path)
            heartbeat = read_heartbeat(heartbeat_path_for(state_path))
            instrument, age = _diagnose(now, state, heartbeat, max_age, hb_max_age)

            # A stale reading is still DISCARDED for every gating decision —
            # this change does not soften the guard by one bit. What it stops
            # doing is destroying the evidence on the way: `state` used to be
            # reassigned to None here, so the age and the reason were gone
            # before anything could report them.
            fresh = state if instrument is Instrument.FRESH else None
            reading = classify(fresh, principal=principal)
            # `restarts` is SUPERVISOR restarts, deliberately NOT the heartbeat's
            # `consecutive_failures` (which counts failed camera reads). They are
            # different faults with different repairs, and putting one under the
            # other's name is how a safety line ends up asserting something it
            # never measured.
            reading = replace(
                reading,
                instrument=instrument,
                age_seconds=age,
                restarts=_read_restart_count(state_path),
            )
            content = render(reading, fresh, level=level, templates=templates)
            return ToolResult(heading=heading, content=content)
        except Exception:  # never raise into the session — degrade to cautious
            log.exception("presence_injector failed; degrading to unknown")
            return ToolResult(heading=heading, content=self._fallback(params, templates))

    @staticmethod
    def _fallback(params: dict, templates: dict[str, str]) -> str:
        """Render the level-appropriate no-reading guidance after an internal error.

        The error path must be no *less* cautious than a normal missing reading:
        a level-3 being still gets its trust-gate. ``level`` is re-parsed
        defensively — if even that is unusable, default to maximum caution.
        """
        try:
            level = int(params.get("level", _DEFAULT_LEVEL))
        except (TypeError, ValueError):
            level = 3  # unparseable config => be maximally cautious
        reading = classify(None, principal="")  # None => no reading, cautious
        return render(reading, None, level=level, templates=templates)


def _diagnose(
    now: float,
    state: PresenceState | None,
    heartbeat: WatcherHeartbeat | None,
    max_age: float,
    hb_max_age: float,
) -> tuple[Instrument, float | None]:
    """Decide the instrument state and the age of the last reading.

    The single place where "old reading" is turned into a CAUSE. Kept pure and
    separate from the hook so it is testable without files, a camera, or a
    session — the 2026-08-14 defect lived in three lines tangled into I/O and
    was therefore never unit-tested at all.

    Returns the instrument state and the reading's age in seconds (``None``
    when there has never been a reading).

    Ordering matters and is deliberate:

    1. No state file at all => NEVER. "Never configured" must not be reported
       as a running system that broke.
    2. Reading is fresh => FRESH, regardless of the heartbeat. A good reading is
       a good reading; refusing it because bookkeeping is missing would make the
       fix less useful than what it replaced.
    3. Reading is stale and NO heartbeat exists => UNKNOWN, never DEAD. A state
       file written before heartbeats existed is indistinguishable from a dead
       watcher, and claiming death without evidence is the same class of error
       as the silence this change exists to fix.
    4. Reading is stale, heartbeat is fresh => STALE. Loop turning, camera not.
    5. Reading is stale, heartbeat is stale => DEAD.
    """
    if state is None:
        return Instrument.NEVER, None
    age = now - state.updated_at
    if age <= max_age:
        return Instrument.FRESH, age
    if heartbeat is None:
        return Instrument.UNKNOWN, age
    if (now - heartbeat.beat_at) <= hb_max_age:
        return Instrument.STALE, age
    return Instrument.DEAD, age


def _read_restart_count(state_path: Path) -> int | None:
    """Recent supervisor restarts, or ``None`` when it cannot be determined.

    ``None`` and ``0`` mean genuinely different things here — "nobody is
    counting" versus "counted, and it has not restarted" — so an unreadable or
    absent file must never collapse to zero. A zero would render as "stable",
    which is a claim, and an absent supervisor has not earned it.
    """
    try:
        raw = (state_path.parent / "supervisor.json").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(raw).get("restarts_recent")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, int) else None
