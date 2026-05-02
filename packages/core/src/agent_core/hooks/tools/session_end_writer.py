"""SessionEndWriter — daily JSONL log + handoff finalization on session end.

On ``SessionEnd``:

1. Appends one JSON object (one line) to ``vault_path / daily_log_dir / YYYY-MM-DD.jsonl``
   with fields matching ``docs/requirements/pepper-requirements.md`` Tool 3.
2. Updates the handoff note: if the current session already has a ``# Handoff Note``
   from HandoffWriter, appends ``## End of Session``. Otherwise delegates to
   ``HandoffWriter`` for a fresh note (same detached ``claude -p`` flow).
3. Maintains ``handoff-status.json`` beside ``handoff.md`` (see
   ``agent_core.hooks.handoff_status``) so ``IdentityInjector`` can avoid treating
   a stale file as authoritative while a background write is in flight.

Configuration (``agent_core.yaml``)::

    pipelines:
      SessionEnd:
        - type: builtin.session_end_writer
          params:
            vault_path: "C:\\\\Users\\\\me\\\\.pepper\\\\Memory"
            daily_log_dir: "daily/raw"
            handoff_file: "pepper/handoff.md"
            transcript_tail_lines: 200
            timezone: "US/Eastern"
            agent_name: "Pepper"

Required params:

    vault_path (str): Root of the vault (Memory directory).

Optional params:

    daily_log_dir (str): Relative directory for daily JSONL files. Default: ``daily/raw``.
    handoff_file (str): Handoff path relative to ``vault_path``. Default: ``pepper/handoff.md``.
    handoff_status_file (str): Optional status sidecar path relative to ``vault_path``.
        Default: ``handoff-status.json`` next to ``handoff.md``.
    transcript_tail_lines (int): Passed through to ``HandoffWriter`` / ``read_transcript``.
    timezone (str): Calendar date for the log filename. Default: ``US/Eastern``.
    agent_name (str): Display name for ``HandoffWriter``. Default: ``Assistant``.
    sender (str): ``sender`` field in the JSONL row. Default: same as ``agent_name``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_core.hooks import handoff_status as hs
from agent_core.models import ToolResult
from agent_core.transcript import read_transcript

_SESSION_END_MARKER_PREFIX = "<!-- agent-core:session-end:"


def _session_end_marker(session_id: str) -> str:
    return f"{_SESSION_END_MARKER_PREFIX}{session_id} -->"


def _handoff_has_session_note(text: str, session_id: str) -> bool:
    if "# Handoff Note" not in text:
        return False
    return f"**Session:** {session_id}" in text


def _already_finalized(text: str, session_id: str) -> bool:
    return _session_end_marker(session_id) in text


def _read_handoff(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8")


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("US/Eastern")


def _daily_log_path(vault: Path, daily_log_dir: str, tz_name: str) -> Path:
    day = datetime.now(UTC).astimezone(_zone(tz_name)).date()
    return vault / Path(daily_log_dir) / f"{day.isoformat()}.jsonl"


def _duration_hint(transcript_path: Path) -> str | None:
    """Best-effort span from numeric timestamps on JSONL rows (if any)."""
    if not transcript_path.exists():
        return None
    values: list[float] = []
    with open(transcript_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("timestamp", "created_at", "ts"):
                if key not in entry:
                    continue
                val = entry[key]
                if isinstance(val, (int, float)):
                    ts = float(val)
                    if ts > 1e12:
                        ts /= 1000.0
                    values.append(ts)
                elif isinstance(val, str):
                    try:
                        raw = val.replace("Z", "+00:00")
                        values.append(datetime.fromisoformat(raw).timestamp())
                    except ValueError:
                        continue
                break
    if len(values) < 2:
        return None
    delta = max(values) - min(values)
    if delta <= 0:
        return None
    mins = int(delta // 60)
    if mins < 120:
        return f"{mins}m"
    hrs = mins // 60
    rem = mins % 60
    return f"{hrs}h {rem}m"


def _topics_hint(transcript_path: Path, tail_lines: int) -> str:
    ctx, n = read_transcript(transcript_path, max_turns=tail_lines)
    if not ctx.strip() or n == 0:
        return "No conversation text extracted."
    flat = re.sub(r"\s+", " ", ctx).strip()
    if len(flat) > 220:
        flat = flat[:217] + "..."
    return flat


def _append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


class SessionEndWriter:
    """Append session-end JSONL and update or create the handoff note."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        _ = event
        vault_raw = params.get("vault_path")
        if not vault_raw:
            raise ValueError("Required param 'vault_path' is missing")

        vault = Path(vault_raw)
        daily_log_dir = params.get("daily_log_dir", "daily/raw")
        handoff_rel = params.get("handoff_file", "pepper/handoff.md")
        tail_lines = int(params.get("transcript_tail_lines", 200))
        tz_name = params.get("timezone", "US/Eastern")
        agent_name = params.get("agent_name", "Assistant")
        sender = params.get("sender", agent_name)

        session_id = hook_input.get("session_id", "unknown")
        transcript_path_str = hook_input.get("transcript_path", "")
        transcript_path = Path(transcript_path_str) if transcript_path_str else Path()

        handoff_path = vault / Path(handoff_rel)
        status_path = (
            vault / Path(params.get("handoff_status_file"))
            if params.get("handoff_status_file")
            else hs.path_for_handoff(handoff_path)
        )

        tz = _zone(tz_name)
        ts_iso = datetime.now(UTC).astimezone(tz).isoformat()

        duration = _duration_hint(transcript_path)
        topics = (
            _topics_hint(transcript_path, tail_lines)
            if transcript_path_str and transcript_path.exists()
            else "Transcript unavailable."
        )
        duration_clause = f" Duration: {duration}." if duration else ""
        content_line = f"Session ended.{duration_clause} Topics: {topics}"

        record = {
            "ts": ts_iso,
            "dir": "system",
            "src": "session-end",
            "cid": f"session-{session_id}",
            "sender": sender,
            "content": content_line,
        }

        daily_file = _daily_log_path(vault, daily_log_dir, tz_name)
        _append_jsonl_record(daily_file, record)

        note = _read_handoff(handoff_path)

        if _already_finalized(note, session_id):
            hs.write_ready(status_path, session_id, note if note else "")
        else:
            hs.write_pending(status_path, session_id)

        end_block = (
            "\n\n## End of Session\n\n"
            f"**When:** {ts_iso}\n"
            f"**Session:** {session_id}\n\n"
            "Session end marker recorded in the daily log.\n\n"
            f"{_session_end_marker(session_id)}\n"
        )

        if not _already_finalized(note, session_id):
            if _handoff_has_session_note(note, session_id):
                handoff_path.parent.mkdir(parents=True, exist_ok=True)
                handoff_path.write_text(note.rstrip() + end_block, encoding="utf-8")
                hs.write_ready(status_path, session_id, _read_handoff(handoff_path))
            else:
                from agent_core.hooks.tools.handoff_writer import HandoffWriter

                handoff_path.parent.mkdir(parents=True, exist_ok=True)
                hw_params: dict = {
                    "output_path": str(handoff_path),
                    "transcript_tail_lines": tail_lines,
                    "timezone": tz_name,
                    "agent_name": agent_name,
                    "handoff_status_path": str(status_path),
                }
                hw_result = HandoffWriter().execute(
                    "SessionEnd",
                    hook_input,
                    hw_params,
                )
                hw_lower = hw_result.content.lower()
                if "enqueued" in hw_lower or "spawned" in hw_lower or "background" in hw_lower:
                    pass  # daemon owns ready/failed; keep pending from above
                elif "already written" in hw_lower:
                    note2 = _read_handoff(handoff_path)
                    if _handoff_has_session_note(note2, session_id) and not _already_finalized(
                        note2, session_id
                    ):
                        handoff_path.write_text(note2.rstrip() + end_block, encoding="utf-8")
                        hs.write_ready(status_path, session_id, _read_handoff(handoff_path))
                    elif note2.strip():
                        hs.write_ready(status_path, session_id, note2)
                    else:
                        hs.write_failed(
                            status_path,
                            session_id,
                            hw_result.content.strip()[:500] or "HandoffWriter dedupe without file",
                        )
                else:
                    hs.write_failed(
                        status_path,
                        session_id,
                        hw_result.content.strip()[:500] or "HandoffWriter did not start",
                    )

        return ToolResult(
            heading="Session End Captured",
            content="Session summary appended to daily log. Handoff note updated.",
        )
