"""Injectable time source.

Production code that needs ``datetime.now()`` should accept a ``Clock``
parameter and call ``clock.now()`` instead of reaching for the wall
clock directly. Tests then pass ``FakeClock`` to get deterministic
timestamps without depending on runner speed.

Default ``Clock`` is ``SystemClock``; passing a ``Clock`` is a kwarg-only
seam so existing call sites keep working unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current time. Implementations must return timezone-aware ``datetime``."""

    def now(self) -> datetime: ...


class SystemClock:
    """Real-time clock — returns ``datetime.now(UTC)`` on every call."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic clock for tests. Time only moves when ``advance()`` is called."""

    def __init__(self, *, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock start must be timezone-aware")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        """Push the clock forward by ``seconds`` (may be fractional)."""
        self._now += timedelta(seconds=seconds)


__all__ = ["Clock", "FakeClock", "SystemClock"]
