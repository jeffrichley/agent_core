"""Tests for the SchedulerEndpoint adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.protocol import Endpoint
from agent_core.endpoints.scheduler import (
    JobDef,
    SchedulerEndpoint,
    build_trigger,
)


def test_endpoint_satisfies_endpoint_protocol():
    ep = SchedulerEndpoint(name="scheduler")
    assert isinstance(ep, Endpoint)


def test_endpoint_exposes_name():
    ep = SchedulerEndpoint(name="scheduler")
    assert ep.name == "scheduler"


def test_endpoint_default_db_path(tmp_path):
    ep = SchedulerEndpoint(name="scheduler")
    # Default expands ~/.agent-core/scheduler.db
    assert str(ep.db_path).endswith("scheduler.db")
    assert ".agent-core" in str(ep.db_path)


def test_endpoint_custom_db_path(tmp_path):
    p = tmp_path / "sched.db"
    ep = SchedulerEndpoint(name="scheduler", db_path=str(p))
    assert ep.db_path == p


def test_endpoint_jobs_path_optional():
    ep = SchedulerEndpoint(name="scheduler")
    assert ep.jobs_path is None


def test_jobdef_validates_required_fields():
    with pytest.raises(Exception):  # pydantic ValidationError
        JobDef(trigger="interval")  # missing target, prompt, schedule


def test_jobdef_accepts_interval_trigger():
    jd = JobDef(
        trigger="interval",
        schedule={"minutes": 5},
        target="agent-test",
        prompt="hi",
    )
    assert jd.trigger == "interval"
    assert jd.schedule == {"minutes": 5}
    assert jd.target == "agent-test"
    assert jd.prompt == "hi"


def test_jobdef_accepts_cron_trigger_with_timezone():
    jd = JobDef(
        trigger="cron",
        schedule={"hour": 7, "minute": 0},
        target="agent-test",
        prompt="morning",
        timezone="US/Eastern",
    )
    assert jd.timezone == "US/Eastern"


def test_jobdef_accepts_date_trigger():
    jd = JobDef(
        trigger="date",
        schedule={"run_time": "2026-05-01T09:00:00-04:00"},
        target="agent-test",
        prompt="reminder",
    )
    assert jd.trigger == "date"


def test_jobdef_metadata_defaults_empty():
    jd = JobDef(
        trigger="interval",
        schedule={"seconds": 30},
        target="agent-test",
        prompt="x",
    )
    assert jd.metadata == {}


def test_jobdef_rejects_unknown_trigger():
    with pytest.raises(Exception):
        JobDef(
            trigger="weekly",  # not interval/cron/date
            schedule={"day": "fri"},
            target="agent-test",
            prompt="x",
        )


def test_build_trigger_interval():
    from apscheduler.triggers.interval import IntervalTrigger

    jd = JobDef(
        trigger="interval",
        schedule={"minutes": 30},
        target="agent-test",
        prompt="x",
    )
    trig = build_trigger(jd)
    assert isinstance(trig, IntervalTrigger)


def test_build_trigger_cron():
    from apscheduler.triggers.cron import CronTrigger

    jd = JobDef(
        trigger="cron",
        schedule={"hour": 9, "minute": 30},
        target="agent-test",
        prompt="x",
        timezone="US/Eastern",
    )
    trig = build_trigger(jd)
    assert isinstance(trig, CronTrigger)


def test_build_trigger_date():
    from apscheduler.triggers.date import DateTrigger

    jd = JobDef(
        trigger="date",
        schedule={"run_time": "2026-05-01T09:00:00-04:00"},
        target="agent-test",
        prompt="x",
    )
    trig = build_trigger(jd)
    assert isinstance(trig, DateTrigger)


@pytest.mark.asyncio
async def test_start_stop_lifecycle(tmp_path):
    """SchedulerEndpoint can start and stop cleanly."""

    class _FakeHandle:
        async def publish(self, *a, **kw): ...
        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    ep = SchedulerEndpoint(
        name="scheduler",
        db_path=str(tmp_path / "sched.db"),
    )
    await ep.start(_FakeHandle())
    await ep.stop()
