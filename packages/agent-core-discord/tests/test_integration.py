"""Optional real-bot integration test.

Skipped unless these env vars are set:
  DISCORD_TEST_TOKEN       — bot token for a Discord application set up for testing
  DISCORD_TEST_CHANNEL_ID  — guild channel id where the bot can send/read
  DISCORD_TEST_USER_ID     — your Discord user id (for any access checks)

This is a smoke flow only. CI does not run it. Operators run it manually
to validate against a live Discord application before declaring v1 done.

The test exercises the same boot path the runner uses: it builds a real
Bus, registers the DiscordEndpoint, and calls bus.start(). If start()
were still awaiting the blocking gateway loop, bus.start() would deadlock
and this test would hang — that's a feature, not a bug.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from agent_core.bus.core import Bus, BusConfig, EndpointSpec
from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    ToolInvocationPayload,
)
from agent_core_discord.endpoint import DiscordEndpoint


REQUIRED_ENV = ("DISCORD_TEST_TOKEN", "DISCORD_TEST_CHANNEL_ID", "DISCORD_TEST_USER_ID")
pytestmark = pytest.mark.skipif(
    not all(os.environ.get(v) for v in REQUIRED_ENV),
    reason=f"set {','.join(REQUIRED_ENV)} to run the integration test",
)


class _ProbeEndpoint:
    """Minimal endpoint that just records every envelope delivered to it.

    Used to capture the Acknowledgment the discord endpoint publishes back
    after dispatching a ToolInvocation."""

    def __init__(self, name: str = "probe-it"):
        self.name = name
        self.received: list[Envelope] = []
        self._handle = None

    async def start(self, bus) -> None:
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        self.received.append(envelope)
        if self._handle is not None:
            await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        self._handle = None

    async def send(self, *, to: str, payload: ToolInvocationPayload) -> str:
        """Helper: publish a ToolInvocation through the bus to `to`."""
        assert self._handle is not None
        env_id = uuid.uuid4().hex
        env = Envelope(
            id=env_id,
            correlation_id=uuid.uuid4().hex,
            from_=self.name,
            to=to,
            kind="ToolInvocation",
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        await self._handle.publish(env)
        return env_id

    async def wait_for_ack(self, in_reply_to: str, *, timeout: float = 10.0) -> Envelope:
        """Spin until an Acknowledgment for `in_reply_to` shows up."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for env in self.received:
                if (
                    env.kind == "Acknowledgment"
                    and isinstance(env.payload, AcknowledgmentPayload)
                    and env.in_reply_to == in_reply_to
                ):
                    return env
            await asyncio.sleep(0.1)
        raise AssertionError(f"no Acknowledgment for {in_reply_to} within {timeout}s")


@pytest.mark.asyncio
async def test_real_bot_send_react_edit_via_bus(tmp_path):
    """Smoke flow: bus boots discord endpoint, then sends/reacts/edits via real bus dispatch.

    This is the boot path the runner uses — bus.start() must return after
    every endpoint's start() returns. If DiscordEndpoint.start() were still
    awaiting the blocking gateway loop (the original bug), bus.start() would
    never return and this test would hang at that line.
    """
    channel_id = os.environ["DISCORD_TEST_CHANNEL_ID"]

    config = BusConfig(storage_path=tmp_path / "bus.sqlite")
    bus = Bus(config)

    probe = _ProbeEndpoint(name="probe-it")
    discord_ep = DiscordEndpoint(
        name="discord-it-test",
        target="probe-it",
        token_env="DISCORD_TEST_TOKEN",
    )

    bus.register(EndpointSpec(endpoint=probe))
    bus.register(EndpointSpec(endpoint=discord_ep))

    try:
        # If start() is broken (awaits blocking gateway loop), this hangs.
        await bus.start()
        # 1. send
        send_id = await probe.send(
            to="discord-it-test",
            payload=ToolInvocationPayload(
                tool="send",
                args={"channel_id": channel_id, "text": "agent-core-discord smoke ping"},
            ),
        )
        ack = await probe.wait_for_ack(send_id)
        result = json.loads(ack.payload.note)
        assert "message_id" in result, f"send did not return message_id: {result}"
        msg_id = result["message_id"]

        # 2. react
        react_id = await probe.send(
            to="discord-it-test",
            payload=ToolInvocationPayload(
                tool="react",
                args={"channel_id": channel_id, "message_id": msg_id, "emoji": "✅"},
            ),
        )
        await probe.wait_for_ack(react_id)

        # 3. edit
        edit_id = await probe.send(
            to="discord-it-test",
            payload=ToolInvocationPayload(
                tool="edit",
                args={
                    "channel_id": channel_id,
                    "message_id": msg_id,
                    "text": "agent-core-discord smoke ping (edited)",
                },
            ),
        )
        await probe.wait_for_ack(edit_id)
    finally:
        await bus.stop()
