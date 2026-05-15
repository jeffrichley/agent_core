"""register_voice_tools mounts two tools with voice_id closed in."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.mcp import register_voice_tools
from agent_core_voice.protocol import VoiceInfo
from fastmcp import FastMCP


@pytest.fixture
def endpoint(tmp_path: Path, ref_wav: Path) -> VoiceEndpoint:
    return VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={
            "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="r-alice"),
            "bob": VoiceInfo(voice_id="bob", ref_wav=ref_wav, ref_text="r-bob"),
        },
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )


async def _tools_by_name(mcp: FastMCP) -> dict:
    """FastMCP returns list_tools() as a list; convert to {name: tool}."""
    return {t.name: t for t in await mcp.list_tools()}


def _text_from(result) -> str:
    """Extract the text payload from a FastMCP tool result (list or object with .content)."""
    blocks = result.content if hasattr(result, "content") else result
    return blocks[0].text


@pytest.mark.asyncio
async def test_tools_registered(endpoint: VoiceEndpoint) -> None:
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await _tools_by_name(mcp)
    assert "synthesize_speech" in tools
    assert "voice_info" in tools


@pytest.mark.asyncio
async def test_synthesize_speech_has_no_voice_id_arg(endpoint: VoiceEndpoint) -> None:
    """ISOLATION: the tool signature must not expose voice_id as a parameter."""
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await _tools_by_name(mcp)
    tool = tools["synthesize_speech"]
    # FastMCP exposes the input schema; voice_id must not be in it.
    schema = tool.parameters
    props = schema.get("properties", {})
    assert "voice_id" not in props, f"voice_id should not be a tool parameter; saw: {list(props)}"
    assert "output_path" not in props
    assert "text" in props
    assert "seed" in props


@pytest.mark.asyncio
async def test_synthesize_speech_success(endpoint: VoiceEndpoint) -> None:
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await _tools_by_name(mcp)
    result = await tools["synthesize_speech"].run({"text": "hello world", "seed": 42})
    payload = json.loads(_text_from(result))
    assert "path" in payload and Path(payload["path"]).exists()
    assert payload["sample_rate"] == 24000
    assert payload["duration_s"] > 0


@pytest.mark.asyncio
async def test_synthesize_speech_failure(endpoint: VoiceEndpoint) -> None:
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await _tools_by_name(mcp)
    result = await tools["synthesize_speech"].run({"text": "", "seed": 42})
    text = _text_from(result)
    assert text.startswith("synthesis failed:")
    assert "empty" in text.lower()


@pytest.mark.asyncio
async def test_voice_info_returns_bound_voice_only(endpoint: VoiceEndpoint) -> None:
    """voice_info exposes only the agent's bound voice, not the registry."""
    mcp = FastMCP(name="test")
    register_voice_tools(mcp=mcp, endpoint=endpoint, voice_id="alice", agent_name="alice")
    tools = await _tools_by_name(mcp)
    result = await tools["voice_info"].run({})
    info = json.loads(_text_from(result))
    assert info["voice_id"] == "alice"
    assert info["ref_text"] == "r-alice"
    # bob must NOT leak.
    assert "bob" not in json.dumps(info)
