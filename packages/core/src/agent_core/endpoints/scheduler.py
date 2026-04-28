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
