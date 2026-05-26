"""register_voice_tools mounts envelope-fire ``synthesize_speech`` + ``voice_info``.

Post-bus-async-flip (spec Task 7): ``synthesize_speech`` is publish-and-return.
The tool publishes a ``SynthesisRequest`` envelope onto a captured ``BusHandle``
and returns ``{"request_id", "status": "queued"}`` synchronously. The actual
WAV arrives later as a ``SynthesisReady`` event on the caller's inbox — that
end-to-end path is covered by ``test_envelope_flow.py``; here we assert the
client-side tool surface in isolation by stubbing the bus.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_core_voice.envelopes import SynthesisRequestPayload
from agent_core_voice.mcp import register_voice_tools
from fastmcp import FastMCP


class _CapturingBusHandle:
    """Stub BusHandle that records every envelope passed to ``publish``.

    Mirrors the real ``agent_core.bus.handle.BusHandle.publish`` shape
    (``async def publish(envelope, to=None)``) but writes to a list instead
    of dispatching. Calling .publish never raises — that's the publish-
    and-return contract from the tool's perspective; bus-side failures
    show up as SynthesisFailed events later.
    """

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, envelope: Any, to: str | list[str] | None = None) -> None:
        del to  # not exercised by the voice tool
        self.published.append(envelope)


_VOICE_INFO_ALICE: dict[str, Any] = {
    "voice_id": "alice",
    "ref_clip": "/tmp/ref.wav",
    "ref_text": "r-alice",
    "blend": None,
    "sample_rate": 24000,
    "mode": "1.7B Base + ICL voice clone",
}


async def _tools_by_name(mcp: FastMCP) -> dict[str, Any]:
    """FastMCP returns ``list_tools()`` as a list; convert to {name: tool}."""
    return {t.name: t for t in await mcp.list_tools()}


def _text_from(result: Any) -> str:
    """Extract the text payload from a FastMCP tool result."""
    blocks = result.content if hasattr(result, "content") else result
    return blocks[0].text


def _mount(*, voice_info: dict[str, Any] | None = None) -> tuple[FastMCP, _CapturingBusHandle]:
    mcp = FastMCP(name="test")
    bus = _CapturingBusHandle()
    register_voice_tools(
        mcp=mcp,
        bus_handle=bus,  # type: ignore[arg-type]
        voice_endpoint_name="voice",
        voice_id="alice",
        agent_name="alice",
        voice_info=voice_info or _VOICE_INFO_ALICE,
    )
    return mcp, bus


@pytest.mark.asyncio
async def test_tools_registered() -> None:
    mcp, _ = _mount()
    tools = await _tools_by_name(mcp)
    assert "synthesize_speech" in tools
    assert "voice_info" in tools


@pytest.mark.asyncio
async def test_synthesize_speech_has_no_voice_id_arg() -> None:
    """ISOLATION: the tool signature must not expose voice_id (or output_path) as a parameter."""
    mcp, _ = _mount()
    tools = await _tools_by_name(mcp)
    tool = tools["synthesize_speech"]
    schema = tool.parameters
    props = schema.get("properties", {})
    assert "voice_id" not in props, f"voice_id should not be a tool parameter; saw: {list(props)}"
    assert "output_path" not in props
    assert "text" in props
    # New envelope-shape params:
    assert "timeout_s" in props
    assert "retain_s" in props
    assert "options" in props


@pytest.mark.asyncio
async def test_synthesize_speech_returns_queued() -> None:
    """The tool returns ``{request_id, status: "queued"}`` synchronously."""
    mcp, bus = _mount()
    tools = await _tools_by_name(mcp)
    result = await tools["synthesize_speech"].run({"text": "hello world"})
    payload = json.loads(_text_from(result))
    assert set(payload) == {"request_id", "status"}
    assert payload["status"] == "queued"
    # request_id is a UUID4-shaped string.
    assert isinstance(payload["request_id"], str)
    assert len(payload["request_id"]) == 36  # canonical uuid4 hex with dashes
    # And the envelope was actually published.
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_synthesize_speech_publishes_synthesis_request_envelope() -> None:
    """The published envelope is an Event/SynthesisRequest addressed to the voice endpoint."""
    mcp, bus = _mount()
    tools = await _tools_by_name(mcp)
    result = await tools["synthesize_speech"].run(
        {"text": "hello world", "options": {"foo": "bar"}}
    )
    payload = json.loads(_text_from(result))

    [env] = bus.published
    assert env.to == "voice"
    assert env.kind == "Event"
    assert env.payload.type == "SynthesisRequest"
    # correlation_id == id so a downstream consumer can correlate replies.
    assert env.id == payload["request_id"]
    assert env.correlation_id == env.id

    # Round-trip the data dict through the typed model to confirm shape.
    req = SynthesisRequestPayload.model_validate(env.payload.data)
    assert req.text == "hello world"
    assert req.options == {"foo": "bar"}
    # Unset optional fields excluded from the wire payload.
    assert "timeout_s" not in env.payload.data
    assert "retain_s" not in env.payload.data


@pytest.mark.asyncio
async def test_synthesize_speech_propagates_timeout_and_retain() -> None:
    """When the caller sets timeout_s / retain_s, they ride on the envelope."""
    mcp, bus = _mount()
    tools = await _tools_by_name(mcp)
    await tools["synthesize_speech"].run(
        {"text": "hi", "timeout_s": 5.0, "retain_s": 60.0}
    )
    [env] = bus.published
    req = SynthesisRequestPayload.model_validate(env.payload.data)
    assert req.timeout_s == 5.0
    assert req.retain_s == 60.0


@pytest.mark.asyncio
async def test_synthesize_speech_invalid_text_returns_error_does_not_publish() -> None:
    """Empty text fails pydantic validation client-side; nothing is published."""
    mcp, bus = _mount()
    tools = await _tools_by_name(mcp)
    result = await tools["synthesize_speech"].run({"text": ""})
    payload = json.loads(_text_from(result))
    assert payload["error"] == "invalid_request"
    assert "detail" in payload
    # No envelope made it onto the bus.
    assert bus.published == []


@pytest.mark.asyncio
async def test_synthesize_speech_timeout_over_cap_returns_error() -> None:
    """timeout_s above the 600s cap is rejected client-side, not published."""
    mcp, bus = _mount()
    tools = await _tools_by_name(mcp)
    result = await tools["synthesize_speech"].run({"text": "hi", "timeout_s": 9999.0})
    payload = json.loads(_text_from(result))
    assert payload["error"] == "invalid_request"
    assert bus.published == []


@pytest.mark.asyncio
async def test_voice_info_returns_bound_voice_only() -> None:
    """voice_info exposes only the agent's bound voice, not the registry."""
    mcp, _ = _mount()
    tools = await _tools_by_name(mcp)
    result = await tools["voice_info"].run({})
    info = json.loads(_text_from(result))
    assert info["voice_id"] == "alice"
    assert info["ref_text"] == "r-alice"
    # No other voice ids leak.
    assert "bob" not in json.dumps(info)
