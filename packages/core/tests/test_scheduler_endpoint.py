"""Tests for the SchedulerEndpoint adapter."""

from __future__ import annotations

import json
import textwrap
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

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
    with pytest.raises(ValidationError):
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
    with pytest.raises(ValidationError):
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
        def endpoints(self):
            return []

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
    with pytest.raises(ValidationError):
        load_seed_jobs(p)


@pytest.mark.asyncio
async def test_seed_jobs_are_added_on_start(tmp_path):
    """When jobs_path points at a yaml file, jobs are added at start()."""

    class _FakeHandle:
        async def publish(self, *a, **kw): ...
        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self):
            return []

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
        def endpoints(self):
            return []

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
    def endpoints(self):
        return []


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
        await _fire("sched-test", "heartbeat", "agent-test", "ping", {"job_kind": "heartbeat"})
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
        def endpoints(self):
            return []

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


def _make_envelope(env_id, frm, to, kind, payload, **kwargs):
    """Build an Envelope for tests."""
    from agent_core.bus.envelope import Envelope

    return Envelope(
        id=env_id,
        correlation_id=kwargs.get("correlation_id", uuid.uuid4().hex),
        from_=frm,
        to=to,
        kind=kind,
        payload=payload,
        created_at=datetime.now(UTC),
    )


def _toolcall(tool: str, args: dict) -> dict:
    """Construct a ToolInvocation payload dict."""
    return {"kind": "ToolInvocation", "tool": tool, "args": args}


@pytest.mark.asyncio
async def test_deliver_create_job_publishes_acknowledgment(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope(
            "env-1",
            frm="agent-test",
            to="scheduler",
            kind="ToolInvocation",
            payload=_toolcall(
                "create_job",
                {
                    "name": "weekly",
                    "trigger": "cron",
                    "schedule": {"day_of_week": "fri", "hour": 17},
                    "target": "agent-test",
                    "prompt": "weekly review",
                },
            ),
        )
        await ep.deliver(env)
        # Expect one Acknowledgment published.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert len(acks) == 1
        ack = acks[0]
        assert ack.to == "agent-test"
        assert ack.payload.of == "env-1"
        result = json.loads(ack.payload.note)
        assert result == {"status": "created", "name": "weekly"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_create_job_duplicate_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        args = {
            "name": "j",
            "trigger": "interval",
            "schedule": {"seconds": 60},
            "target": "agent-test",
            "prompt": "x",
        }
        env1 = _make_envelope(
            "e1", "agent-test", "scheduler", "ToolInvocation", _toolcall("create_job", args)
        )
        env2 = _make_envelope(
            "e2", "agent-test", "scheduler", "ToolInvocation", _toolcall("create_job", args)
        )
        await ep.deliver(env1)
        await ep.deliver(env2)
        ack2 = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "e2"]
        assert len(ack2) == 1
        assert "already exists" in ack2[0].payload.note
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_list_jobs_returns_empty_then_one(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        # list when empty
        env = _make_envelope(
            "l1", "agent-test", "scheduler", "ToolInvocation", _toolcall("list_jobs", {})
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l1"][
            0
        ]
        assert json.loads(ack.payload.note) == []

        # create one then list
        await ep.deliver(
            _make_envelope(
                "c1",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall(
                    "create_job",
                    {
                        "name": "j",
                        "trigger": "interval",
                        "schedule": {"seconds": 60},
                        "target": "agent-test",
                        "prompt": "x",
                    },
                ),
            )
        )
        await ep.deliver(
            _make_envelope(
                "l2", "agent-test", "scheduler", "ToolInvocation", _toolcall("list_jobs", {})
            )
        )
        ack2 = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l2"][
            0
        ]
        listed = json.loads(ack2.payload.note)
        assert len(listed) == 1
        assert listed[0]["name"] == "j"
        assert listed[0]["target"] == "agent-test"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_delete_job_then_list_is_empty(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        await ep.deliver(
            _make_envelope(
                "c",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall(
                    "create_job",
                    {
                        "name": "j",
                        "trigger": "interval",
                        "schedule": {"seconds": 60},
                        "target": "agent-test",
                        "prompt": "x",
                    },
                ),
            )
        )
        await ep.deliver(
            _make_envelope(
                "d",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall("delete_job", {"name": "j"}),
            )
        )
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "d"][0]
        assert json.loads(ack.payload.note) == {"status": "deleted", "name": "j"}

        await ep.deliver(
            _make_envelope(
                "l", "agent-test", "scheduler", "ToolInvocation", _toolcall("list_jobs", {})
            )
        )
        ackl = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l"][
            0
        ]
        assert json.loads(ackl.payload.note) == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_delete_unknown_job_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope(
            "d",
            "agent-test",
            "scheduler",
            "ToolInvocation",
            _toolcall("delete_job", {"name": "nope"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "d"][0]
        assert "not found" in ack.payload.note
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_pause_and_resume(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        await ep.deliver(
            _make_envelope(
                "c",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall(
                    "create_job",
                    {
                        "name": "j",
                        "trigger": "interval",
                        "schedule": {"seconds": 60},
                        "target": "agent-test",
                        "prompt": "x",
                    },
                ),
            )
        )
        await ep.deliver(
            _make_envelope(
                "p",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall("pause_job", {"name": "j"}),
            )
        )
        ackp = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "p"][
            0
        ]
        assert json.loads(ackp.payload.note) == {"status": "paused", "name": "j"}

        await ep.deliver(
            _make_envelope(
                "r",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall("resume_job", {"name": "j"}),
            )
        )
        ackr = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "r"][
            0
        ]
        assert json.loads(ackr.payload.note) == {"status": "resumed", "name": "j"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_pause_unknown_job_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope(
            "p",
            "agent-test",
            "scheduler",
            "ToolInvocation",
            _toolcall("pause_job", {"name": "nope"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "p"][0]
        assert "not found" in ack.payload.note
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_resume_unknown_job_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope(
            "r",
            "agent-test",
            "scheduler",
            "ToolInvocation",
            _toolcall("resume_job", {"name": "nope"}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "r"][0]
        assert "not found" in ack.payload.note
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_unknown_tool_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope(
            "u",
            "agent-test",
            "scheduler",
            "ToolInvocation",
            _toolcall("frobnicate", {"x": 1}),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "u"][0]
        assert "unknown tool" in ack.payload.note.lower()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_bad_args_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        # create_job missing required `target`
        env = _make_envelope(
            "b",
            "agent-test",
            "scheduler",
            "ToolInvocation",
            _toolcall(
                "create_job",
                {
                    "name": "j",
                    "trigger": "interval",
                    "schedule": {"seconds": 60},
                    "prompt": "x",
                },
            ),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "b"][0]
        assert "error" in ack.payload.note.lower()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_update_job_changes_prompt(tmp_path):
    """update_job rewrites prompt while preserving target/schedule."""
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        # Create a job.
        await ep.deliver(
            _make_envelope(
                "c",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall(
                    "create_job",
                    {
                        "name": "j",
                        "trigger": "interval",
                        "schedule": {"seconds": 60},
                        "target": "agent-test",
                        "prompt": "old prompt",
                    },
                ),
            )
        )
        # Update only the prompt.
        await ep.deliver(
            _make_envelope(
                "u",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall("update_job", {"name": "j", "prompt": "new prompt"}),
            )
        )
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "u"][0]
        assert json.loads(ack.payload.note) == {"status": "updated", "name": "j"}

        # list_jobs confirms the prompt is updated and target preserved.
        await ep.deliver(
            _make_envelope(
                "l",
                "agent-test",
                "scheduler",
                "ToolInvocation",
                _toolcall("list_jobs", {}),
            )
        )
        ackl = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l"][
            0
        ]
        listed = json.loads(ackl.payload.note)
        assert len(listed) == 1
        assert listed[0]["name"] == "j"
        assert listed[0]["prompt"] == "new prompt"
        assert listed[0]["target"] == "agent-test"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_non_toolinvocation_publishes_warning(tmp_path):
    from agent_core.bus.envelope import TextMessagePayload

    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope(
            "w",
            "agent-test",
            "scheduler",
            "TextMessage",
            TextMessagePayload(text="random"),
        )
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "w"][0]
        assert "warning" in ack.payload.note.lower()
        assert "TextMessage" in ack.payload.note
    finally:
        await ep.stop()
