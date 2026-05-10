"""SchedulerEndpoint — bus endpoint that fires scheduled jobs as envelopes.

Wraps apscheduler.AsyncScheduler with a SQLAlchemyDataStore-backed SQLite
persistence. Static jobs are loaded from an optional jobs.yaml at start().
Dynamic management uses ToolInvocation envelopes addressed to to=scheduler;
replies are Acknowledgment envelopes.

When a scheduled job fires, the endpoint publishes an envelope to the job's
target endpoint via the bus's BusHandle. The bus auto-stamps from_=scheduler
on every publish.

Each job carries an `envelope_kind` (default `"TextMessage"`, also supports
`"Event"`):

- `TextMessage` jobs publish `TextMessagePayload(text=prompt)` — the legacy
  shape, byte-identical to before Event support was added.
- `Event` jobs publish `EventPayload(type=payload["type"],
  data=payload["data"])` — used by the briefs orchestrator (cutover #09) to
  fire BriefRequest events from cron schedules.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml
from apscheduler import AsyncScheduler
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.ext.asyncio import create_async_engine

from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus.protocol import EndpointUnavailable

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


class SchedulerConfigError(Exception):
    """Raised when seed-jobs YAML or jobs.d fragments are malformed."""


def _validate_envelope_shape(
    envelope_kind: str,
    prompt: str | None,
    payload: dict[str, Any] | None,
) -> None:
    """Shared shape check for JobDef / _CreateJobArgs.

    TextMessage jobs require a non-empty `prompt`. Event jobs require a
    `payload` mapping with both `type` and `data` keys. Anything else raises
    ValueError so the job is rejected at config-load / create time.
    """
    if envelope_kind == "TextMessage":
        if not prompt:
            raise ValueError("envelope_kind='TextMessage' requires a non-empty 'prompt'")
        return
    if envelope_kind == "Event":
        if payload is None:
            raise ValueError("envelope_kind='Event' requires a 'payload' mapping")
        if "type" not in payload or "data" not in payload:
            raise ValueError(
                "envelope_kind='Event' requires payload with both 'type' and 'data' keys"
            )
        # Loud-fail at create/YAML-load time on a non-mapping `data` value.
        # Without this, a misconfigured YAML (e.g., `data: "not a dict"`)
        # would pass validation here and only blow up inside _fire's
        # EventPayload(...) construction — at which point the broad
        # exception-swallow drops the publish silently. "My brief never
        # fires" is the worst failure mode in this framework, so reject
        # at the boundary instead.
        if not isinstance(payload["data"], dict):
            raise ValueError(
                "envelope_kind='Event' payload['data'] must be a mapping (dict), "
                f"got {type(payload['data']).__name__}"
            )
        return
    raise ValueError(f"envelope_kind must be 'TextMessage' or 'Event', got {envelope_kind!r}")


class JobDef(BaseModel):
    """Validated job definition (yaml entry or tool call args)."""

    trigger: Literal["interval", "cron", "date"]
    schedule: dict[str, Any]
    target: str = Field(min_length=1)
    # `prompt` is required for TextMessage jobs (the legacy default) and
    # ignored for Event jobs. Validation runs in the model_validator below.
    prompt: str | None = None
    timezone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    envelope_kind: Literal["TextMessage", "Event"] = "TextMessage"
    # For Event jobs, `payload` is `{"type": str, "data": dict}` and the bus
    # constructs an EventPayload at fire time. None for TextMessage jobs.
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_envelope(self) -> JobDef:
        _validate_envelope_shape(self.envelope_kind, self.prompt, self.payload)
        return self


class _CreateJobArgs(BaseModel):
    name: str = Field(min_length=1)
    trigger: Literal["interval", "cron", "date"]
    schedule: dict[str, Any]
    target: str = Field(min_length=1)
    prompt: str | None = None
    timezone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    envelope_kind: Literal["TextMessage", "Event"] = "TextMessage"
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_envelope(self) -> _CreateJobArgs:
        _validate_envelope_shape(self.envelope_kind, self.prompt, self.payload)
        return self


class _UpdateJobArgs(BaseModel):
    name: str = Field(min_length=1)
    schedule: dict[str, Any] | None = None
    target: str | None = None
    prompt: str | None = None
    timezone: str | None = None
    metadata: dict[str, Any] | None = None
    envelope_kind: Literal["TextMessage", "Event"] | None = None
    payload: dict[str, Any] | None = None


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
    # Conf.d-style merge: every <yaml_path_dir>/jobs.d/*.yaml fragment
    # contributes its top-level job-name keys. Naming collisions are
    # loud errors — caller renames (hatcher prefixes with being name).
    fragments_dir = yaml_path.parent / "jobs.d"
    if fragments_dir.is_dir():
        for fragment_path in sorted(fragments_dir.glob("*.yaml")):
            fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
            if not isinstance(fragment, dict):
                raise SchedulerConfigError(
                    f"jobs.d fragment {fragment_path.name!r}: "
                    f"top-level must be a mapping, got {type(fragment).__name__}"
                )
            for job_name, job_data in fragment.items():
                if job_name in raw:
                    raise SchedulerConfigError(
                        f"jobs.d fragment {fragment_path.name!r}: "
                        f"job name {job_name!r} collides with existing job"
                    )
                raw[job_name] = job_data
    return {name: JobDef(**spec) for name, spec in raw.items()}


# Module-level lookup so APScheduler-persisted job args can be pure strings.
# Args passed to add_schedule() are pickled into the SQLAlchemy data store; a
# live BusHandle isn't safely picklable (it holds the running Bus). _fire uses
# this map to find the live endpoint by name at fire time.
_active_endpoints: dict[str, SchedulerEndpoint] = {}


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
        self._handle: BusHandle | None = None
        self._scheduler: AsyncScheduler | None = None

    async def start(self, bus: BusHandle) -> None:
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
        assert self.jobs_path is not None
        seeds = load_seed_jobs(self.jobs_path)
        for job_name, job in seeds.items():
            if job_name in existing_ids:
                log.debug("Seed job %s already present; skipping", job_name)
                continue
            trig = build_trigger(job)
            schedule_kwargs = _build_schedule_kwargs(
                self.name,
                job_name,
                job.target,
                job.prompt,
                job.metadata,
                job.envelope_kind,
                job.payload,
            )
            await self._scheduler.add_schedule(_fire, trig, id=job_name, **schedule_kwargs)
            log.info("Seeded job: %s", job_name)

    async def deliver(self, envelope: Envelope) -> None:
        """Handle ToolInvocation envelopes; warn on others."""
        if self._handle is None:
            raise EndpointUnavailable(f"scheduler '{self.name}' not started")

        if envelope.kind != "ToolInvocation":
            await self._reply(
                envelope,
                f"warning: unsupported envelope kind '{envelope.kind}'",
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
            await self._reply(envelope, f"error: {exc}")
        except Exception as exc:
            log.exception("scheduler tool '%s' raised", tool)
            await self._reply(envelope, f"error: {exc}")

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
        try:
            jd = JobDef(
                trigger=args.trigger,
                schedule=args.schedule,
                target=args.target,
                prompt=args.prompt,
                timezone=args.timezone,
                metadata=args.metadata,
                envelope_kind=args.envelope_kind,
                payload=args.payload,
            )
        except Exception as exc:
            raise _ToolError(f"invalid job definition: {exc}") from exc
        try:
            trig = build_trigger(jd)
        except Exception as exc:
            raise _ToolError(f"invalid trigger: {exc}") from exc
        schedule_kwargs = _build_schedule_kwargs(
            self.name,
            args.name,
            args.target,
            args.prompt,
            args.metadata,
            args.envelope_kind,
            args.payload,
        )
        await self._scheduler.add_schedule(_fire, trig, id=args.name, **schedule_kwargs)
        return {"status": "created", "name": args.name}

    async def _update_job(self, args: _UpdateJobArgs) -> dict:
        assert self._scheduler is not None
        try:
            existing = await self._scheduler.get_schedule(args.name)
        except Exception as exc:
            raise _ToolError(f"job '{args.name}' not found") from exc

        # Existing args tuple is (scheduler_name, job_name, target, prompt, metadata).
        cur_args = list(existing.args) if existing.args else [self.name, args.name, "", "", {}]
        current_target = str(cur_args[2])
        current_prompt = str(cur_args[3]) if cur_args[3] is not None else None
        current_metadata = (
            cast(dict[str, Any], cur_args[4]) if isinstance(cur_args[4], dict) else {}
        )
        # Envelope-kind / payload live in kwargs (only present when the job was
        # created with envelope_kind="Event"; legacy TextMessage jobs pass none).
        cur_kwargs = dict(existing.kwargs) if existing.kwargs else {}
        current_envelope_kind = cur_kwargs.get("envelope_kind", "TextMessage")
        current_payload = cur_kwargs.get("payload")

        new_target = args.target if args.target is not None else current_target
        new_prompt = args.prompt if args.prompt is not None else current_prompt
        new_metadata = args.metadata if args.metadata is not None else current_metadata
        new_envelope_kind = (
            args.envelope_kind if args.envelope_kind is not None else current_envelope_kind
        )
        new_payload = args.payload if args.payload is not None else current_payload

        # Re-validate the merged shape so an update can't strand the job in an
        # invalid configuration (e.g., switching to Event without a payload).
        try:
            _validate_envelope_shape(new_envelope_kind, new_prompt, new_payload)
        except ValueError as exc:
            raise _ToolError(f"invalid job definition: {exc}") from exc

        # Rebuild trigger if schedule or timezone changed; else reuse existing.
        if args.schedule is not None:
            jd = JobDef(
                trigger=_trigger_kind_of(existing.trigger),
                schedule=args.schedule,
                target=new_target,
                prompt=new_prompt,
                timezone=args.timezone,
                metadata=new_metadata,
                envelope_kind=new_envelope_kind,
                payload=new_payload,
            )
            try:
                new_trig = build_trigger(jd)
            except Exception as exc:
                raise _ToolError(f"invalid trigger: {exc}") from exc
        else:
            new_trig = existing.trigger  # type: ignore[assignment]

        await self._scheduler.remove_schedule(args.name)
        schedule_kwargs = _build_schedule_kwargs(
            self.name,
            args.name,
            new_target,
            new_prompt,
            new_metadata,
            new_envelope_kind,
            new_payload,
        )
        await self._scheduler.add_schedule(_fire, new_trig, id=args.name, **schedule_kwargs)
        return {"status": "updated", "name": args.name}

    async def _delete_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        # APScheduler v4's remove_schedule silently no-ops on missing ids; check
        # existence first so we can return a meaningful error.
        existing = await self._scheduler.get_schedules()
        if not any(s.id == args.name for s in existing):
            raise _ToolError(f"job '{args.name}' not found")
        await self._scheduler.remove_schedule(args.name)
        return {"status": "deleted", "name": args.name}

    async def _list_jobs(self) -> list[dict]:
        assert self._scheduler is not None
        schedules = await self._scheduler.get_schedules()
        out: list[dict] = []
        for s in schedules:
            cur_args = list(s.args) if s.args else [self.name, s.id, "", "", {}]
            cur_kwargs = dict(s.kwargs) if s.kwargs else {}
            out.append(
                {
                    "name": s.id,
                    "trigger": str(s.trigger),
                    "target": cur_args[2],
                    "prompt": cur_args[3],
                    "envelope_kind": cur_kwargs.get("envelope_kind", "TextMessage"),
                    "payload": cur_kwargs.get("payload"),
                    "next_run": s.next_fire_time.isoformat() if s.next_fire_time else None,
                    "paused": getattr(s, "paused", False),
                }
            )
        return out

    async def _pause_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        existing = await self._scheduler.get_schedules()
        if not any(s.id == args.name for s in existing):
            raise _ToolError(f"job '{args.name}' not found")
        await self._scheduler.pause_schedule(args.name)
        return {"status": "paused", "name": args.name}

    async def _resume_job(self, args: _NameOnlyArgs) -> dict:
        assert self._scheduler is not None
        existing = await self._scheduler.get_schedules()
        if not any(s.id == args.name for s in existing):
            raise _ToolError(f"job '{args.name}' not found")
        await self._scheduler.unpause_schedule(args.name, resume_from="now")
        return {"status": "resumed", "name": args.name}

    async def _reply(self, incoming: Envelope, note: str) -> None:
        """Publish an Acknowledgment back to incoming.from_."""
        assert self._handle is not None
        ack = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=incoming.correlation_id,
            in_reply_to=incoming.id,
            to=incoming.from_,
            kind="Acknowledgment",
            payload=AcknowledgmentPayload(of=incoming.id, note=note),
            created_at=datetime.now(UTC),
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


def _build_schedule_kwargs(
    scheduler_name: str,
    job_name: str,
    target: str,
    prompt: str | None,
    metadata: dict[str, Any],
    envelope_kind: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compose the APScheduler `add_schedule(args=..., kwargs=...)` payload.

    For TextMessage jobs (the default), only positional `args` are persisted —
    byte-identical to the pre-Event-support shape, so legacy SQLite stores
    keep working with no migration. Event jobs additionally persist
    `envelope_kind` and `payload` as kwargs; `_fire` reads them at fire time.
    """
    out: dict[str, Any] = {
        "args": [scheduler_name, job_name, target, prompt, metadata],
    }
    if envelope_kind != "TextMessage":
        out["kwargs"] = {"envelope_kind": envelope_kind, "payload": payload}
    return out


# _fire is a module-level coroutine so APScheduler can serialize a reference
# to it across restarts. Args are pickled into the SQLAlchemy data store, so
# they must be picklable and stable across restarts — hence the scheduler
# instance name (a string) instead of a live BusHandle.
async def _fire(
    scheduler_name: str,
    name: str,
    target: str,
    prompt: str | None,
    metadata: dict | None = None,
    *,
    envelope_kind: str = "TextMessage",
    payload: dict[str, Any] | None = None,
) -> None:
    """APScheduler job callable. Publishes an envelope to `target`.

    For `envelope_kind="TextMessage"` (the default and legacy shape), the
    envelope's payload is `TextMessagePayload(text=prompt)`. For
    `envelope_kind="Event"`, the envelope's payload is
    `EventPayload(type=payload["type"], data=payload["data"])`.

    Looks up the live SchedulerEndpoint via _active_endpoints[scheduler_name];
    if missing (endpoint stopped, daemon restarted but scheduler not yet
    re-registered), the fire is a no-op (logs a warning).

    Errors during publish are logged and swallowed: the job stays scheduled,
    and APScheduler's misfire policy decides whether to retry. Bus-side
    delivery failures (target unregistered, mailbox full) propagate as
    publish exceptions and end up here.

    Validation errors (missing payload for Event, unknown envelope_kind) are
    logged and the fire becomes a no-op rather than crashing the worker.
    """
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

    try:
        env_payload, env_kind = _build_envelope_payload(envelope_kind, prompt, payload)
    except (ValueError, ValidationError):
        # Defensive: a misconfigured Event job (e.g., a handcrafted bad row in
        # the SQLite store, or a future Pydantic version that no longer makes
        # ValidationError a ValueError subclass) is logged-and-dropped here
        # rather than crashing the APScheduler worker.
        #
        # Tradeoff: operators discovering "why isn't my brief firing?" will
        # need to grep logs — there is no externally-visible signal beyond
        # this log line. Loud failure at create/YAML-load time (via
        # _validate_envelope_shape) catches the common cases earlier, so
        # this branch is the last-resort safety net for already-persisted
        # bad rows.
        log.exception("Job %s has invalid envelope shape; dropping fire", name)
        return

    env = Envelope(
        id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        to=target,
        kind=env_kind,  # type: ignore[arg-type]
        payload=env_payload,
        metadata=md,
        created_at=datetime.now(UTC),
    )
    try:
        await bus_handle.publish(env)
        log.info("Job %s fired → %s (envelope %s)", name, target, env.id)
    except Exception:
        log.exception("Job %s failed to publish to %s", name, target)


def _build_envelope_payload(
    envelope_kind: str,
    prompt: str | None,
    payload: dict[str, Any] | None,
) -> tuple[TextMessagePayload | EventPayload, str]:
    """Return (payload, kind) for the envelope based on the job's shape."""
    _validate_envelope_shape(envelope_kind, prompt, payload)
    if envelope_kind == "TextMessage":
        # `prompt` is guaranteed non-None by the validator above.
        return TextMessagePayload(text=prompt or ""), "TextMessage"
    # envelope_kind == "Event"
    assert payload is not None  # validator ensured this
    return (
        EventPayload(type=str(payload["type"]), data=dict(payload["data"])),
        "Event",
    )


class _ToolError(Exception):
    """User-error during tool dispatch — produces an Acknowledgment with note."""


def _trigger_kind_of(trigger: Any) -> Literal["interval", "cron", "date"]:
    """Best-effort mapping from APScheduler trigger object → JobDef.trigger str."""
    s = str(trigger).lower()
    if "interval" in s:
        return "interval"
    if "date" in s:
        return "date"
    return "cron"
