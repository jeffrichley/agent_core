"""Agent-facing MCP tool surface for the voice endpoint.

Two tools: ``synthesize_speech`` and ``voice_info``. Both close ``voice_id``
and ``agent_name`` into their callable at mount time. The agent's tool
surface has no ``voice_id`` parameter -- by construction, an agent cannot
request another agent's voice.

Returns a list of MCP TextContent blocks. On error, returns a single
TextContent with ``"synthesis failed: <reason>"``. No exceptions escape.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp.types import TextContent

from agent_core_voice.endpoint import SynthesisError, SynthesisSuccess

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from agent_core_voice.endpoint import VoiceEndpoint


def register_voice_tools(
    *,
    mcp: FastMCP,
    endpoint: VoiceEndpoint,
    voice_id: str,
    agent_name: str,
) -> None:
    """Mount ``synthesize_speech`` and ``voice_info`` on ``mcp``.

    ``voice_id`` and ``agent_name`` are closed into the callables -- they are
    NOT parameters of the resulting tools.
    """

    @mcp.tool(
        name="synthesize_speech",
        description=(
            "Synthesize text in your assigned voice and save to a wav file. "
            "Returns the saved file path plus duration and sample-rate metadata. "
            "Use the returned path to attach to Discord, archive, or hand off "
            "to another tool."
        ),
    )
    async def _synthesize(text: str, seed: int = 42) -> list[Any]:
        result = await endpoint.synthesize_safe(
            agent_name=agent_name,
            voice_id=voice_id,
            text=text,
            seed=seed,
        )
        if isinstance(result, SynthesisError):
            return [TextContent(type="text", text=f"synthesis failed: {result.message}")]
        assert isinstance(result, SynthesisSuccess)
        payload = {
            "path": result.path,
            "duration_s": result.duration_s,
            "sample_rate": 24000,
            "generation_s": result.generation_s,
        }
        return [TextContent(type="text", text=json.dumps(payload))]

    @mcp.tool(
        name="voice_info",
        description=(
            "Return metadata about your assigned voice (id, ref clip, sample rate, mode)."
        ),
    )
    async def _voice_info() -> list[Any]:
        info = endpoint.voice_info(voice_id)
        return [TextContent(type="text", text=json.dumps(info))]


__all__ = ["register_voice_tools"]
