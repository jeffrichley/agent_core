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

import json
import logging
import uuid
from datetime import datetime, timezone
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

from agent_core.bus.envelope import AcknowledgmentPayload, Envelope, TextMessagePayload
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
        cron_kwargs: dict[str, Any] = {
            "second": job.schedule.get("second"),
            "minute": job.schedule.get("minute"),
            "hour": job.schedule.get("hour"),
            "day": job.schedule.get("day"),
            "month": job.schedule.get("month"),
            "day_of_week": job.schedule.get("day_of_week"),
            "year": job.schedule.get("year"),
        }
        # APScheduler v4 CronTrigger rejects timezone=None — omit when not set.
        if job.timezone is not None:
            cron_kwargs["timezone"] = job.timezone
        return CronTrigger(**cron_kwargs)
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


# Module-level lookup so APScheduler-persisted job args can be pure strings.
# Args passed to add_schedule() are pickled into the SQLAlchemy data store; a
# live BusHandle isn't safely picklable (it holds the running Bus). _fire uses
# this map to find the live endpoint by name at fire time.
_active_endpoints: dict[str, "SchedulerEndpoint"] = {}


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
            _active_endpoints[self.name] = self
            if self.jobs_path is not None:
                await self._seed_jobs()
        except BaseException:
            # Roll back partial init so callers don't see a half-built scheduler.
            _active_endpoints.pop(self.name, None)
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
                args=[self.name, job_name, job.target, job.prompt, job.metadata],
            )
            log.info("Seeded job: %s", job_name)

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
            args=[self.name, args.name, args.target, args.prompt, args.metadata],
        )
        return {"status": "created", "name": args.name}

    async def _update_job(self, args: _UpdateJobArgs) -> dict:
        assert self._scheduler is not None
        try:
            existing = await self._scheduler.get_schedule(args.name)
        except Exception as exc:
            raise _ToolError(f"job '{args.name}' not found") from exc

        # Existing args tuple is (scheduler_name, job_name, target, prompt, metadata).
        cur_args = list(existing.args) if existing.args else [self.name, args.name, "", "", {}]
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
            args=[self.name, args.name, new_target, new_prompt, new_metadata],
        )
        return {"status": "updated", "name": args.name}

    async def _delete_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        # APScheduler v4's remove_schedule silently no-ops on missing ids; check
        # existence first so we can return a meaningful error.
        existing = await self._scheduler.get_schedules()
        if not any(s.id == args.name for s in existing):
            raise _ToolError(f"job '{args.name}' not found")
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
            cur_args = list(s.args) if s.args else [self.name, s.id, "", "", {}]
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

    async def stop(self) -> None:
        _active_endpoints.pop(self.name, None)
        if self._scheduler is not None:
            try:
                await self._scheduler.__aexit__(None, None, None)
            except Exception:
                log.exception("SchedulerEndpoint(%s) error during scheduler shutdown", self.name)
            finally:
                self._scheduler = None
        self._handle = None
        log.info("SchedulerEndpoint(name=%s) stopped", self.name)


# _fire is a module-level coroutine so APScheduler can serialize a reference
# to it across restarts. Args are pickled into the SQLAlchemy data store, so
# they must be picklable and stable across restarts — hence the scheduler
# instance name (a string) instead of a live BusHandle.
async def _fire(
    scheduler_name: str,
    name: str,
    target: str,
    prompt: str,
    metadata: dict | None = None,
) -> None:
    """APScheduler job callable. Publishes a TextMessage envelope to `target`.

    Looks up the live SchedulerEndpoint via _active_endpoints[scheduler_name];
    if missing (endpoint stopped, daemon restarted but scheduler not yet
    re-registered), the fire is a no-op (logs a warning).

    Errors during publish are logged and swallowed: the job stays scheduled,
    and APScheduler's misfire policy decides whether to retry. Bus-side
    delivery failures (target unregistered, mailbox full) propagate as
    publish exceptions and end up here."""
    endpoint = _active_endpoints.get(scheduler_name)
    if endpoint is None:
        log.warning(
            "Job %s fired but scheduler '%s' is not active; dropping",
            name,
            scheduler_name,
        )
        return
    bus_handle = endpoint._handle
    if bus_handle is None:
        log.warning(
            "Job %s fired but scheduler '%s' has no handle; dropping",
            name,
            scheduler_name,
        )
        return

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
