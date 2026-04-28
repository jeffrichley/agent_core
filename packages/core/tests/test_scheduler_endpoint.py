"""Tests for the SchedulerEndpoint adapter."""

from __future__ import annotations

import textwrap

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


def test_load_seed_jobs_returns_empty_for_missing_file(tmp_path):
    from agent_core.endpoints.scheduler import load_seed_jobs

    missing = tmp_path / "nope.yaml"
    assert load_seed_jobs(missing) == {}


def test_load_seed_jobs_returns_empty_for_empty_file(tmp_path):
    from agent_core.endpoints.scheduler import load_seed_jobs

    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert load_seed_jobs(p) == {}


def test_load_seed_jobs_parses_yaml(tmp_path):
    from agent_core.endpoints.scheduler import load_seed_jobs

    p = tmp_path / "jobs.yaml"
    p.write_text(
        textwrap.dedent(
            """
            heartbeat:
              trigger: interval
              schedule: { minutes: 30 }
              target: agent-pepper
              prompt: ping
            morning:
              trigger: cron
              schedule: { hour: 7, minute: 0 }
              timezone: US/Eastern
              target: agent-pepper
              prompt: brief
            """
        ).strip(),
        encoding="utf-8",
    )
    jobs = load_seed_jobs(p)
    assert set(jobs.keys()) == {"heartbeat", "morning"}
    assert jobs["heartbeat"].trigger == "interval"
    assert jobs["heartbeat"].target == "agent-pepper"
    assert jobs["morning"].trigger == "cron"
    assert jobs["morning"].timezone == "US/Eastern"


def test_load_seed_jobs_raises_on_malformed_entry(tmp_path):
    from agent_core.endpoints.scheduler import load_seed_jobs

    p = tmp_path / "bad.yaml"
    p.write_text(
        textwrap.dedent(
            """
            broken:
              trigger: interval
              schedule: { minutes: 5 }
              # missing target and prompt
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        load_seed_jobs(p)


@pytest.mark.asyncio
async def test_seed_jobs_are_added_on_start(tmp_path):
    """When jobs_path points at a yaml file, jobs are added at start()."""

    class _FakeHandle:
        async def publish(self, *a, **kw): ...
        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    yaml_path = tmp_path / "jobs.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """
            heartbeat:
              trigger: interval
              schedule: { seconds: 60 }
              target: agent-test
              prompt: ping
            """
        ).strip(),
        encoding="utf-8",
    )

    ep = SchedulerEndpoint(
        name="scheduler",
        jobs_path=str(yaml_path),
        db_path=str(tmp_path / "sched.db"),
    )
    await ep.start(_FakeHandle())
    try:
        # Inspect APScheduler's registered schedules. The exact API is
        # scheduler.get_schedules() returning a list of Schedule objects with
        # an `id` attribute matching the job name we registered.
        schedules = await ep._scheduler.get_schedules()
        ids = {s.id for s in schedules}
        assert "heartbeat" in ids
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_seed_jobs_skip_duplicates(tmp_path):
    """Re-running start() with the same yaml does not duplicate jobs."""

    class _FakeHandle:
        async def publish(self, *a, **kw): ...
        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    yaml_path = tmp_path / "jobs.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """
            heartbeat:
              trigger: interval
              schedule: { seconds: 60 }
              target: agent-test
              prompt: ping
            """
        ).strip(),
        encoding="utf-8",
    )
    db_path = str(tmp_path / "sched.db")

    ep1 = SchedulerEndpoint(name="scheduler", jobs_path=str(yaml_path), db_path=db_path)
    await ep1.start(_FakeHandle())
    schedules1 = await ep1._scheduler.get_schedules()
    await ep1.stop()
    assert len([s for s in schedules1 if s.id == "heartbeat"]) == 1

    # Restart with same db: heartbeat persists; seeding must not duplicate.
    ep2 = SchedulerEndpoint(name="scheduler", jobs_path=str(yaml_path), db_path=db_path)
    await ep2.start(_FakeHandle())
    try:
        schedules2 = await ep2._scheduler.get_schedules()
        assert len([s for s in schedules2 if s.id == "heartbeat"]) == 1
    finally:
        await ep2.stop()


class _RecordingHandle:
    """Test-double BusHandle that records publishes."""

    def __init__(self):
        self.published: list = []

    async def publish(self, envelope, to=None) -> None:
        if to is not None:
            envelope = envelope.model_copy(update={"to": to if isinstance(to, str) else to[0]})
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self): return []


class _StubEndpointForFire:
    """Minimal stand-in for SchedulerEndpoint that holds a _handle."""

    def __init__(self, handle):
        self._handle = handle


@pytest.mark.asyncio
async def test_fire_publishes_text_message_to_target():
    from agent_core.bus.envelope import TextMessagePayload
    from agent_core.endpoints.scheduler import _active_endpoints, _fire

    handle = _RecordingHandle()
    _active_endpoints["sched-test"] = _StubEndpointForFire(handle)
    try:
        await _fire(
            "sched-test", "heartbeat", "agent-test", "ping", {"job_kind": "heartbeat"}
        )
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.to == "agent-test"
        assert env.kind == "TextMessage"
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "ping"
        # scheduler_job is automatically merged into metadata
        assert env.metadata["scheduler_job"] == "heartbeat"
        assert env.metadata["job_kind"] == "heartbeat"
    finally:
        _active_endpoints.pop("sched-test", None)


@pytest.mark.asyncio
async def test_fire_handles_metadata_none():
    from agent_core.endpoints.scheduler import _active_endpoints, _fire

    handle = _RecordingHandle()
    _active_endpoints["sched-test"] = _StubEndpointForFire(handle)
    try:
        await _fire("sched-test", "j", "agent-test", "x", None)
        env = handle.published[0]
        assert env.metadata == {"scheduler_job": "j"}
    finally:
        _active_endpoints.pop("sched-test", None)


@pytest.mark.asyncio
async def test_fire_swallows_publish_errors():
    """If bus.publish raises, _fire logs and returns; does not bubble."""
    from agent_core.endpoints.scheduler import _active_endpoints, _fire

    class _FailingHandle:
        async def publish(self, *a, **kw):
            raise RuntimeError("bus dispatch failed")

        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    _active_endpoints["sched-test"] = _StubEndpointForFire(_FailingHandle())
    try:
        # Should not raise. Bus-side delivery failures are logged + swallowed
        # so APScheduler's misfire policy handles future runs.
        await _fire("sched-test", "j", "agent-test", "x", {})
    finally:
        _active_endpoints.pop("sched-test", None)


@pytest.mark.asyncio
async def test_fire_no_active_endpoint_is_noop():
    """If the named scheduler isn't in _active_endpoints (e.g., stopped), _fire
    logs a warning and returns. No exception, no crash."""
    from agent_core.endpoints.scheduler import _fire

    # Don't register anything in _active_endpoints.
    await _fire("not-running", "j", "agent-test", "x", None)
    # No assertion needed — passing without raising is the contract.
