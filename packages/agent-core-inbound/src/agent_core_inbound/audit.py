"""JSONL audit log for inbound-notifications router.

One line per classification. Allow lines carry tier + reason +
rule_id; Deny lines carry only the timestamp + source + target.
Deny intentionally does NOT serialize the event body so a denied
inbound leaves no privacy-sensitive trace.
"""
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_core_inbound.types import Allow


def _default_clock() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    """Append-only JSONL writer keyed by absolute path.

    ``clock`` is injectable so tests can pin the timestamp without
    monkey-patching ``datetime.now``. Parent directories are created
    on first write.
    """

    def __init__(
        self,
        *,
        path: Path,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._path = path
        self._clock = clock

    def record_allow(
        self,
        *,
        connector_name: str,
        target_being: str,
        verdict: Allow,
        rule_id: str,
    ) -> None:
        self._write({
            "ts": self._clock().isoformat(),
            "source": connector_name,
            "to": target_being,
            "verdict": "allow",
            "tier": verdict.tier.value,
            "rule_id": rule_id,
            "reason": verdict.reason,
        })

    def record_deny(self, *, connector_name: str, target_being: str) -> None:
        self._write({
            "ts": self._clock().isoformat(),
            "source": connector_name,
            "to": target_being,
            "verdict": "deny",
        })

    def _write(self, entry: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
