# SchedulerEndpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a bus-native `SchedulerEndpoint` that fires scheduled prompts as bus envelopes (no HTTP POST). Static yaml seeds at boot plus dynamic management via `ToolInvocation` envelopes addressed to `to=scheduler`. Pepper's existing scheduler stays untouched.

**Architecture:** New `Endpoint` Protocol implementer in `packages/core/src/agent_core/endpoints/scheduler.py`. Wraps `apscheduler.AsyncScheduler` with `SQLAlchemyDataStore` (aiosqlite-backed) at `~/.agent-core/scheduler.db`. Tool dispatch in `deliver()` switches on `ToolInvocation.payload.tool`; replies via `Acknowledgment` envelopes. Job firing publishes a `TextMessage` envelope to the job's named target endpoint via `bus_handle.publish()`.

**Tech Stack:** Python 3.12+, uv workspace, apscheduler v4, sqlalchemy, aiosqlite, pydantic, pyyaml, pytest, pytest-asyncio.

**Spec:** [`docs/superpowers/specs/2026-04-28-scheduler-endpoint-design.md`](../specs/2026-04-28-scheduler-endpoint-design.md)

---

## File Structure

**Create:**
- `packages/core/src/agent_core/endpoints/scheduler.py` — `SchedulerEndpoint` class, `JobDef` schema, `build_trigger`, `_fire`, tool dispatcher, seed loader (single module, target ~300-400 lines).
- `packages/core/tests/test_scheduler_endpoint.py` — unit tests (Endpoint Protocol, JobDef validation, build_trigger, seed loader, tool dispatch with mocked APScheduler, error paths).
- `packages/core/tests/test_scheduler_integration.py` — integration tests (real APScheduler + Bus + Stub round trip; dynamic create_job flow).
- `packages/core/changelog.d/+scheduler-endpoint.added.md`

**Modify:**
- `packages/core/pyproject.toml` — add `apscheduler>=4.0`, `sqlalchemy>=2.0` deps.

No other files. The endpoint is a leaf addition; runner discovery already handles new endpoint classes via the existing YAML registration mechanism.

---

## Task 1: Pre-flight + branch + dependencies

**Files:**
- Modify: `packages/core/pyproject.toml`

- [ ] **Step 1: Confirm clean working tree, then create the branch**

```bash
git status
git checkout main
git pull origin main
git checkout -b feat/scheduler-endpoint
```

Expected: clean tree on main, branch created.

- [ ] **Step 2: Verify baseline tests pass**

```bash
uv run --no-sync pytest -q
```

Expected: 244 passed / 2 skipped (matching the post-bus-daemon baseline). Record exact numbers; you'll re-check at the end.

- [ ] **Step 3: Add the new dependencies to `packages/core/pyproject.toml`**

Open `packages/core/pyproject.toml`. Find the `dependencies = [...]` block (added by the bus-daemon work — should already include fastmcp, starlette, uvicorn, psutil). Append two new lines:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.29",
    "python-dotenv>=1.0.0",
    "tzdata>=2024.1",
    "pydantic>=2.0",
    "typer>=0.12",
    "rich>=13.0",
    "pyyaml>=6.0",
    "agentmail>=0.4",
    "aiosqlite>=0.20",
    "fastmcp>=2.0",
    "starlette>=0.37",
    "uvicorn>=0.30",
    "psutil>=5.9",
    "apscheduler>=4.0",
    "sqlalchemy>=2.0",
]
```

(APScheduler v4 is the AsyncScheduler API. If `apscheduler>=4.0` doesn't resolve cleanly, try `apscheduler>=4.0.0a5` to match Pepper's reference pin.)

- [ ] **Step 4: Sync and verify imports resolve**

```bash
uv sync
uv run --no-sync python -c "from apscheduler import AsyncScheduler; from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore; from apscheduler.triggers.cron import CronTrigger; from apscheduler.triggers.date import DateTrigger; from apscheduler.triggers.interval import IntervalTrigger; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Re-run baseline tests**

```bash
uv run --no-sync pytest -q
```

Expected: same baseline (244 passed / 2 skipped, no new errors, no test count change).

- [ ] **Step 6: Commit**

```bash
git add packages/core/pyproject.toml uv.lock
git commit -m "build(scheduler): add apscheduler and sqlalchemy deps"
```

---

## Task 2: SchedulerEndpoint scaffolding (Protocol, JobDef schema, lifecycle, build_trigger)

**Files:**
- Create: `packages/core/src/agent_core/endpoints/scheduler.py`
- Create: `packages/core/tests/test_scheduler_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_scheduler_endpoint.py`:

```python
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
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: ImportError for `agent_core.endpoints.scheduler`.

- [ ] **Step 3: Create the SchedulerEndpoint scaffolding**

Create `packages/core/src/agent_core/endpoints/scheduler.py`:

```python
"""SchedulerEndpoint — bus endpoint that fires scheduled prompts as envelopes.

Wraps apscheduler.AsyncScheduler with a SQLAlchemyDataStore-backed SQLite
persistence. Static jobs are loaded from an optional jobs.yaml at start().
Dynamic management uses ToolInvocation envelopes addressed to to=scheduler;
replies are Acknowledgment envelopes.

When a scheduled job fires, the endpoint publishes a TextMessage envelope to
the job's target endpoint via the bus's BusHandle. The bus auto-stamps
from_=scheduler on every publish.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from apscheduler import AsyncScheduler
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import create_async_engine

from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import EndpointUnavailable

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


class JobDef(BaseModel):
    """Validated job definition (yaml entry or tool call args)."""

    trigger: Literal["interval", "cron", "date"]
    schedule: dict[str, Any]
    target: str = Field(min_length=1)
    prompt: str
    timezone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_trigger(job: JobDef) -> IntervalTrigger | CronTrigger | DateTrigger:
    """Build an APScheduler trigger from a JobDef."""
    if job.trigger == "interval":
        return IntervalTrigger(
            seconds=job.schedule.get("seconds", 0),
            minutes=job.schedule.get("minutes", 0),
            hours=job.schedule.get("hours", 0),
            days=job.schedule.get("days", 0),
        )
    if job.trigger == "cron":
        return CronTrigger(
            second=job.schedule.get("second"),
            minute=job.schedule.get("minute"),
            hour=job.schedule.get("hour"),
            day=job.schedule.get("day"),
            month=job.schedule.get("month"),
            day_of_week=job.schedule.get("day_of_week"),
            year=job.schedule.get("year"),
            timezone=job.timezone,
        )
    if job.trigger == "date":
        run_time = job.schedule["run_time"]
        return DateTrigger(run_time=run_time)
    raise ValueError(f"unknown trigger: {job.trigger!r}")


def _default_db_path() -> Path:
    """The default APScheduler datastore path."""
    return Path("~/.agent-core/scheduler.db").expanduser()


class SchedulerEndpoint:
    """Bus endpoint that runs APScheduler and fires jobs as bus envelopes."""

    def __init__(
        self,
        *,
        name: str,
        jobs_path: str | Path | None = None,
        db_path: str | Path | None = None,
    ):
        self.name = name
        self.jobs_path: Path | None = Path(jobs_path).expanduser() if jobs_path else None
        self.db_path: Path = Path(db_path).expanduser() if db_path else _default_db_path()
        self._handle: "BusHandle | None" = None
        self._scheduler: AsyncScheduler | None = None

    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        data_store = SQLAlchemyDataStore(engine)
        self._scheduler = AsyncScheduler(data_store=data_store)
        await self._scheduler.__aenter__()
        # Allow concurrent fires per task (APScheduler defaults to max_running_jobs=1).
        await self._scheduler.configure_task(_fire, max_running_jobs=None)
        await self._scheduler.start_in_background()
        # Seed loading + tool dispatch land in Tasks 3 and 5.
        log.info("SchedulerEndpoint(name=%s) started; db=%s", self.name, self.db_path)

    async def deliver(self, envelope: Envelope) -> None:
        # Tool dispatch lands in Task 5. For now, every envelope reaches scheduler
        # is unsupported; ack with a warning Acknowledgment.
        if self._handle is None:
            raise EndpointUnavailable(f"scheduler '{self.name}' not started")
        # Implemented in Task 5.
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.__aexit__(None, None, None)
            self._scheduler = None
        self._handle = None
        log.info("SchedulerEndpoint(name=%s) stopped", self.name)


# _fire is a module-level coroutine so APScheduler can serialize a reference
# to it across restarts. The bus_handle is closed over via a partial; see the
# `_register_seed` and `create_job` paths for how that's wired up.
async def _fire(
    bus_handle: "BusHandle",
    name: str,
    target: str,
    prompt: str,
    metadata: dict | None = None,
) -> None:
    """APScheduler job callable. Publishes a TextMessage envelope to target.

    Implemented in Task 4. Stubbed here so test_start_stop_lifecycle's
    configure_task(_fire, ...) call has a function to register."""
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Verify import-linter still passes**

```bash
uv run --no-sync lint-imports
```

Expected: 1 contract kept, 0 broken.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/endpoints/scheduler.py packages/core/tests/test_scheduler_endpoint.py
git commit -m "feat(scheduler): scaffold SchedulerEndpoint, JobDef, build_trigger"
```

---

## Task 3: Static seed loader and start()-time seeding

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/scheduler.py`
- Modify: `packages/core/tests/test_scheduler_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_scheduler_endpoint.py`:

```python
import textwrap


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
```

- [ ] **Step 2: Run tests to confirm new tests fail**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: previous 14 still pass; new tests fail (no `load_seed_jobs`, no seeding logic).

- [ ] **Step 3: Add `load_seed_jobs` and seed logic to `scheduler.py`**

Add these to `packages/core/src/agent_core/endpoints/scheduler.py`:

After `build_trigger`, add:

```python
import yaml  # type: ignore[import-untyped]


def load_seed_jobs(yaml_path: Path) -> dict[str, JobDef]:
    """Parse a yaml file into validated JobDef entries keyed by name.

    Returns an empty dict if the file does not exist or is empty.
    Raises pydantic ValidationError if any entry is malformed.
    """
    if not yaml_path.exists():
        return {}
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    return {name: JobDef(**spec) for name, spec in raw.items()}
```

Modify `start()` in `SchedulerEndpoint` to seed jobs after `start_in_background()`:

```python
    async def start(self, bus: "BusHandle") -> None:
        self._handle = bus
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        data_store = SQLAlchemyDataStore(engine)
        self._scheduler = AsyncScheduler(data_store=data_store)
        await self._scheduler.__aenter__()
        await self._scheduler.configure_task(_fire, max_running_jobs=None)
        await self._scheduler.start_in_background()

        # Seed static jobs (skip any whose id is already in the persisted store).
        if self.jobs_path is not None:
            await self._seed_jobs()

        log.info("SchedulerEndpoint(name=%s) started; db=%s", self.name, self.db_path)

    async def _seed_jobs(self) -> None:
        """Add jobs from jobs.yaml. Skip names that already exist in the store."""
        assert self._scheduler is not None
        existing = await self._scheduler.get_schedules()
        existing_ids = {s.id for s in existing}
        seeds = load_seed_jobs(self.jobs_path)  # type: ignore[arg-type]
        for job_name, job in seeds.items():
            if job_name in existing_ids:
                log.debug("Seed job %s already present; skipping", job_name)
                continue
            trig = build_trigger(job)
            await self._scheduler.add_schedule(
                _fire,
                trig,
                id=job_name,
                args=[self._handle, job_name, job.target, job.prompt, job.metadata],
            )
            log.info("Seeded job: %s", job_name)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: all tests pass (14 + 5 = 19).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/scheduler.py packages/core/tests/test_scheduler_endpoint.py
git commit -m "feat(scheduler): static jobs.yaml seed loader, seed at start()"
```

---

## Task 4: `_fire` job runner — publish on schedule

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/scheduler.py`
- Modify: `packages/core/tests/test_scheduler_endpoint.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/test_scheduler_endpoint.py`:

```python
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


@pytest.mark.asyncio
async def test_fire_publishes_text_message_to_target():
    from agent_core.bus.envelope import TextMessagePayload
    from agent_core.endpoints.scheduler import _fire

    handle = _RecordingHandle()
    await _fire(handle, "heartbeat", "agent-test", "ping", {"job_kind": "heartbeat"})
    assert len(handle.published) == 1
    env = handle.published[0]
    assert env.to == "agent-test"
    assert env.kind == "TextMessage"
    assert isinstance(env.payload, TextMessagePayload)
    assert env.payload.text == "ping"
    # scheduler_job is automatically merged into metadata
    assert env.metadata["scheduler_job"] == "heartbeat"
    assert env.metadata["job_kind"] == "heartbeat"


@pytest.mark.asyncio
async def test_fire_handles_metadata_none():
    from agent_core.endpoints.scheduler import _fire

    handle = _RecordingHandle()
    await _fire(handle, "j", "agent-test", "x", None)
    env = handle.published[0]
    assert env.metadata == {"scheduler_job": "j"}


@pytest.mark.asyncio
async def test_fire_swallows_publish_errors():
    """If bus.publish raises, _fire logs and returns; does not bubble."""
    from agent_core.endpoints.scheduler import _fire

    class _FailingHandle:
        async def publish(self, *a, **kw):
            raise RuntimeError("bus dispatch failed")

        async def ack(self, *a, **kw): ...
        async def nack(self, *a, **kw): ...
        def endpoints(self): return []

    # Should not raise. The job stays scheduled; APScheduler's misfire policy
    # handles future runs. Caller (APScheduler internal) sees a clean return.
    await _fire(_FailingHandle(), "j", "agent-test", "x", {})
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -k fire -v
```

Expected: fire tests fail (current `_fire` is a stub `return None`).

- [ ] **Step 3: Implement `_fire`**

Replace the stub `_fire` in `packages/core/src/agent_core/endpoints/scheduler.py` with the real implementation:

```python
import uuid

from agent_core.bus.envelope import Envelope, TextMessagePayload


async def _fire(
    bus_handle: "BusHandle",
    name: str,
    target: str,
    prompt: str,
    metadata: dict | None = None,
) -> None:
    """APScheduler job callable. Publishes a TextMessage envelope to `target`.

    Errors during publish are logged and swallowed: the job stays scheduled,
    and APScheduler's misfire policy decides whether to retry. Bus-side
    delivery failures (target unregistered, mailbox full) propagate as
    publish exceptions and end up here."""
    md = dict(metadata or {})
    md["scheduler_job"] = name
    env = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to=target,
        kind="TextMessage",
        payload=TextMessagePayload(text=prompt),
        metadata=md,
        created_at=datetime.now(timezone.utc),
    )
    try:
        await bus_handle.publish(env)
        log.info("Job %s fired → %s (envelope %s)", name, target, env.id)
    except Exception:
        log.exception("Job %s failed to publish to %s", name, target)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: all tests pass (19 + 3 = 22).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/scheduler.py packages/core/tests/test_scheduler_endpoint.py
git commit -m "feat(scheduler): _fire publishes TextMessage envelope to target"
```

---

## Task 5: Tool dispatcher in `deliver()` — six tools + Acknowledgment replies + non-TI handling

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/scheduler.py`
- Modify: `packages/core/tests/test_scheduler_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_scheduler_endpoint.py`:

```python
import json


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
        created_at=datetime.now(timezone.utc),
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
        env1 = _make_envelope("e1", "agent-test", "scheduler", "ToolInvocation",
                              _toolcall("create_job", args))
        env2 = _make_envelope("e2", "agent-test", "scheduler", "ToolInvocation",
                              _toolcall("create_job", args))
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
        env = _make_envelope("l1", "agent-test", "scheduler", "ToolInvocation",
                             _toolcall("list_jobs", {}))
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l1"][0]
        assert json.loads(ack.payload.note) == []

        # create one then list
        await ep.deliver(_make_envelope("c1", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("create_job", {
                                            "name": "j",
                                            "trigger": "interval",
                                            "schedule": {"seconds": 60},
                                            "target": "agent-test",
                                            "prompt": "x",
                                        })))
        await ep.deliver(_make_envelope("l2", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("list_jobs", {})))
        ack2 = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l2"][0]
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
        await ep.deliver(_make_envelope("c", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("create_job", {
                                            "name": "j",
                                            "trigger": "interval",
                                            "schedule": {"seconds": 60},
                                            "target": "agent-test",
                                            "prompt": "x",
                                        })))
        await ep.deliver(_make_envelope("d", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("delete_job", {"name": "j"})))
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "d"][0]
        assert json.loads(ack.payload.note) == {"status": "deleted", "name": "j"}

        await ep.deliver(_make_envelope("l", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("list_jobs", {})))
        ackl = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "l"][0]
        assert json.loads(ackl.payload.note) == []
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_delete_unknown_job_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope("d", "agent-test", "scheduler", "ToolInvocation",
                             _toolcall("delete_job", {"name": "nope"}))
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
        await ep.deliver(_make_envelope("c", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("create_job", {
                                            "name": "j",
                                            "trigger": "interval",
                                            "schedule": {"seconds": 60},
                                            "target": "agent-test",
                                            "prompt": "x",
                                        })))
        await ep.deliver(_make_envelope("p", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("pause_job", {"name": "j"})))
        ackp = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "p"][0]
        assert json.loads(ackp.payload.note) == {"status": "paused", "name": "j"}

        await ep.deliver(_make_envelope("r", "agent-test", "scheduler", "ToolInvocation",
                                        _toolcall("resume_job", {"name": "j"})))
        ackr = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "r"][0]
        assert json.loads(ackr.payload.note) == {"status": "resumed", "name": "j"}
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_unknown_tool_returns_error(tmp_path):
    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope("u", "agent-test", "scheduler", "ToolInvocation",
                             _toolcall("frobnicate", {"x": 1}))
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
        env = _make_envelope("b", "agent-test", "scheduler", "ToolInvocation",
                             _toolcall("create_job", {
                                 "name": "j",
                                 "trigger": "interval",
                                 "schedule": {"seconds": 60},
                                 "prompt": "x",
                             }))
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "b"][0]
        assert "error" in ack.payload.note.lower()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_deliver_non_toolinvocation_publishes_warning(tmp_path):
    from agent_core.bus.envelope import TextMessagePayload

    handle = _RecordingHandle()
    ep = SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))
    await ep.start(handle)
    try:
        env = _make_envelope("w", "agent-test", "scheduler", "TextMessage",
                             TextMessagePayload(text="random"))
        await ep.deliver(env)
        ack = [e for e in handle.published if e.kind == "Acknowledgment" and e.payload.of == "w"][0]
        assert "warning" in ack.payload.note.lower()
        assert "TextMessage" in ack.payload.note
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: previous tests still pass; new tool-dispatch tests fail (current `deliver()` only acks).

- [ ] **Step 3: Implement the tool dispatcher**

Replace `deliver()` in `SchedulerEndpoint` and add the tool handlers + reply helper. Add these to `packages/core/src/agent_core/endpoints/scheduler.py`:

```python
import json
from agent_core.bus.envelope import AcknowledgmentPayload


# Tool args models (Pydantic) — used to validate and route tool calls.

class _CreateJobArgs(BaseModel):
    name: str = Field(min_length=1)
    trigger: Literal["interval", "cron", "date"]
    schedule: dict[str, Any]
    target: str = Field(min_length=1)
    prompt: str
    timezone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class _UpdateJobArgs(BaseModel):
    name: str = Field(min_length=1)
    schedule: dict[str, Any] | None = None
    target: str | None = None
    prompt: str | None = None
    timezone: str | None = None
    metadata: dict[str, Any] | None = None


class _NameOnlyArgs(BaseModel):
    name: str = Field(min_length=1)


class _NoArgs(BaseModel):
    pass


# ---


class SchedulerEndpoint:
    # ... existing __init__/start/stop preserved ...

    async def deliver(self, envelope: Envelope) -> None:
        """Handle ToolInvocation envelopes; warn on others."""
        if self._handle is None:
            raise EndpointUnavailable(f"scheduler '{self.name}' not started")

        if envelope.kind != "ToolInvocation":
            await self._reply(
                envelope,
                f"warning: unsupported envelope kind '{envelope.kind}'",
                ok=False,
            )
            await self._handle.ack(envelope.id)
            return

        # ToolInvocation
        tool = envelope.payload.tool  # type: ignore[union-attr]
        args = envelope.payload.args  # type: ignore[union-attr]

        try:
            result = await self._dispatch(tool, args)
            await self._reply(envelope, json.dumps(result))
        except _ToolError as exc:
            await self._reply(envelope, f"error: {exc}", ok=False)
        except Exception as exc:
            log.exception("scheduler tool '%s' raised", tool)
            await self._reply(envelope, f"error: {exc}", ok=False)

        await self._handle.ack(envelope.id)

    async def _dispatch(self, tool: str, args: dict) -> Any:
        """Route a tool call to its handler. Raises _ToolError on user errors."""
        if tool == "create_job":
            return await self._create_job(_CreateJobArgs(**args))
        if tool == "update_job":
            return await self._update_job(_UpdateJobArgs(**args))
        if tool == "delete_job":
            return await self._delete_job(_NameOnlyArgs(**args))
        if tool == "list_jobs":
            _NoArgs(**args)  # validate empty
            return await self._list_jobs()
        if tool == "pause_job":
            return await self._pause_job(_NameOnlyArgs(**args))
        if tool == "resume_job":
            return await self._resume_job(_NameOnlyArgs(**args))
        raise _ToolError(f"unknown tool '{tool}'")

    async def _create_job(self, args: _CreateJobArgs) -> dict:
        assert self._scheduler is not None
        existing = await self._scheduler.get_schedules()
        if any(s.id == args.name for s in existing):
            raise _ToolError(f"job '{args.name}' already exists")
        jd = JobDef(
            trigger=args.trigger,
            schedule=args.schedule,
            target=args.target,
            prompt=args.prompt,
            timezone=args.timezone,
            metadata=args.metadata,
        )
        try:
            trig = build_trigger(jd)
        except Exception as exc:
            raise _ToolError(f"invalid trigger: {exc}") from exc
        await self._scheduler.add_schedule(
            _fire,
            trig,
            id=args.name,
            args=[self._handle, args.name, args.target, args.prompt, args.metadata],
        )
        return {"status": "created", "name": args.name}

    async def _update_job(self, args: _UpdateJobArgs) -> dict:
        assert self._scheduler is not None
        try:
            existing = await self._scheduler.get_schedule(args.name)
        except Exception as exc:
            raise _ToolError(f"job '{args.name}' not found") from exc

        # Existing args tuple is (bus_handle, name, target, prompt, metadata).
        cur_args = list(existing.args) if existing.args else [None, args.name, "", "", {}]
        new_target = args.target if args.target is not None else cur_args[2]
        new_prompt = args.prompt if args.prompt is not None else cur_args[3]
        new_metadata = args.metadata if args.metadata is not None else cur_args[4]

        # Rebuild trigger if schedule or timezone changed; else reuse existing.
        if args.schedule is not None:
            jd = JobDef(
                trigger=_trigger_kind_of(existing.trigger),
                schedule=args.schedule,
                target=new_target,
                prompt=new_prompt,
                timezone=args.timezone,
                metadata=new_metadata,
            )
            try:
                new_trig = build_trigger(jd)
            except Exception as exc:
                raise _ToolError(f"invalid trigger: {exc}") from exc
        else:
            new_trig = existing.trigger

        await self._scheduler.remove_schedule(args.name)
        await self._scheduler.add_schedule(
            _fire,
            new_trig,
            id=args.name,
            args=[self._handle, args.name, new_target, new_prompt, new_metadata],
        )
        return {"status": "updated", "name": args.name}

    async def _delete_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        try:
            await self._scheduler.remove_schedule(args.name)
        except Exception as exc:
            raise _ToolError(f"job '{args.name}' not found") from exc
        return {"status": "deleted", "name": args.name}

    async def _list_jobs(self) -> list[dict]:
        assert self._scheduler is not None
        schedules = await self._scheduler.get_schedules()
        out: list[dict] = []
        for s in schedules:
            cur_args = list(s.args) if s.args else [None, s.id, "", "", {}]
            out.append(
                {
                    "name": s.id,
                    "trigger": str(s.trigger),
                    "target": cur_args[2],
                    "prompt": cur_args[3],
                    "next_run": s.next_fire_time.isoformat() if s.next_fire_time else None,
                    "paused": getattr(s, "paused", False),
                }
            )
        return out

    async def _pause_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        try:
            await self._scheduler.pause_schedule(args.name)
        except Exception as exc:
            raise _ToolError(f"job '{args.name}' not found") from exc
        return {"status": "paused", "name": args.name}

    async def _resume_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        try:
            await self._scheduler.unpause_schedule(args.name, resume_from="now")
        except Exception as exc:
            raise _ToolError(f"job '{args.name}' not found") from exc
        return {"status": "resumed", "name": args.name}

    async def _reply(self, incoming: Envelope, note: str, *, ok: bool = True) -> None:
        """Publish an Acknowledgment back to incoming.from_."""
        assert self._handle is not None
        ack = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=incoming.correlation_id,
            in_reply_to=incoming.id,
            to=incoming.from_,
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of=incoming.id, note=note),
            created_at=datetime.now(timezone.utc),
        )
        try:
            await self._handle.publish(ack)
        except Exception:
            log.exception("scheduler failed to publish Acknowledgment for %s", incoming.id)


class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""


def _trigger_kind_of(trigger: Any) -> str:
    """Best-effort mapping from APScheduler trigger object → JobDef.trigger str."""
    s = str(trigger).lower()
    if "interval" in s:
        return "interval"
    if "date" in s:
        return "date"
    return "cron"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_endpoint.py -v
```

Expected: all tests pass (previously 22 + 9 new tool-dispatch tests = 31).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/scheduler.py packages/core/tests/test_scheduler_endpoint.py
git commit -m "feat(scheduler): tool dispatcher (create/update/delete/list/pause/resume) + Acknowledgment replies"
```

---

## Task 6: Integration test (real APScheduler + Bus + Stub round trip)

**Files:**
- Create: `packages/core/tests/test_scheduler_integration.py`

- [ ] **Step 1: Write the integration test**

Create `packages/core/tests/test_scheduler_integration.py`:

```python
"""Integration: bus + SchedulerEndpoint + StubEndpoint, full round trips.

Verifies:

1. A static seed job with a 1-second interval fires and reaches the stub
   within a 3-second window.
2. Dynamic create_job via ToolInvocation lands in the scheduler, fires, and
   reaches the stub.
3. delete_job stops further fires.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import uuid
from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import (
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core.bus.runner import build_bus_from_config


def _config_yaml(tmp_path, jobs_yaml: str | None = None) -> str:
    bus_db = tmp_path / "bus.sqlite"
    sched_db = tmp_path / "sched.db"
    parts = [
        "bus:",
        f"  storage_path: {bus_db}",
        "endpoints:",
        "  - class: agent_core.endpoints.scheduler.SchedulerEndpoint",
        "    name: scheduler",
        "    description: 'scheduler under test'",
        "    params:",
        f"      db_path: {sched_db}",
    ]
    if jobs_yaml is not None:
        parts.append(f"      jobs_path: {jobs_yaml}")
    parts += [
        "  - class: agent_core.endpoints.stub.StubEndpoint",
        "    name: agent-test",
        "    description: 'fake test agent'",
    ]
    return "\n".join(parts) + "\n"


@pytest.mark.asyncio
async def test_seed_job_fires_to_stub(tmp_path):
    """A 1-second interval seed job fires and the stub receives the envelope."""
    jobs_path = tmp_path / "jobs.yaml"
    jobs_path.write_text(
        textwrap.dedent(
            """
            heartbeat:
              trigger: interval
              schedule: { seconds: 1 }
              target: agent-test
              prompt: ping
              metadata: { kind: heartbeat }
            """
        ).strip(),
        encoding="utf-8",
    )

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(_config_yaml(tmp_path, jobs_yaml=str(jobs_path)), encoding="utf-8")

    bus, _ = await build_bus_from_config(cfg)
    await bus.start()
    try:
        stub = bus._endpoints_by_name["agent-test"].endpoint

        # Wait up to 3s for the first fire.
        for _ in range(60):
            if stub.inbox:
                break
            await asyncio.sleep(0.05)

        assert stub.inbox, "scheduler did not fire the seed job within 3s"
        env = stub.inbox[0]
        assert env.kind == "TextMessage"
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "ping"
        assert env.metadata.get("scheduler_job") == "heartbeat"
        assert env.metadata.get("kind") == "heartbeat"
        assert env.from_ == "scheduler"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_dynamic_create_job_via_toolinvocation(tmp_path):
    """Stub sends a create_job ToolInvocation; scheduler creates and fires the job."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(_config_yaml(tmp_path), encoding="utf-8")

    bus, _ = await build_bus_from_config(cfg)
    await bus.start()
    try:
        stub = bus._endpoints_by_name["agent-test"].endpoint

        # Stub publishes a ToolInvocation envelope to scheduler.
        await stub.send(
            to="scheduler",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="create_job",
                args={
                    "name": "spike",
                    "trigger": "interval",
                    "schedule": {"seconds": 1},
                    "target": "agent-test",
                    "prompt": "spike-prompt",
                },
            ),
        )

        # Wait for either an Acknowledgment (job created) or the first fire.
        for _ in range(60):
            if any(e.payload.text == "spike-prompt"
                   if isinstance(e.payload, TextMessagePayload) else False
                   for e in stub.inbox):
                break
            await asyncio.sleep(0.05)

        text_envs = [e for e in stub.inbox
                     if isinstance(e.payload, TextMessagePayload)
                     and e.payload.text == "spike-prompt"]
        assert text_envs, "dynamic job did not fire within 3s"

        # And the Acknowledgment for the create_job call should be in stub's inbox too.
        acks = [e for e in stub.inbox if e.kind == "Acknowledgment"]
        assert acks, "no Acknowledgment received from scheduler"
        ack = acks[0]
        result = json.loads(ack.payload.note)
        assert result == {"status": "created", "name": "spike"}
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_delete_job_stops_fires(tmp_path):
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(_config_yaml(tmp_path), encoding="utf-8")

    bus, _ = await build_bus_from_config(cfg)
    await bus.start()
    try:
        stub = bus._endpoints_by_name["agent-test"].endpoint

        # Create a 1s interval job.
        await stub.send(
            to="scheduler",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="create_job",
                args={
                    "name": "ephemeral",
                    "trigger": "interval",
                    "schedule": {"seconds": 1},
                    "target": "agent-test",
                    "prompt": "transient",
                },
            ),
        )

        # Wait for it to fire at least once.
        for _ in range(60):
            if any(isinstance(e.payload, TextMessagePayload) and e.payload.text == "transient"
                   for e in stub.inbox):
                break
            await asyncio.sleep(0.05)

        fired_count = len([e for e in stub.inbox
                           if isinstance(e.payload, TextMessagePayload)
                           and e.payload.text == "transient"])
        assert fired_count >= 1

        # Delete it.
        await stub.send(
            to="scheduler",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(tool="delete_job", args={"name": "ephemeral"}),
        )

        # Give the delete a moment to take effect.
        await asyncio.sleep(0.5)

        # Wait 2s; no new fires of "transient" should arrive.
        baseline = len([e for e in stub.inbox
                        if isinstance(e.payload, TextMessagePayload)
                        and e.payload.text == "transient"])
        await asyncio.sleep(2.0)
        after = len([e for e in stub.inbox
                     if isinstance(e.payload, TextMessagePayload)
                     and e.payload.text == "transient"])
        assert after == baseline, "scheduler kept firing after delete_job"
    finally:
        await bus.stop()
```

- [ ] **Step 2: Run the integration tests**

```bash
uv run --no-sync pytest packages/core/tests/test_scheduler_integration.py -v
```

Expected: 3 passed. Each test waits up to 3 seconds for a fire, so total runtime ~6-10 seconds.

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_scheduler_integration.py
git commit -m "test(scheduler): integration — seed job fires, dynamic create/delete via bus"
```

---

## Task 7: Changelog fragment + final smoke + push branch + open PR

**Files:**
- Create: `packages/core/changelog.d/+scheduler-endpoint.added.md`

- [ ] **Step 1: Add the changelog fragment**

Create `packages/core/changelog.d/+scheduler-endpoint.added.md`:

```markdown
- `SchedulerEndpoint` adapter — fires scheduled prompts as bus envelopes.
  Static `jobs.yaml` seeds at boot plus dynamic management via
  `ToolInvocation` envelopes addressed to `to=scheduler`. Six tools:
  `create_job`, `update_job`, `delete_job`, `list_jobs`, `pause_job`,
  `resume_job`. Replies via `Acknowledgment` envelopes back to caller.
```

- [ ] **Step 2: Smoke-test the full suite**

```bash
uv run --no-sync pytest -q
```

Expected: full suite passes (244 baseline + ~34 new scheduler tests = ~278 passed / 2 skipped).

- [ ] **Step 3: Smoke-test ruff and import-linter**

```bash
uv run --no-sync ruff check .
uv run --no-sync lint-imports
```

Expected: ruff baseline held (no new errors); 1 contract kept, 0 broken.

- [ ] **Step 4: Verify the endpoint can be loaded by the runner**

```bash
uv run --no-sync python -c "
from agent_core.endpoints.scheduler import SchedulerEndpoint
from agent_core.bus.protocol import Endpoint
ep = SchedulerEndpoint(name='scheduler')
assert isinstance(ep, Endpoint)
print('SchedulerEndpoint registers as Endpoint Protocol: OK')
"
```

Expected: `SchedulerEndpoint registers as Endpoint Protocol: OK`.

- [ ] **Step 5: Commit changelog**

```bash
git add packages/core/changelog.d/+scheduler-endpoint.added.md
git commit -m "docs(scheduler): add changelog fragment"
```

- [ ] **Step 6: Push branch and open PR**

```bash
git push -u origin feat/scheduler-endpoint
gh pr create --title "feat(scheduler): SchedulerEndpoint — bus-native scheduled prompts (sub-project A step 4)" --body "$(cat <<'EOF'
## Summary

Implements sub-project A Step 4 of the agent-core roadmap ([spec](docs/superpowers/specs/2026-04-28-scheduler-endpoint-design.md), [plan](docs/superpowers/plans/2026-04-28-scheduler-endpoint.md)).

- `SchedulerEndpoint` adapter — wraps `apscheduler.AsyncScheduler` with `SQLAlchemyDataStore` (aiosqlite at `~/.agent-core/scheduler.db`).
- Static jobs seeded from optional `jobs.yaml` at boot.
- Dynamic management via `ToolInvocation` envelopes (six tools: `create_job`, `update_job`, `delete_job`, `list_jobs`, `pause_job`, `resume_job`); replies via `Acknowledgment` envelopes.
- When a job fires, scheduler publishes a `TextMessage` envelope to the job's `target` endpoint via the bus's `BusHandle`. No HTTP POST anywhere.
- Pepper's existing scheduler stays untouched per the project rule.

## Test plan

- [x] `uv run --no-sync pytest -q` — full suite passes (was 244; added ~34 scheduler tests).
- [x] `uv run --no-sync ruff check .` — no new errors.
- [x] `uv run --no-sync lint-imports` — 1 contract kept, 0 broken.
- [x] Integration test with real APScheduler + Bus + Stub — seed job fires within 3s; dynamic `create_job` flow works; `delete_job` stops further fires.
- [ ] Manual milestone: deferred (integration test covers the same vertical slice).

EOF
)"
```

- [ ] **Step 7: Bind changelog fragment to PR number after PR opens**

Once `gh pr create` returns the PR number `N`:

```bash
git mv packages/core/changelog.d/+scheduler-endpoint.added.md packages/core/changelog.d/N.added.md
git commit -m "docs(scheduler): bind towncrier fragment to PR #N"
git push
```

---

## Self-Review Checklist (run before handing off)

- **Spec coverage:** Every section of the spec maps to a task —
  Architecture (Tasks 2-5), Job YAML schema (Task 3), ToolInvocation
  contract (Task 5), Error handling (Task 5), Configuration (Task 6
  config yaml), Testing (Tasks 2-6).
- **Placeholder scan:** No "TBD", no "implement later", no "similar to
  task N". `_fire` and `deliver()` are stubbed in their first appearance
  for TDD reasons but every stub has a concrete real implementation in a
  later task.
- **Type consistency:** `JobDef`, `_CreateJobArgs`, `_UpdateJobArgs`,
  `_NameOnlyArgs`, `_NoArgs` are introduced in their first task and
  referenced consistently. `build_trigger`, `load_seed_jobs`, `_fire`
  signatures stable across tasks.
- **No-dead-code rule:** No HTTP POST anywhere; the `bus_handle.publish`
  path replaces Pepper's `httpx.post(channel/message)` outright.
- **Pepper hands-off rule:** No edits under `~/.pepper/` or to Pepper's
  templates or scheduler. The spec references Pepper's source as a
  read-only port-by-recreation reference.
