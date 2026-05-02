"""HandoffWriter — enqueue-only handoff job hook.

This hook performs no handoff generation and does not write handoff files.
It only validates minimal inputs and POSTs a job request to the daemon's
handoff-jobs HTTP endpoint. The daemon worker owns status transitions and file
writes.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_core.models import ToolResult

logger = logging.getLogger("agent_core.hooks.tools.handoff_writer")


class HandoffWriter:
    """Enqueue-only hook tool for daemon-managed handoff generation."""

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        """POST a handoff job to daemon and return quickly."""
        output_path_str = params.get("output_path")
        if not output_path_str:
            raise ValueError("Required param 'output_path' is missing")

        output_path = Path(output_path_str)
        vault_root = Path(params.get("vault_root", str(output_path.parent)))
        status_path = Path(params.get("handoff_status_path", str(output_path.parent / "handoff-status.json")))
        enqueue_url = params.get("handoff_jobs_url", "http://127.0.0.1:8788/internal/handoff-jobs")
        agent_name = params.get("agent_name", "Assistant")
        session_id = str(hook_input.get("session_id", "")).strip()
        transcript_path_str = str(hook_input.get("transcript_path", "")).strip()
        if not session_id:
            return ToolResult(
                heading="Handoff Job Enqueue Failed",
                content="Missing session_id in hook payload.",
            )
        if not transcript_path_str:
            return ToolResult(
                heading="Handoff Job Enqueue Failed",
                content="Missing transcript_path in hook payload.",
            )

        payload = {
            "session_id": session_id,
            "event": event,
            "agent_name": agent_name,
            "vault_root": str(vault_root),
            "handoff_path": str(output_path),
            "handoff_status_path": str(status_path),
            "transcript_path": transcript_path_str,
            "requested_at": datetime.now(UTC).isoformat(),
            "idempotency_key": f"{session_id}:{event}:{output_path}",
            "context": {},
        }

        req = urllib.request.Request(
            url=enqueue_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            logger.warning("handoff enqueue rejected: %s", exc)
            return ToolResult(
                heading="Handoff Job Enqueue Failed",
                content=f"Daemon rejected handoff job ({exc.code}).",
            )
        except (urllib.error.URLError, OSError):
            return ToolResult(
                heading="Handoff Job Enqueue Failed",
                content="Cannot reach handoff daemon endpoint.",
            )

        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}

        job_id = parsed.get("job_id", "unknown")
        return ToolResult(
            heading="Handoff Job Enqueued",
            content=f"Enqueued daemon handoff job {job_id} for session {session_id}.",
        )
