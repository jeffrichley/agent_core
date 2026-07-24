"""now — emit current-time facts for use in playbook expressions and sections.

Every brief is time-anchored. Almost every conditional ``when:`` and almost
every dynamic-color expression that gates by date or day-of-week reaches
for facts about *now*. Rather than have each agent ship its own equivalent
fetcher, ``now`` is a built-in.

The framework still does NOT auto-inject a ``now`` namespace; you opt in
via the gather config like any other fetcher (``type: now``, declare a
``namespace: now``). This keeps the gather contract uniform — context
is always exactly what the gather config asked for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class NowFetcher:
    """Emit calendar / clock facts for the gather window's ``when`` time.

    Config:
    - ``timezone`` (str | None): IANA timezone name (e.g., ``"America/New_York"``).
      Defaults to UTC. Bad names raise ``ValueError`` rather than silently
      falling back, so misconfiguration is loud.
    - ``weekly_digest_day`` (str | None): day-of-week name (case-insensitive)
      whose presence sets ``is_weekly_digest_day=True``. Defaults to ``Monday``.

    Output shape (always exactly these keys, deterministic field order
    irrelevant since dict iteration isn't load-bearing here):

    - ``date`` — ``YYYY-MM-DD`` string in the configured timezone.
    - ``iso_datetime`` — full ISO datetime with offset.
    - ``day_of_week`` — full English name, capitalised (``"Monday"``).
    - ``is_monday`` ... ``is_sunday`` — per-day booleans (lowercase keys).
      ``is_friday`` is naturally one of these; the cutover #09 example
      relies on it.
    - ``is_weekend`` — Saturday or Sunday.
    - ``is_weekly_digest_day`` — true on the configured digest day
      (Monday by default).
    - ``iso_week`` — ``YYYY-Www`` ISO week string (e.g., ``"2026-W18"``).
    - ``hour`` (0–23), ``minute`` (0–59) — clock fields, integers.

    The ``when`` argument is the gather window's ``when`` datetime
    supplied by the orchestrator — not ``datetime.now()``. This makes
    backfill / replay / fixed-clock testing trivial: the framework
    decides what "now" means and the fetcher just reports it through
    the configured timezone lens.
    """

    type_id = "now"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict[str, Any], when: datetime) -> dict[str, Any]:
        tz_name = config.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"now: unknown timezone {tz_name!r}") from exc

        if when.tzinfo is None:
            raise ValueError(
                "now: gather window 'when' must be timezone-aware "
                "(orchestrator supplies UTC by default)"
            )
        local = when.astimezone(tz)

        digest_day_raw = config.get("weekly_digest_day", "Monday")
        digest_day = digest_day_raw.lower()
        if digest_day not in _DAY_NAMES:
            raise ValueError(
                f"now: weekly_digest_day must be a day name "
                f"(monday..sunday), got {digest_day_raw!r}"
            )

        weekday_index = local.weekday()  # 0=Monday, 6=Sunday
        day_name = _DAY_NAMES[weekday_index]

        result: dict[str, Any] = {
            "date": local.strftime("%Y-%m-%d"),
            "iso_datetime": local.isoformat(),
            "day_of_week": day_name.capitalize(),
            "is_weekend": weekday_index >= 5,
            "is_weekly_digest_day": day_name == digest_day,
            "iso_week": f"{local.isocalendar().year:04d}-W{local.isocalendar().week:02d}",
            "hour": local.hour,
            "minute": local.minute,
        }
        for name in _DAY_NAMES:
            result[f"is_{name}"] = name == day_name
        return result
