"""TimeInjector — injects the current date and time into session context.

Emits an absolute timestamp on every firing. When ``track_session`` is enabled
(the default for UserPromptSubmit registrations), it also emits relative time
markers — "session started Xm ago", "last user turn Ym ago" — so the model
re-anchors on each turn instead of drifting after the SessionStart injection.

Configuration:
    In agent_core.yaml, register under any lifecycle event:

        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.time_injector.TimeInjector
              params:
                format: "%A, %B %d, %Y %I:%M %p %Z"
          UserPromptSubmit:
            - tool: agent_core.hooks.tools.time_injector.TimeInjector
              params:
                format: "%A, %B %d, %Y %I:%M %p %Z"
                track_session: true

    Supported params:
        format (str): Python strftime format string.
            Default: "%A, %B %d, %Y %I:%M %p %Z"
        track_session (bool): If true, persist per-session start/last-seen
            timestamps to ~/.agent_core/time-state.json and emit relative
            deltas alongside the absolute timestamp. Default: false.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_core.models import ToolResult

_STATE_FILE = Path.home() / ".agent_core" / "time-state.json"
_STATE_TTL_SECONDS = 7 * 24 * 60 * 60  # prune session entries older than 7 days


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # Justified: time-state persistence is best-effort; a failed write just
        # means the next run recomputes from scratch — no correctness impact.
        pass


def _prune(state: dict, now: float) -> dict:
    cutoff = now - _STATE_TTL_SECONDS
    return {sid: entry for sid, entry in state.items() if entry.get("last_seen", 0) >= cutoff}


def _humanize(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if secs == 0 else f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs}h" if hrs else f"{days}d"


class TimeInjector:
    """Injects the current date and time, optionally with session-relative deltas."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        fmt = params.get("format", "%A, %B %d, %Y %I:%M %p %Z")
        track_session = bool(params.get("track_session", False))

        now_dt = datetime.now(UTC).astimezone()
        now_ts = now_dt.timestamp()
        absolute = now_dt.strftime(fmt)

        if not track_session:
            return ToolResult(heading="Current Time", content=absolute)

        session_id = hook_input.get("session_id", "")
        state = _prune(_load_state(), now_ts)
        entry = state.get(session_id) if session_id else None

        lines = [absolute]
        if entry:
            started_at = entry.get("started_at", now_ts)
            last_seen = entry.get("last_seen", started_at)
            lines.append(f"Session started {_humanize(now_ts - started_at)} ago.")
            since_last = now_ts - last_seen
            # Only mention "last turn" on UserPromptSubmit and when meaningful
            if event == "UserPromptSubmit" and since_last >= 1:
                lines.append(f"Last user turn {_humanize(since_last)} ago.")
        else:
            # First sighting of this session (or state was pruned/missing)
            entry = {"started_at": now_ts}

        if session_id:
            entry["last_seen"] = now_ts
            entry.setdefault("started_at", now_ts)
            state[session_id] = entry
            _save_state(state)

        return ToolResult(heading="Current Time", content="\n".join(lines))
