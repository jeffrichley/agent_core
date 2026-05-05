from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from agent_core_briefs.fetchers.now import NowFetcher


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.mark.asyncio
async def test_friday_in_utc():
    # 2026-05-08 is a Friday.
    when = _utc(2026, 5, 8, 12, 0)
    result = await NowFetcher().fetch({}, when)
    assert result["date"] == "2026-05-08"
    assert result["day_of_week"] == "Friday"
    assert result["is_friday"] is True
    assert result["is_monday"] is False
    assert result["is_weekend"] is False


@pytest.mark.asyncio
async def test_monday_is_weekly_digest_day_by_default():
    # 2026-05-04 is a Monday.
    when = _utc(2026, 5, 4)
    result = await NowFetcher().fetch({}, when)
    assert result["is_monday"] is True
    assert result["is_weekly_digest_day"] is True
    assert result["is_friday"] is False


@pytest.mark.asyncio
async def test_weekend_flags():
    saturday = _utc(2026, 5, 9)
    result = await NowFetcher().fetch({}, saturday)
    assert result["is_saturday"] is True
    assert result["is_weekend"] is True
    assert result["is_weekly_digest_day"] is False

    sunday = _utc(2026, 5, 10)
    result = await NowFetcher().fetch({}, sunday)
    assert result["is_sunday"] is True
    assert result["is_weekend"] is True


@pytest.mark.asyncio
async def test_clock_fields():
    when = _utc(2026, 5, 4, 7, 30)
    result = await NowFetcher().fetch({}, when)
    assert result["hour"] == 7
    assert result["minute"] == 30


@pytest.mark.asyncio
async def test_iso_week():
    when = _utc(2026, 5, 4)  # Week 19 of 2026
    result = await NowFetcher().fetch({}, when)
    assert result["iso_week"] == "2026-W19"


@pytest.mark.asyncio
async def test_timezone_shifts_day():
    # 2026-05-04 03:00 UTC is still 2026-05-03 23:00 in America/New_York (EDT).
    when = datetime(2026, 5, 4, 3, 0, tzinfo=UTC)
    result = await NowFetcher().fetch({"timezone": "America/New_York"}, when)
    assert result["date"] == "2026-05-03"
    assert result["day_of_week"] == "Sunday"
    assert result["is_sunday"] is True
    assert result["hour"] == 23


@pytest.mark.asyncio
async def test_timezone_aware_input_in_non_utc():
    # A wall-clock-aware datetime in EDT should match its own date even
    # when the fetcher reports back in the same zone.
    eastern = ZoneInfo("America/New_York")
    when = datetime(2026, 5, 4, 9, 0, tzinfo=eastern)
    result = await NowFetcher().fetch({"timezone": "America/New_York"}, when)
    assert result["date"] == "2026-05-04"
    assert result["hour"] == 9


@pytest.mark.asyncio
async def test_weekly_digest_day_override():
    # Sunday is the digest day for some agents.
    when = _utc(2026, 5, 10)  # Sunday
    result = await NowFetcher().fetch({"weekly_digest_day": "sunday"}, when)
    assert result["is_weekly_digest_day"] is True

    # Monday should now be False under that override.
    when = _utc(2026, 5, 4)
    result = await NowFetcher().fetch({"weekly_digest_day": "Sunday"}, when)
    assert result["is_weekly_digest_day"] is False


@pytest.mark.asyncio
async def test_unknown_timezone_raises():
    when = _utc(2026, 5, 4)
    with pytest.raises(ValueError, match="unknown timezone"):
        await NowFetcher().fetch({"timezone": "Mars/Olympus_Mons"}, when)


@pytest.mark.asyncio
async def test_unknown_weekly_digest_day_raises():
    when = _utc(2026, 5, 4)
    with pytest.raises(ValueError, match="weekly_digest_day"):
        await NowFetcher().fetch({"weekly_digest_day": "blursday"}, when)


@pytest.mark.asyncio
async def test_naive_when_raises():
    when = datetime(2026, 5, 4, 12, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        await NowFetcher().fetch({}, when)


@pytest.mark.asyncio
async def test_all_weekday_booleans_present_and_one_true():
    when = _utc(2026, 5, 6)  # Wednesday
    result = await NowFetcher().fetch({}, when)
    flags = {k: v for k, v in result.items() if k.startswith("is_") and k.endswith("day")}
    # is_monday..is_sunday + is_weekly_digest_day = 8 keys
    day_only = {k: v for k, v in flags.items() if k != "is_weekly_digest_day"}
    assert len(day_only) == 7
    assert sum(1 for v in day_only.values() if v) == 1
    assert result["is_wednesday"] is True


@pytest.mark.asyncio
async def test_type_id_and_namespace():
    f = NowFetcher()
    assert f.type_id == "now"
    assert f.namespace == ""  # set per-invocation via gather config
