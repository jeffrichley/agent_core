"""Scenario 5: scheduler create + list + delete round-trip.

Why v0.3.0 needs it: scheduler powers agent_core's daemon heartbeat,
liveness-probe, auth-health-probe, and nightly-reflection infrastructure.
If the scheduler endpoint is broken, the daemon goes silent on all timed
operations.

Transport choice (Preflight Step 0 finding):
    SchedulerEndpoint is NOT a ClaudeCodeMCPEndpoint — it is a plain bus
    endpoint whose ``deliver()`` method handles ``ToolInvocation`` envelopes
    and publishes ``Acknowledgment`` envelopes back to the sender. It has
    NO HTTP/MCP surface of its own. Route (b) — ``ToolInvocation`` envelopes
    — is required.

    1. ``send_envelope`` calls the ``send`` MCP tool on the ``qa``
       ClaudeCodeMCPEndpoint (at ``/mcp/qa/``). The bus stamps
       ``from_="qa"`` and dispatches the envelope to ``scheduler``
       in-process (single-event-loop, synchronous delivery).
    2. The scheduler dispatches the named tool, builds a result, and
       publishes an ``Acknowledgment`` envelope back to ``"qa"``.
    3. The ``qa`` ClaudeCodeMCPEndpoint receives the Ack in ``deliver()``.
       If the Ack is a *routine green Ack* (urgency=green, note not
       prefixed "error:", ``in_reply_to`` set and matching ``payload.of``),
       it is auto-cleared in persistence and NEVER queued — so
       ``poll_envelopes`` cannot find it.
    4. Error Acks (note prefixed "error:") are NOT auto-cleared and DO
       land in the ``qa`` inbox where ``poll_envelopes`` can find them.

    This auto-clear asymmetry drives the test strategy below.

Acknowledgment auto-clear mechanics:
    ``ClaudeCodeMCPEndpoint._is_routine_green_ack()`` silently discards
    Acks that match the "routine green" shape — this is the scheduler's
    normal success-path. Success Acks therefore cannot be read via
    ``list_pending``. The test verifies success by **absence of error**:

    - If ``create_job`` succeeds, no error Ack appears in the ``qa`` inbox.
    - Proof that the job actually exists: a duplicate ``create_job`` call
      returns an error Ack with note "error: job 'X' already exists".
    - If ``delete_job`` succeeds, no error Ack appears.
    - Proof that the job is gone: a second ``delete_job`` call returns an
      error Ack with note "error: job 'X' not found".

    This pattern is robust to the auto-clear design without requiring
    ``wake_on_all_acknowledgments`` in the test daemon's ``qa`` endpoint
    config.

Tool names discovered from packages/core/src/agent_core/endpoints/scheduler.py:
    ``_dispatch()`` routes:
        "create_job"  → _CreateJobArgs(name, trigger, schedule, target, prompt)
        "list_jobs"   → _NoArgs() (empty dict)
        "delete_job"  → _NameOnlyArgs(name)

    Success notes (JSON-serialized into Acknowledgment.payload.note):
        create_job → ``{"status": "created", "name": ...}``
        delete_job → ``{"status": "deleted", "name": ...}``
        list_jobs  → JSON array of job dicts
    Error notes: ``"error: ..."`` plain string (not JSON).

Scheduler endpoint name in the test daemon: ``"scheduler"`` (conventional;
confirmed from test suite in packages/core/tests/test_scheduler_endpoint.py
and builtin plugin alias mapping in packages/core/src/agent_core/plugins/).

Date trigger selected for the probe job: ``"date"`` with
``run_time=<one year from now>`` — guaranteed not to fire during the test,
so no side effects from the job existing while the test runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta


def _toolinvocation_payload(tool: str, args: dict) -> dict:
    """Build the payload dict for a ToolInvocation envelope."""
    return {"kind": "ToolInvocation", "tool": tool, "args": args}


def _is_error_ack_for(env_id: str):
    """Return a poll predicate matching an error Acknowledgment for ``env_id``.

    Error Acks (note prefixed "error:") are NOT auto-cleared by the
    ``qa`` ClaudeCodeMCPEndpoint and land in the inbox. Success Acks
    ARE auto-cleared and never appear. This predicate matches only the
    error case; no match within the poll window means success.
    """

    def _predicate(e: dict) -> bool:
        if e.get("kind") != "Acknowledgment":
            return False
        if e.get("in_reply_to") != env_id:
            return False
        note = e.get("payload", {}).get("note", "") or ""
        return note.startswith("error:")

    return _predicate


async def test_scheduler_create_and_delete_roundtrip(client):
    """Create a far-future job; verify creation; delete; verify deletion.

    Uses ``ToolInvocation`` envelopes addressed to the ``scheduler`` endpoint.
    Success is confirmed by absence of error Acks + deliberate duplicate /
    re-delete probes that return error Acks only when the prior operation
    completed correctly.

    The test SKIPs (via autouse ``daemon_liveness_required``) when no daemon
    is reachable at the configured URL.
    """
    job_name = f"qa-probe-{uuid.uuid4().hex[:8]}"
    # One year in the future — will not fire during test execution.
    fires_at = (datetime.now(UTC) + timedelta(days=365)).isoformat()

    create_args = {
        "name": job_name,
        "trigger": "date",
        "schedule": {"run_time": fires_at},
        "target": "qa",
        "prompt": "qa-scheduler-probe",
    }

    # ── Phase 1: CREATE ──────────────────────────────────────────────────
    create_result = await client.send_envelope(
        {
            "to": "scheduler",
            "kind": "ToolInvocation",
            "payload": _toolinvocation_payload("create_job", create_args),
            "metadata": {},
            "urgency": "green",
        }
    )
    assert create_result.status_code == 200, (
        f"send_envelope (create_job) transport error: "
        f"status={create_result.status_code} body={create_result.text[:300]}"
    )
    create_data = create_result.json()
    assert isinstance(create_data, dict) and create_data.get("status") == "published", (
        f"send tool did not return 'published': {create_data!r}"
    )
    create_env_id = create_data["id"]

    # Success Ack is auto-cleared (routine green). An error Ack is NOT —
    # verify no error appeared within the poll window.
    error_ack = await client.poll_envelopes(
        predicate=_is_error_ack_for(create_env_id),
        timeout=3.0,
        interval=0.2,
    )
    assert error_ack is None, (
        f"create_job returned an error Ack: "
        f"{error_ack.get('payload', {}).get('note', '')!r}"
    )

    # ── Phase 2: VERIFY CREATE (duplicate probe) ─────────────────────────
    # Send an identical create_job call. The scheduler must return an error
    # Ack "job 'X' already exists" — proving the first create was persisted.
    dupe_result = await client.send_envelope(
        {
            "to": "scheduler",
            "kind": "ToolInvocation",
            "payload": _toolinvocation_payload("create_job", create_args),
            "metadata": {},
            "urgency": "green",
        }
    )
    assert dupe_result.status_code == 200, (
        f"send_envelope (duplicate create_job) transport error: "
        f"status={dupe_result.status_code} body={dupe_result.text[:300]}"
    )
    dupe_env_id = dupe_result.json()["id"]
    dupe_error_ack = await client.poll_envelopes(
        predicate=_is_error_ack_for(dupe_env_id),
        timeout=5.0,
        interval=0.2,
    )
    assert dupe_error_ack is not None, (
        f"Duplicate create_job did not return an error Ack within 5s. "
        f"Expected 'error: job {job_name!r} already exists'. "
        f"The scheduler may not be registered in the test daemon's endpoint config, "
        f"or the first create_job did not persist."
    )
    dupe_note = dupe_error_ack.get("payload", {}).get("note", "")
    assert "already exists" in dupe_note, (
        f"Duplicate create_job error Ack note unexpected: {dupe_note!r}"
    )

    # ── Phase 3: DELETE ──────────────────────────────────────────────────
    delete_result = await client.send_envelope(
        {
            "to": "scheduler",
            "kind": "ToolInvocation",
            "payload": _toolinvocation_payload("delete_job", {"name": job_name}),
            "metadata": {},
            "urgency": "green",
        }
    )
    assert delete_result.status_code == 200, (
        f"send_envelope (delete_job) transport error: "
        f"status={delete_result.status_code} body={delete_result.text[:300]}"
    )
    delete_env_id = delete_result.json()["id"]

    # Success Ack is auto-cleared. Verify no error appeared.
    delete_error_ack = await client.poll_envelopes(
        predicate=_is_error_ack_for(delete_env_id),
        timeout=3.0,
        interval=0.2,
    )
    assert delete_error_ack is None, (
        f"delete_job returned an error Ack: "
        f"{delete_error_ack.get('payload', {}).get('note', '')!r}"
    )

    # ── Phase 4: VERIFY DELETE (re-delete probe) ─────────────────────────
    # Send a second delete_job for the same name. The scheduler must return
    # an error Ack "job 'X' not found" — proving the delete was applied.
    redelete_result = await client.send_envelope(
        {
            "to": "scheduler",
            "kind": "ToolInvocation",
            "payload": _toolinvocation_payload("delete_job", {"name": job_name}),
            "metadata": {},
            "urgency": "green",
        }
    )
    assert redelete_result.status_code == 200, (
        f"send_envelope (re-delete) transport error: "
        f"status={redelete_result.status_code} body={redelete_result.text[:300]}"
    )
    redelete_env_id = redelete_result.json()["id"]
    redelete_error_ack = await client.poll_envelopes(
        predicate=_is_error_ack_for(redelete_env_id),
        timeout=5.0,
        interval=0.2,
    )
    assert redelete_error_ack is not None, (
        f"Re-delete did not return an error Ack within 5s. "
        f"Expected 'error: job {job_name!r} not found'. "
        f"The delete_job tool may not have removed the job from persistence."
    )
    redelete_note = redelete_error_ack.get("payload", {}).get("note", "")
    assert "not found" in redelete_note, (
        f"Re-delete error Ack note unexpected: {redelete_note!r}"
    )
