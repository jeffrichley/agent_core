"""Issue #70: wake-stats analyzer joins relay wake-audit + bus mcp_audit
and classifies each wake's outcome."""

from __future__ import annotations

import json
from pathlib import Path

from agent_core.wake_stats import WakeOutcome, classify_wakes, summarize


def _wake_line(wake_id: str, ts: str, env_ids: list[str], fallback: str | None = None) -> dict:
    return {
        "ts": ts,
        "agent": "pepper",
        "wake_id": wake_id,
        "mode": "full",
        "envelopes_inlined": [{"id": eid, "kind": "TextMessage", "from": "discord",
                                "urgency": "green", "bytes": 50} for eid in env_ids],
        "queue_total_count": len(env_ids),
        "fallback": fallback,
    }


def _audit_line(ts: str, tool: str, args: dict) -> dict:
    return {
        "ts": ts,
        "agent": "pepper",
        "session_id": "s-1",
        "tool": tool,
        "args": args,
    }


def test_classify_replied(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-1", "2026-05-09T00:00:00+00:00", ["e-1"])) + "\n")
    audit.write_text(json.dumps(_audit_line(
        "2026-05-09T00:00:30+00:00", "reply", {"in_reply_to": "e-1"}
    )) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert len(outcomes) == 1
    assert outcomes[0].classification == "replied"
    assert outcomes[0].wake_id == "w-1"


def test_classify_handled(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-h", "2026-05-09T00:00:00+00:00", ["e-h"])) + "\n")
    audit.write_text(json.dumps(_audit_line(
        "2026-05-09T00:00:10+00:00", "handle", {"envelope_id": "e-h"}
    )) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "handled"


def test_classify_engaged_with_fetch(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-p", "2026-05-09T00:00:00+00:00", ["e-p"])) + "\n")
    lines = [
        _audit_line("2026-05-09T00:00:05+00:00", "peek", {"envelope_id": "e-p"}),
        _audit_line("2026-05-09T00:00:15+00:00", "reply", {"in_reply_to": "e-p"}),
    ]
    audit.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "engaged-with-fetch"


def test_classify_side_action(tmp_path: Path) -> None:
    """Next call is `send` to a different recipient — Pepper's
    cross-channel 2-call case."""
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-s", "2026-05-09T00:00:00+00:00", ["e-s"])) + "\n")
    audit.write_text(json.dumps(_audit_line(
        "2026-05-09T00:00:30+00:00", "send", {"to": "slack", "kind": "TextMessage"}
    )) + "\n")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "side-action"


def test_classify_ignored(tmp_path: Path) -> None:
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(json.dumps(_wake_line("w-i", "2026-05-09T00:00:00+00:00", ["e-i"])) + "\n")
    # No following audit lines.
    audit.write_text("")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    assert outcomes[0].classification == "ignored"


def test_classify_skip_fallback_wakes(tmp_path: Path) -> None:
    """Wakes with fallback set are not 'inline' wakes — exclude from stats."""
    wake = tmp_path / "wake.jsonl"
    audit = tmp_path / "audit.jsonl"
    wake.write_text(
        json.dumps(_wake_line("w-fb", "2026-05-09T00:00:00+00:00", [], fallback="empty_queue")) + "\n"
    )
    audit.write_text("")

    outcomes = classify_wakes(wake_audit_path=wake, mcp_audit_path=audit, window_seconds=300)
    # Empty-queue wakes are excluded — no envelopes_inlined to evaluate.
    assert outcomes == []


def test_summarize_rates(tmp_path: Path) -> None:
    outcomes = [
        WakeOutcome(wake_id="a", classification="replied"),
        WakeOutcome(wake_id="b", classification="replied"),
        WakeOutcome(wake_id="c", classification="ignored"),
        WakeOutcome(wake_id="d", classification="side-action"),
    ]
    summary = summarize(outcomes)
    assert summary["total"] == 4
    assert summary["counts"]["replied"] == 2
    assert summary["counts"]["ignored"] == 1
    assert summary["counts"]["side-action"] == 1
    # The 30% rollout-gate metric: ignored + side-action vs total.
    no_engagement_rate = (1 + 1) / 4
    assert abs(summary["no_engagement_rate"] - no_engagement_rate) < 1e-9
