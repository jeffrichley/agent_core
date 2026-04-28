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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml  # type: ignore[import-untyped]
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


def load_seed_jobs(yaml_path: Path) -> dict[str, JobDef]:
    """Parse a yaml file into validated JobDef entries keyed by name.

    Returns an empty dict if the file does not exist or is empty.
    Raises pydantic ValidationError if any entry is malformed.
    """
    if not yaml_path.exists():
        return {}
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    return {name: JobDef(**spec) for name, spec in raw.items()}


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
        try:
            await self._scheduler.__aenter__()
            # Allow concurrent fires per task (APScheduler defaults to max_running_jobs=1).
            await self._scheduler.configure_task(_fire, max_running_jobs=None)
            await self._scheduler.start_in_background()
            if self.jobs_path is not None:
                await self._seed_jobs()
        except BaseException:
            # Roll back partial init so callers don't see a half-built scheduler.
            try:
                await self._scheduler.__aexit__(None, None, None)
            except Exception:
                log.exception("rollback __aexit__ failed during start()")
            self._scheduler = None
            self._handle = None
            raise
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

    async def deliver(self, envelope: Envelope) -> None:
        # Tool dispatch lands in Task 5. For now, every envelope reaches scheduler
        # is unsupported; ack with a warning Acknowledgment.
        if self._handle is None:
            raise EndpointUnavailable(f"scheduler '{self.name}' not started")
        # Implemented in Task 5.
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        if self._scheduler is not None:
            try:
                await self._scheduler.__aexit__(None, None, None)
            except Exception:
                log.exception(
                    "SchedulerEndpoint(%s) error during scheduler shutdown", self.name
                )
            finally:
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
