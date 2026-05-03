"""Write side of the bus log: small append-only helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_core.bus.envelope import Envelope


def default_log_root() -> Path:
    """Default daemon-owned location for daily JSONL files.

    Both the BusHook (write) and ClaudeCodeMCPEndpoint (read for
    ``show_my_day``) default here, so zero-config setups work.
    """
    return Path.home() / ".agent-core" / "bus" / "raw"


def daily_path(
    log_root: Path,
    *,
    timezone: str = "US/Eastern",
    when: datetime | None = None,
) -> Path:
    """Return ``<log_root>/<YYYY-MM-DD>.jsonl`` for ``when`` in the given
    timezone (defaults to now).

    Date rollover happens at local midnight in the configured timezone —
    a 23:50 ET publish lands in today's file, an 00:10 ET publish
    tomorrow. Operators in different timezones get different rollover
    points, which matches expectations for daily summaries.
    """
    if when is None:
        when = datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    local_date = when.astimezone(ZoneInfo(timezone)).date()
    return log_root / f"{local_date.isoformat()}.jsonl"


def append_envelope_jsonl(path: Path, envelope: Envelope) -> None:
    """Append a single ``Envelope`` to ``path`` as one JSON line.

    Uses ``model_dump_json(by_alias=True)`` so the on-disk shape uses
    ``"from"`` (the alias), matching how envelopes appear elsewhere on
    the wire. Reading is symmetric — ``Envelope.model_validate_json``
    accepts both the alias and the field name because of
    ``populate_by_name=True``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = envelope.model_dump_json(by_alias=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
