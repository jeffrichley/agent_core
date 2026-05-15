"""Plugin hookimpls — registration + wiring + isolation."""

from __future__ import annotations

import json as _json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from agent_core_voice import plugin as voice_plugin
from agent_core_voice.endpoint import VoiceEndpoint
from agent_core_voice.fake import FakeTTSBackend
from agent_core_voice.protocol import VoiceInfo


def test_register_endpoint_types() -> None:
    types = voice_plugin.register_endpoint_types()
    assert types == {"builtin.voice": VoiceEndpoint}


def test_reserved_endpoint_params() -> None:
    reserved = voice_plugin.reserved_endpoint_params()
    assert set(reserved) == {"voice", "voice_id"}


class _FakeClaudeCodeMCP:
    """Stand-in for ClaudeCodeMCPEndpoint — only needs ._mcp and .deferred_tool_mounters."""

    def __init__(self) -> None:
        self._mcp = FastMCP(name="agent")
        self.deferred_tool_mounters: list = []


@pytest.fixture
def two_agents(tmp_path: Path, ref_wav: Path):
    voice = VoiceEndpoint.for_test(
        backend=FakeTTSBackend(),
        voices={
            "alice": VoiceInfo(voice_id="alice", ref_wav=ref_wav, ref_text="ra"),
            "bob":   VoiceInfo(voice_id="bob",   ref_wav=ref_wav, ref_text="rb"),
        },
        output_dir=tmp_path / "out",
        audit_path=tmp_path / "audit.jsonl",
    )
    alice_mcp = _FakeClaudeCodeMCP()
    bob_mcp = _FakeClaudeCodeMCP()
    return voice, alice_mcp, bob_mcp


def _patch_isinstance_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make plugin._resolve_claude_code_mcp_cls() return our fake class."""
    import agent_core_voice.plugin as plugin_mod

    fake_cls = _FakeClaudeCodeMCP
    monkeypatch.setattr(
        plugin_mod,
        "_resolve_claude_code_mcp_cls",
        lambda: fake_cls,
        raising=False,
    )


def test_wire_happy_path_appends_mounter(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    voice, alice_mcp, bob_mcp = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp, "bob": bob_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "alice"}},
        "bob":   {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "bob"}},
    }
    voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())

    assert len(alice_mcp.deferred_tool_mounters) == 1
    assert len(bob_mcp.deferred_tool_mounters) == 1


def test_wire_unknown_voice_raises(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    voice, alice_mcp, _ = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "nope"}},
    }
    with pytest.raises(ValueError) as exc:
        voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())
    assert "nope" in str(exc.value)


def test_wire_missing_voice_endpoint_raises(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    _, alice_mcp, _ = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"alice": alice_mcp}
    raw = {
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "alice"}},
    }
    with pytest.raises(ValueError) as exc:
        voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())
    assert "voice" in str(exc.value)


def test_wire_omitted_voice_param_skips_mount(two_agents, monkeypatch: pytest.MonkeyPatch) -> None:
    voice, alice_mcp, _ = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {}},
    }
    voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())
    assert alice_mcp.deferred_tool_mounters == []


@pytest.mark.asyncio
async def test_mounter_closure_binds_per_agent(
    two_agents, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the mounter runs, alice's MCP exposes ONLY alice's voice via voice_info."""
    voice, alice_mcp, bob_mcp = two_agents
    _patch_isinstance_check(monkeypatch)

    endpoints = {"voice": voice, "alice": alice_mcp, "bob": bob_mcp}
    raw = {
        "voice": {"type": "builtin.voice", "params": {}},
        "alice": {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "alice"}},
        "bob":   {"type": "builtin.claude_code_mcp", "params": {"voice": "voice", "voice_id": "bob"}},
    }
    voice_plugin.wire_endpoints_after_registration(endpoints, raw, services=MagicMock())

    # Run each mounter (the bus_handle arg is unused by the voice mounter).
    alice_mcp.deferred_tool_mounters[0](bus_handle=None)
    bob_mcp.deferred_tool_mounters[0](bus_handle=None)

    # Use whatever helpers test_mcp_tools.py uses to list tools + extract text.
    alice_tools_list = await alice_mcp._mcp.list_tools()
    bob_tools_list = await bob_mcp._mcp.list_tools()
    alice_tools = {t.name: t for t in alice_tools_list}
    bob_tools = {t.name: t for t in bob_tools_list}

    a_blocks = await alice_tools["voice_info"].run({})
    b_blocks = await bob_tools["voice_info"].run({})
    a_text = (a_blocks.content if hasattr(a_blocks, "content") else a_blocks)[0].text
    b_text = (b_blocks.content if hasattr(b_blocks, "content") else b_blocks)[0].text
    a_info = _json.loads(a_text)
    b_info = _json.loads(b_text)
    assert a_info["voice_id"] == "alice"
    assert b_info["voice_id"] == "bob"
    assert a_info["ref_text"] == "ra"
    assert b_info["ref_text"] == "rb"
