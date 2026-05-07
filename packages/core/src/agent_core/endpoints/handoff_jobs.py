"""HandoffJobsEndpoint — daemon-owned handoff job intake and worker loop.

This endpoint is both:
- a bus Endpoint (for lifecycle start/stop and optional event publishing), and
- an HTTPHost mount that accepts internal handoff jobs over HTTP.

The hook-side caller should enqueue only. All status-file mutations happen here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Router

from agent_core.bus.envelope import Envelope, EventPayload

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle

log = logging.getLogger(__name__)


def _default_transcript_root() -> str:
    # Claude Code stores per-session transcripts under ``~/.claude/projects/``
    # by design — outside any agent vault. Match that by default so the daemon
    # accepts transcript paths from real sessions; explicit override is allowed
    # via the request field for tests or non-Claude-Code callers.
    return str(Path.home() / ".claude" / "projects")


def _read_transcript_tail(
    transcript_path: Path,
    *,
    max_bytes: int = 256 * 1024,
    max_messages: int = 200,
) -> str:
    """Return the tail of a jsonl transcript, line-aligned and capped both ways.

    For files at or below ``max_bytes`` returns the full file. For larger
    files seeks ``max_bytes`` from the end, drops the first (potentially
    partial) line, and additionally caps to the last ``max_messages`` lines
    if the byte tail still exceeds that count.
    """
    if not transcript_path.exists():
        raise FileNotFoundError(f"transcript does not exist: {transcript_path}")

    file_size = transcript_path.stat().st_size
    if file_size == 0:
        return ""

    if file_size <= max_bytes:
        text = transcript_path.read_text(encoding="utf-8")
    else:
        with transcript_path.open("rb") as fh:
            fh.seek(file_size - max_bytes)
            tail_bytes = fh.read()
        # Drop the partial first line (always — even if the seek happened to
        # land on a newline, the next byte starts a new line cleanly).
        nl_index = tail_bytes.find(b"\n")
        if nl_index >= 0:
            tail_bytes = tail_bytes[nl_index + 1:]
        text = tail_bytes.decode("utf-8")

    # Normalize line endings to \n (handles Windows CRLF line endings).
    text = text.replace("\r\n", "\n")

    # Cap by message count (line count) if needed.
    lines = text.split("\n")
    # ``split`` produces a trailing empty string when text ends with newline;
    # treat empty trailing entry as a sentinel rather than a message.
    has_trailing_newline = text.endswith("\n")
    if has_trailing_newline:
        lines = lines[:-1]

    if len(lines) > max_messages:
        lines = lines[-max_messages:]

    result = "\n".join(lines)
    if has_trailing_newline and result:
        result += "\n"
    return result


class HandoffJobRequest(BaseModel):
    session_id: str = Field(min_length=1)
    event: Literal["SessionEnd", "PreCompact"]
    agent_name: str = Field(min_length=1)
    # ``mailbox`` is the bus endpoint name to publish HandoffReady/Failed to.
    # Decoupled from ``agent_name`` (the human identity) so configs can map
    # an agent to a routing endpoint with a different name — e.g. identity
    # "testbot" → endpoint "agent-testbot", or identity "Pepper" → endpoint
    # "pepper". Optional; if omitted, falls back to ``agent_name``.
    mailbox: str | None = None
    vault_root: str = Field(min_length=1)
    handoff_path: str = Field(min_length=1)
    handoff_status_path: str = Field(min_length=1)
    transcript_path: str = Field(min_length=1)
    transcript_root: str = Field(default_factory=_default_transcript_root, min_length=1)
    requested_at: datetime
    idempotency_key: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

    @property
    def routing_target(self) -> str:
        return self.mailbox or self.agent_name


@dataclass(frozen=True)
class _QueuedJob:
    job_id: str
    request: HandoffJobRequest
    idempotency_key: str


class HandoffJobsEndpoint:
    """Internal HTTP endpoint for handoff job enqueue + async execution."""

    def __init__(
        self,
        *,
        name: str,
        mount: str = "/internal/handoff-jobs",
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.25,
        transcript_tail_max_bytes: int = 256 * 1024,
        transcript_tail_max_messages: int = 200,
        jobs_log_dir: str | Path | None = None,
    ):
        self.name = name
        self.mount = mount
        self._max_attempts = max(1, max_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._transcript_tail_max_bytes = max(1, transcript_tail_max_bytes)
        self._transcript_tail_max_messages = max(1, transcript_tail_max_messages)
        if jobs_log_dir is not None:
            self._jobs_log_dir = Path(jobs_log_dir).expanduser()
        else:
            self._jobs_log_dir = Path("~/.agent-core/handoffs/jobs").expanduser()
        self._handle: BusHandle | None = None
        self._jobs: asyncio.Queue[_QueuedJob] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._idempotency_index: dict[str, str] = {}

    # ---- Bus endpoint protocol ----
    async def start(self, bus: BusHandle) -> None:
        self._handle = bus
        self._worker_task = asyncio.create_task(self._worker_loop())
        log.info("HandoffJobsEndpoint(name=%s) started at mount=%s", self.name, self.mount)

    async def deliver(self, envelope: Envelope) -> None:
        """This endpoint currently has no bus inbound contract; auto-ack."""
        if self._handle is None:
            raise RuntimeError(f"endpoint '{self.name}' is not started")
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        self._handle = None
        log.info("HandoffJobsEndpoint(name=%s) stopped", self.name)

    # ---- MCPHostable protocol ----
    def asgi_app(self):
        router = Router(
            routes=[
                Route("/", endpoint=self._post_job, methods=["POST"]),
            ],
            redirect_slashes=False,
        )
        return router

    # ---- HTTP handlers ----
    async def _post_job(self, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        try:
            job_req = HandoffJobRequest.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse({"error": "invalid request", "details": exc.errors()}, status_code=400)

        try:
            vault_root = Path(job_req.vault_root).expanduser().resolve(strict=False)
            transcript_root = Path(job_req.transcript_root).expanduser().resolve(strict=False)
            self._resolve_under_root(job_req.handoff_path, vault_root)
            status_path = self._resolve_under_root(job_req.handoff_status_path, vault_root)
            self._resolve_under_root(
                job_req.transcript_path, transcript_root, root_label="transcript_root"
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)

        idem = job_req.idempotency_key or self._derive_idempotency_key(job_req)
        if idem in self._idempotency_index:
            return JSONResponse(
                {"job_id": self._idempotency_index[idem], "status": "accepted"},
                status_code=202,
            )

        job_id = uuid.uuid4().hex
        self._idempotency_index[idem] = job_id
        await self._jobs.put(_QueuedJob(job_id=job_id, request=job_req, idempotency_key=idem))

        # Daemon owns status file transitions: accepted implies pending.
        self._write_pending(
            status_path=status_path,
            session_id=job_req.session_id,
            job_id=job_id,
        )

        return JSONResponse({"job_id": job_id, "status": "accepted"}, status_code=202)

    # ---- Worker ----
    async def _worker_loop(self) -> None:
        while True:
            job = await self._jobs.get()
            try:
                await self._process_job(job)
            except Exception:
                log.exception("handoff job %s crashed in worker loop", job.job_id)
            finally:
                self._jobs.task_done()

    async def _process_job(self, job: _QueuedJob) -> None:
        req = job.request
        vault_root = Path(req.vault_root).expanduser().resolve(strict=False)
        transcript_root = Path(req.transcript_root).expanduser().resolve(strict=False)
        handoff_path = self._resolve_under_root(req.handoff_path, vault_root)
        status_path = self._resolve_under_root(req.handoff_status_path, vault_root)
        transcript_path = self._resolve_under_root(
            req.transcript_path, transcript_root, root_label="transcript_root"
        )
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                transcript_text = _read_transcript_tail(
                    transcript_path,
                    max_bytes=self._transcript_tail_max_bytes,
                    max_messages=self._transcript_tail_max_messages,
                )
                handoff_content = await self._extract_handoff(req, transcript_text)
                normalized_handoff = handoff_content.rstrip() + "\n"
                self._atomic_write(handoff_path, normalized_handoff)
                sha = hashlib.sha256(normalized_handoff.encode("utf-8")).hexdigest()
                self._write_ready(
                    status_path=status_path,
                    session_id=req.session_id,
                    job_id=job.job_id,
                    content_sha256=sha,
                )
                await self._publish_result(
                    kind="HandoffReady",
                    req=req,
                    job_id=job.job_id,
                    handoff_path=handoff_path,
                    handoff_status_path=status_path,
                    content_sha256=sha,
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                backoff_seconds = self._retry_backoff_seconds * attempt
                if backoff_seconds > 0:
                    await asyncio.sleep(backoff_seconds)

        error_text = str(last_error) if last_error is not None else "unknown handoff processing error"
        self._write_failed(
            status_path=status_path,
            session_id=req.session_id,
            job_id=job.job_id,
            error=error_text,
        )
        await self._publish_result(
            kind="HandoffFailed",
            req=req,
            job_id=job.job_id,
            handoff_path=handoff_path,
            handoff_status_path=status_path,
            error=error_text,
        )

    async def _extract_handoff(self, req: HandoffJobRequest, transcript_text: str) -> str:
        try:
            response_text = await self._call_agent_sdk(req=req, transcript_text=transcript_text)
            if response_text.strip():
                return response_text
            raise RuntimeError("empty handoff extraction")
        except Exception as exc:
            generated_at = datetime.now(UTC).isoformat()
            return (
                f"# Handoff ({req.agent_name})\n\n"
                f"- session_id: {req.session_id}\n"
                f"- event: {req.event}\n"
                f"- generated_at: {generated_at}\n\n"
                f"Extraction fallback used: {exc}"
            )

    async def _call_agent_sdk(self, *, req: HandoffJobRequest, transcript_text: str) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        prompt = f"""You are writing a handoff note for {req.agent_name} for continuity between sessions.
Write from {req.agent_name}'s perspective. Based on the transcript below, produce:

## What We Were Working On
- specific bullets

## Decisions Made
- specific bullets

## Emotional Temperature
- one sentence

## Open Threads
- specific bullets

## Observations
- specific bullets

Rules:
- Keep each section to 2-5 bullets where applicable.
- Skip empty sections.
- Be concrete: mention files, tools, errors, and decisions.
- If truly trivial transcript, respond with exactly HANDOFF_EMPTY.

Session metadata:
- session_id: {req.session_id}
- event: {req.event}

Transcript:
{transcript_text}
"""
        response_text = ""
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[],
                max_turns=2,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

        if response_text.strip() == "HANDOFF_EMPTY":
            return (
                f"# Handoff ({req.agent_name})\n\n"
                f"- session_id: {req.session_id}\n"
                f"- event: {req.event}\n\n"
                "No significant content to hand off."
            )
        return response_text

    async def _publish_result(
        self,
        *,
        kind: Literal["HandoffReady", "HandoffFailed"],
        req: HandoffJobRequest,
        job_id: str,
        handoff_path: Path,
        handoff_status_path: Path,
        content_sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        if self._handle is None:
            log.warning("cannot publish %s for job=%s: endpoint not started", kind, job_id)
            return

        payload_data: dict[str, Any] = {
            "job_id": job_id,
            "session_id": req.session_id,
            "agent_name": req.agent_name,
            "handoff_path": str(handoff_path),
            "handoff_status_path": str(handoff_status_path),
        }
        if content_sha256 is not None:
            payload_data["content_sha256"] = content_sha256
        if error is not None:
            payload_data["error"] = error

        env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            to=req.routing_target,
            kind="Event",
            payload=EventPayload(type=kind, data=payload_data),
            metadata={"handoff_job_id": job_id, "handoff_event": req.event},
            created_at=datetime.now(UTC),
        )
        await self._handle.publish(env)

    # ---- Status + filesystem helpers ----
    @staticmethod
    def _summarize_stderr(text: str, *, max_chars: int = 500) -> str:
        """Return a short summary of stderr text capped at ``max_chars``.

        Uses the first and last non-empty lines, joined with " ... " when the
        full text would exceed the cap. Returns the full text untruncated when
        it already fits.
        """
        if not text:
            return ""
        non_empty = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not non_empty:
            return text[:max_chars]
        if len(text) <= max_chars:
            return text
        first = non_empty[0]
        last = non_empty[-1] if len(non_empty) > 1 else ""
        if not last or first == last:
            return first[:max_chars]
        sep = " ... "
        # Reserve room for separator and last line; truncate first if needed.
        budget = max_chars - len(sep) - len(last)
        if budget <= 0:
            # last line alone is too long; fall back to truncated first line
            return first[:max_chars]
        return f"{first[:budget]}{sep}{last}"

    @staticmethod
    def _capture_subprocess_stderr(exc: BaseException) -> str:
        """Best-effort extraction of subprocess stderr from an exception.

        Walks the ``__cause__`` chain looking for messages that mention stderr
        or subprocess exit. Falls back to ``repr(exc)`` when nothing structured
        is found.
        """
        parts: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            msg = str(current)
            if msg:
                parts.append(msg)
            current = current.__cause__ or current.__context__

        if parts:
            return "\n".join(parts)
        return repr(exc)

    @staticmethod
    def _resolve_under_root(
        path_value: str, root: Path, *, root_label: str = "vault_root"
    ) -> Path:
        p = Path(path_value).expanduser().resolve(strict=False)
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"path escapes {root_label}: {path_value}") from exc
        return p

    @staticmethod
    def _derive_idempotency_key(req: HandoffJobRequest) -> str:
        raw = f"{req.session_id}|{req.event}|{req.handoff_path}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def _write_pending(cls, *, status_path: Path, session_id: str, job_id: str) -> None:
        cls._atomic_write(
            status_path,
            json.dumps(
                {
                    "state": "pending",
                    "session_id": session_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "job_id": job_id,
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
        )

    @classmethod
    def _write_ready(
        cls,
        *,
        status_path: Path,
        session_id: str,
        job_id: str,
        content_sha256: str,
    ) -> None:
        cls._atomic_write(
            status_path,
            json.dumps(
                {
                    "state": "ready",
                    "session_id": session_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "job_id": job_id,
                    "content_sha256": content_sha256,
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
        )

    @classmethod
    def _write_failed(
        cls,
        *,
        status_path: Path,
        session_id: str,
        job_id: str,
        error: str,
    ) -> None:
        cls._atomic_write(
            status_path,
            json.dumps(
                {
                    "state": "failed",
                    "session_id": session_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "job_id": job_id,
                    "error": error,
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
        )

