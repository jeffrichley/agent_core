"""Read + filter daily bus log JSONL files.

The on-disk format is one ``Envelope.model_dump_json(by_alias=True)`` per
line. Reads are tolerant of malformed lines (logged + skipped, never
silently ignored) so a single bad line does not poison the day.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from agent_core.bus.envelope import Envelope

log = logging.getLogger(__name__)


def iter_envelopes(
    path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Envelope]:
    """Yield envelopes from a daily JSONL file.

    Missing files yield nothing (a quiet day, not an error). Blank lines
    are skipped silently. Lines that fail JSON parsing or Envelope
    validation are logged at WARNING and skipped — operator gets a signal
    without losing the rest of the day.

    ``since`` is inclusive, ``until`` is exclusive (matches Python
    range/slice conventions). Both are compared against
    ``envelope.created_at`` after coercing it to UTC.
    """
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.exception("bus_log: failed to read %s", path)
        return
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            env = Envelope.model_validate_json(line)
        except ValidationError as exc:
            log.warning("bus_log: malformed envelope at %s:%d (%s)", path, lineno, exc)
            continue
        except ValueError as exc:
            log.warning("bus_log: parse error at %s:%d (%s)", path, lineno, exc)
            continue
        ts = env.created_at if env.created_at.tzinfo else env.created_at.replace(tzinfo=UTC)
        if since is not None and ts < since:
            continue
        if until is not None and ts >= until:
            continue
        yield env
