"""Optional real-bot integration test.

Skipped unless these env vars are set:
  DISCORD_TEST_TOKEN       — bot token for a Discord application set up for testing
  DISCORD_TEST_CHANNEL_ID  — guild channel id where the bot can send/read
  DISCORD_TEST_USER_ID     — your Discord user id (for any access checks)

This is a smoke flow only. CI does not run it. Operators run it manually
to validate against a live Discord application before declaring v1 done.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from agent_core.bus.envelope import (
    EndpointInfo,
    Envelope,
    ToolInvocationPayload,
)
from agent_core_discord.endpoint import DiscordEndpoint


REQUIRED_ENV = ("DISCORD_TEST_TOKEN", "DISCORD_TEST_CHANNEL_ID", "DISCORD_TEST_USER_ID")
pytestmark = pytest.mark.skipif(
    not all(os.environ.get(v) for v in REQUIRED_ENV),
    reason=f"set {','.join(REQUIRED_ENV)} to run the integration test",
)


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


@pytest.mark.asyncio
async def test_real_bot_send_and_react():
    """Smoke flow: bot connects, sends a message, reacts to it, edits it."""
    channel_id = os.environ["DISCORD_TEST_CHANNEL_ID"]
    handle = _Recording()
    ep = DiscordEndpoint(
        name="discord-it-test",
        target="agent-it-test",
        token_env="DISCORD_TEST_TOKEN",
    )
    # discord.Client.start() blocks the loop; spawn it.
    start_task = asyncio.create_task(ep.start(handle))
    # Give the connection a few seconds to settle.
    await asyncio.sleep(5)
    try:
        # Send.
        send_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            from_="agent-it-test",
            to="discord-it-test",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="send",
                args={"channel_id": channel_id, "text": "agent-core-discord smoke ping"},
            ),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(send_env)
        # Look at the Acknowledgment we got back.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert acks, "no Acknowledgment from send"
        import json

        sent = json.loads(acks[-1].payload.note)
        msg_id = sent["message_id"]

        # React.
        react_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            from_="agent-it-test",
            to="discord-it-test",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="react",
                args={"channel_id": channel_id, "message_id": msg_id, "emoji": "✅"},
            ),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(react_env)

        # Edit.
        edit_env = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            from_="agent-it-test",
            to="discord-it-test",
            kind="ToolInvocation",
            payload=ToolInvocationPayload(
                tool="edit",
                args={
                    "channel_id": channel_id,
                    "message_id": msg_id,
                    "text": "agent-core-discord smoke ping (edited)",
                },
            ),
            created_at=datetime.now(timezone.utc),
        )
        await ep.deliver(edit_env)

    finally:
        await ep.stop()
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass
        except BaseException:
            pass
