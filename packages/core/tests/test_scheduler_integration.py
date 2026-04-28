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

import pytest

from agent_core.bus.envelope import (
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

        # Give the delete a moment to take effect.
        await asyncio.sleep(0.5)

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
