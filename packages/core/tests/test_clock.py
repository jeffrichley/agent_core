"""Tests for the Clock seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_core.clock import Clock, FakeClock, SystemClock


class TestSystemClock:
    def test_now_returns_timezone_aware(self) -> None:
        # Arrange - SystemClock should produce UTC datetimes.
        clock = SystemClock()
        # Act - Call now() twice.
        first = clock.now()
        second = clock.now()
        # Assert - Both are tzaware UTC, and time moves forward (or stays equal).
        assert first.tzinfo is not None
        assert second.tzinfo is not None
        assert second >= first

    def test_satisfies_clock_protocol(self) -> None:
        # Arrange + Act
        clock: Clock = SystemClock()
        # Assert - structural typing check (also runtime via @runtime_checkable).
        assert isinstance(clock, Clock)


class TestFakeClock:
    def test_now_returns_start(self) -> None:
        # Arrange - Pin start to a specific instant.
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        clock = FakeClock(start=start)
        # Act + Assert
        assert clock.now() == start

    def test_advance_moves_clock_forward(self) -> None:
        # Arrange
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        clock = FakeClock(start=start)
        # Act
        clock.advance(7.5)
        # Assert
        assert clock.now() == start + timedelta(seconds=7.5)

    def test_now_is_idempotent_between_advance_calls(self) -> None:
        # Arrange
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        clock = FakeClock(start=start)
        # Act
        first = clock.now()
        second = clock.now()
        # Assert - No drift; deterministic.
        assert first == second

    def test_start_must_be_tzaware(self) -> None:
        # Arrange - Naive datetime should be rejected.
        naive = datetime(2026, 1, 1, 12, 0, 0)
        # Act + Assert
        with pytest.raises(ValueError, match="timezone-aware"):
            FakeClock(start=naive)

    def test_satisfies_clock_protocol(self) -> None:
        # Arrange
        clock: Clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
        # Assert
        assert isinstance(clock, Clock)
