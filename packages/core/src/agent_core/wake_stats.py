"""Issue #70: wake-stats analyzer.

Joins the relay's wake-audit JSONL with the bus's mcp-audit JSONL to
classify each wake's outcome. The 30% rollout gate uses this data to
decide whether to keep inline_mode=full or fall back to inline_mode=preview.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Classification = Literal["replied", "handled", "engaged-with-fetch", "side-action", "ignored"]


@dataclass(frozen=True)
class WakeOutcome:
    wake_id: str
    classification: Classification


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def classify_wakes(
    *,
    wake_audit_path: Path,
    mcp_audit_path: Path,
    window_seconds: int = 300,
) -> list[WakeOutcome]:
    """Read both logs and classify each inline wake by its first follow-up call.

    Wakes with fallback set (empty_queue, render_error, circuit-breaker tripped)
    are excluded — they have no inlined envelopes to evaluate.
    """
    wake_lines = _read_jsonl(wake_audit_path)
    audit_lines = sorted(_read_jsonl(mcp_audit_path), key=lambda x: _parse_iso(x["ts"]))

    outcomes: list[WakeOutcome] = []
    for wake in wake_lines:
        if wake.get("fallback") is not None:
            continue
        envelope_ids = {e["id"] for e in wake.get("envelopes_inlined", [])}
        if not envelope_ids:
            continue
        wake_ts = _parse_iso(wake["ts"])
        window_end = wake_ts.timestamp() + window_seconds

        # Find the first audit line within the window.
        first_call = None
        for line in audit_lines:
            ts = _parse_iso(line["ts"])
            if ts <= wake_ts:
                continue
            if ts.timestamp() > window_end:
                break
            first_call = line
            break

        if first_call is None:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="ignored"))
            continue

        tool = first_call.get("tool")
        args = first_call.get("args", {}) or {}

        if tool == "reply" and args.get("in_reply_to") in envelope_ids:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="replied"))
        elif tool == "handle" and args.get("envelope_id") in envelope_ids:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="handled"))
        elif tool == "peek" and args.get("envelope_id") in envelope_ids:
            # Look ahead for a subsequent reply/handle in the same window.
            outcomes.append(
                WakeOutcome(wake_id=wake["wake_id"], classification="engaged-with-fetch")
            )
        else:
            outcomes.append(WakeOutcome(wake_id=wake["wake_id"], classification="side-action"))

    return outcomes


def summarize(outcomes: list[WakeOutcome]) -> dict:
    """Aggregate outcomes into rates suitable for the rollout gate."""
    counts: Counter[str] = Counter(o.classification for o in outcomes)
    total = len(outcomes)
    no_engagement = counts.get("ignored", 0) + counts.get("side-action", 0)
    return {
        "total": total,
        "counts": dict(counts),
        "no_engagement_rate": (no_engagement / total) if total else 0.0,
    }
