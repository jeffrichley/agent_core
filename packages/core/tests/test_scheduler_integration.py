"""Integration: bus + SchedulerEndpoint + StubEndpoint, full round trips.

Verifies:

1. A static seed job with a 1-second interval fires and reaches the stub
   within a 10-second window.
2. Dynamic create_job via ToolInvocation lands in the scheduler, fires, and
   reaches the stub.
3. delete_job stops further fires.
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from agent_core.bus.envelope import (
    TextMessagePayload,
    ToolInvocationPayload,
)


def _config_yaml(tmp_path, jobs_yaml: str | None = None) -> str:
    bus_db = tmp_path / "bus.sqlite"
    sched_db = tmp_path / "sched.db"
    parts = [
        "bus:",
        f"  storage_path: {bus_db}",
        "endpoints:",
        "  - type: builtin.scheduler",
        "    name: scheduler",
        "    description: 'scheduler under test'",
        "    params:",
        f"      db_path: {sched_db}",
    ]
    if jobs_yaml is not None:
        parts.append(f"      jobs_path: {jobs_yaml}")
    parts += [
        "  - type: builtin.stub",
        "    name: agent-test",
        "    description: 'fake test agent'",
    ]
    return "\n".join(parts) + "\n"


@pytest.mark.asyncio
async def test_seed_job_fires_to_stub(tmp_path, build_bus):
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

    bus, _ = await build_bus(cfg)
    await bus.start()
    try:
        stub = bus._endpoints_by_name["agent-test"].endpoint

        # Wait up to 10s for the first fire. The interval trigger's first
        # fire lands at +1s; the generous ceiling absorbs slow/loaded CI
        # runners (esp. Windows) without changing what is asserted — the
        # test verifies the fire happens and the payload is correct, not
        # sub-second latency. A tight 3s window was an intermittent
        # false-red on the required windows-latest check.
        for _ in range(200):
            if stub.inbox:
                break
            await asyncio.sleep(0.05)

        assert stub.inbox, "scheduler did not fire the seed job within 10s"
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
async def test_dynamic_create_job_via_toolinvocation(tmp_path, build_bus):
    """Stub sends a create_job ToolInvocation; scheduler creates and fires the job."""
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(_config_yaml(tmp_path), encoding="utf-8")

    bus, _ = await build_bus(cfg)
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
        for _ in range(200):
            if any(
                e.payload.text == "spike-prompt"
                if isinstance(e.payload, TextMessagePayload)
                else False
                for e in stub.inbox
            ):
                break
            await asyncio.sleep(0.05)

        text_envs = [
            e
            for e in stub.inbox
            if isinstance(e.payload, TextMessagePayload) and e.payload.text == "spike-prompt"
        ]
        assert text_envs, "dynamic job did not fire within 10s"

        # And the Acknowledgment for the create_job call should be in stub's inbox too.
        acks = [e for e in stub.inbox if e.kind == "Acknowledgment"]
        assert acks, "no Acknowledgment received from scheduler"
        ack = acks[0]
        result = json.loads(ack.payload.note)
        assert result == {"status": "created", "name": "spike"}
    finally:
        await bus.stop()


def _is_delete_ack(env) -> bool:
    """True if *env* is the scheduler's Acknowledgment for a completed delete_job.

    The scheduler replies to ``delete_job`` with an Acknowledgment whose
    ``note`` is ``{"status": "deleted", "name": ...}`` (see
    ``endpoints/scheduler.py::_delete_job``). Non-Acknowledgment envelopes and
    error acks carrying a non-JSON note are safely rejected.
    """
    if env.kind != "Acknowledgment":
        return False
    try:
        return json.loads(env.payload.note).get("status") == "deleted"
    except (ValueError, AttributeError, TypeError):
        return False


@pytest.mark.asyncio
async def test_delete_job_stops_fires(tmp_path, build_bus):
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(_config_yaml(tmp_path), encoding="utf-8")

    bus, _ = await build_bus(cfg)
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

        # Wait up to 10s for it to fire at least once (generous ceiling for
        # slow/loaded CI runners; see test_seed_job_fires_to_stub).
        for _ in range(200):
            if any(
                isinstance(e.payload, TextMessagePayload) and e.payload.text == "transient"
                for e in stub.inbox
            ):
                break
            await asyncio.sleep(0.05)

        fired_count = len(
            [
                e
                for e in stub.inbox
                if isinstance(e.payload, TextMessagePayload) and e.payload.text == "transient"
            ]
        )
        assert fired_count >= 1

        # Delete it.
        await stub.send(
            to="scheduler",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(tool="delete_job", args={"name": "ephemeral"}),
        )

        # Wait for the scheduler to ACK the deletion — proof the job was
        # actually removed — instead of a blind sleep. The fixed 0.5s guess
        # raced a fire through before the delete was processed on loaded CI
        # runners (windows-latest flake: "scheduler kept firing after
        # delete_job", after == baseline + 1).
        for _ in range(200):  # up to 10s
            if any(_is_delete_ack(e) for e in stub.inbox):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("scheduler did not ACK delete_job within 10s")

        # Let a fire dispatched just before the delete took effect land — so it
        # is counted in the baseline, not the measured window. One interval + margin.
        await asyncio.sleep(1.2)

        # Wait 2s; no new fires of "transient" should arrive.
        baseline = len(
            [
                e
                for e in stub.inbox
                if isinstance(e.payload, TextMessagePayload) and e.payload.text == "transient"
            ]
        )
        await asyncio.sleep(2.0)
        after = len(
            [
                e
                for e in stub.inbox
                if isinstance(e.payload, TextMessagePayload) and e.payload.text == "transient"
            ]
        )
        assert after == baseline, "scheduler kept firing after delete_job"
    finally:
        await bus.stop()
