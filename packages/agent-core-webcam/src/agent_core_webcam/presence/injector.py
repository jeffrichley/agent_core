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

import logging
import time
from pathlib import Path

from agent_core.models import ToolResult
from agent_core_webcam.presence.levels import DEFAULT_TEMPLATES, classify, render
from agent_core_webcam.presence.state import read_state

log = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path.home() / ".agent-core" / "presence" / "state.json"
_DEFAULT_MAX_AGE_SECONDS = 30.0
_DEFAULT_HEADING = "Presence"
_DEFAULT_LEVEL = 1
_DEFAULT_PRINCIPAL = "jeff"


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
            ``trust_gate``).
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

            state = read_state(state_path)
            if state is not None and (time.time() - state.updated_at) > max_age:
                state = None  # stale => treat as no reading (cautious)
            reading = classify(state, principal=principal)
            content = render(reading, state, level=level, templates=templates)
            return ToolResult(heading=heading, content=content)
        except Exception:  # never raise into the session — degrade to cautious
            log.exception("presence_injector failed; degrading to unknown")
            return ToolResult(heading=heading, content=templates["unknown_banner"])
