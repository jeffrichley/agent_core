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
from agent_core.bus_log.projectors import get_projector

log = logging.getLogger(__name__)


def iter_envelopes(
    path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Envelope]:
    """Yield envelopes from a daily JSONL file.

    Missing files yield nothing (a quiet day, not an error). Blank lines
    are skipped silently. Lines that fail Envelope validation (including
    invalid JSON syntax — ``ValidationError`` subclasses ``ValueError``
    so it covers both shapes) are logged at WARNING and skipped — operator
    gets a signal without losing the rest of the day.

    ``since`` is inclusive, ``until`` is exclusive (matches Python
    range/slice conventions). Both bounds and ``envelope.created_at`` are
    coerced to UTC before comparison so naive datetimes from any side
    don't trigger ``TypeError``.
    """
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.exception("bus_log: failed to read %s", path)
        return

    def _to_utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    since_utc = _to_utc(since) if since is not None else None
    until_utc = _to_utc(until) if until is not None else None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            env = Envelope.model_validate_json(line)
        except ValidationError as exc:
            log.warning("bus_log: malformed envelope at %s:%d (%s)", path, lineno, exc)
            continue
        ts = _to_utc(env.created_at)
        if since_utc is not None and ts < since_utc:
            continue
        if until_utc is not None and ts >= until_utc:
            continue
        yield env


def iter_for_agent(
    path: Path,
    *,
    agent: str,
    projected: bool = True,
    timezone: str = "US/Eastern",
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Envelope | dict]:
    """Yield envelopes from ``path`` that touch ``agent`` (to or from_).

    With ``projected=True`` (default), yield Tool 3 rows produced by
    registered projectors; envelopes whose projector returns None are
    skipped. With ``projected=False``, yield raw ``Envelope`` instances.

    ``timezone`` is forwarded to projectors for ``ts`` rendering and
    is ignored when ``projected=False``.
    """
    for env in iter_envelopes(path, since=since, until=until):
        if env.to != agent and env.from_ != agent:
            continue
        if not projected:
            yield env
            continue
        projector = get_projector(env)
        row = projector.render(env, perspective=agent, timezone=timezone)
        if row is not None:
            yield row
