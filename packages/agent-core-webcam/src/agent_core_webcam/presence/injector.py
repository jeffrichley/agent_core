"""The ``PresenceInjector`` hook — reader half of the presence contract.

Runs in-session on each lifecycle event. It never computes presence itself
(no camera, no CV — the hook is invoked fresh every turn and must be instant);
it only reads the state file written out-of-band by the CV watcher.

Fail-safe by construction: a missing, unreadable, or stale reading degrades to
an explicit "unknown". The hook never blocks and never asserts a stale
identity — a crashed watcher simply means "I don't know who's there".
"""

from __future__ import annotations

import time
from pathlib import Path

from agent_core.models import ToolResult
from agent_core_webcam.presence.state import PresenceState, read_state

_DEFAULT_STATE_PATH = Path.home() / ".agent-core" / "presence" / "state.json"
_DEFAULT_MAX_AGE_SECONDS = 30.0
_DEFAULT_HEADING = "Presence"


def _render(state: PresenceState) -> str:
    """Render a fresh reading as a one-line presence tag."""
    at_desk = "yes" if state.at_desk else "no"
    recognized = ", ".join(state.known) if state.known else "nobody enrolled-recognized"
    return f"At desk: {at_desk}. Recognized: {recognized}. Unknown faces: {state.unknown_count}."


class PresenceInjector:
    """Inject a compact, staleness-guarded presence tag into session context.

    Params (from the ``params:`` block of the yaml registration):
        state_path (str): Path to the presence-state JSON file.
            Default: ``~/.agent-core/presence/state.json``.
        max_age_seconds (float): Readings older than this degrade to "unknown".
            Default: ``30``.
        heading (str): Section heading for the injected context. Default:
            ``"Presence"``.
    """

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """Return the current presence tag, or "unknown" when missing or stale."""
        del event, hook_input  # presence depends on neither the event nor the prompt
        state_path = Path(params.get("state_path", _DEFAULT_STATE_PATH))
        max_age = float(params.get("max_age_seconds", _DEFAULT_MAX_AGE_SECONDS))
        heading = str(params.get("heading", _DEFAULT_HEADING))

        state = read_state(state_path)
        if state is None:
            return ToolResult(heading=heading, content="Presence unknown — no reading available.")

        age = time.time() - state.updated_at
        if age > max_age:
            return ToolResult(
                heading=heading,
                content=f"Presence unknown — reading is stale ({int(age)}s old).",
            )
        return ToolResult(heading=heading, content=_render(state))
