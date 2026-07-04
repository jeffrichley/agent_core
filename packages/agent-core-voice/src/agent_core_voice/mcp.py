"""Agent-facing MCP tool surface for the voice endpoint (async-via-bus).

Two tools: ``synthesize_speech`` and ``voice_info``. Both close ``voice_id``
and ``agent_name`` into their callable at mount time. The agent's tool
surface has no ``voice_id`` parameter — by construction, an agent cannot
request another agent's voice.

``synthesize_speech`` is **publish-and-return**: it builds a
``SynthesisRequest`` envelope, publishes it to the voice endpoint via the
caller's ``BusHandle``, and returns ``{"request_id", "status": "queued"}``
immediately. The caller's inbox wakes when a correlated
``SynthesisReady`` (success) or ``SynthesisFailed`` (failure) arrives.

Returns a list of MCP TextContent blocks. No exceptions escape the tool —
client-side validation errors surface as ``{"error", "detail"}`` JSON.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp.types import TextContent

from agent_core_voice.envelopes import SynthesisRequestPayload

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle
    from fastmcp import FastMCP


def register_voice_tools(
    *,
    mcp: FastMCP,
    bus_handle: BusHandle,
    voice_endpoint_name: str,
    voice_id: str,
    agent_name: str,
    voice_info: dict[str, Any],
) -> None:
    """Mount ``synthesize_speech`` (envelope-fire) + ``voice_info`` on ``mcp``.

    ``voice_id``, ``agent_name``, and the target voice endpoint name are
    closed at mount time. Synthesis is async-via-bus: the tool publishes a
    ``SynthesisRequest`` envelope and returns the envelope id immediately.
    The caller's inbox wakes when a ``SynthesisReady`` (or
    ``SynthesisFailed``) arrives.
    """
    # ``agent_name`` and ``voice_id`` are accepted for closure parity with the
    # pre-bus-async signature (callers in plugin.py thread them through), but
    # they're not used by the bus path: bus stamps ``from_`` on publish, and
    # the voice endpoint resolves voice_id by looking up the agent it
    # registered at wire time.
    del agent_name, voice_id

    @mcp.tool(
        name="synthesize_speech",
        description=(
            "Synthesize text in your assigned voice. Returns {request_id, status} "
            "immediately; the synthesized audio arrives on your inbox as a "
            "SynthesisReady event with `wav_path`. SynthesisFailed arrives on the "
            "same inbox if synthesis fails. "
            "``format`` selects the output container: \"wav\" (default), \"mp3\", or \"ogg\"."
        ),
    )
    async def _synthesize(
        text: str,
        timeout_s: float | None = None,
        retain_s: float | None = None,
        options: dict | None = None,
        format: str = "wav",
    ) -> list[Any]:
        from agent_core.bus.envelope import Envelope, EventPayload

        try:
            payload = SynthesisRequestPayload(
                text=text,
                timeout_s=timeout_s,
                retain_s=retain_s,
                options=options,
                format=format,  # type: ignore[arg-type]
            )
        except Exception as exc:  # pydantic ValidationError or anything else
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": "invalid_request", "detail": str(exc)}),
                )
            ]

        env_id = str(uuid.uuid4())
        env = Envelope(
            id=env_id,
            correlation_id=env_id,
            to=voice_endpoint_name,
            kind="Event",
            payload=EventPayload(
                type="SynthesisRequest",
                data=payload.model_dump(exclude_none=True),
            ),
            created_at=datetime.now(UTC),
        )
        await bus_handle.publish(env)
        return [
            TextContent(
                type="text",
                text=json.dumps({"request_id": env_id, "status": "queued"}),
            )
        ]

    @mcp.tool(
        name="voice_info",
        description="Return metadata about your assigned voice.",
    )
    async def _voice_info() -> list[Any]:
        return [TextContent(type="text", text=json.dumps(voice_info, ensure_ascii=False))]


__all__ = ["register_voice_tools"]
