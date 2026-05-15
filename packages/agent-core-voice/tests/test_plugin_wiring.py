"""Plugin hookimpls — registration + wiring + isolation."""

from __future__ import annotations

from agent_core_voice import plugin as voice_plugin
from agent_core_voice.endpoint import VoiceEndpoint


def test_register_endpoint_types() -> None:
    types = voice_plugin.register_endpoint_types()
    assert types == {"builtin.voice": VoiceEndpoint}


def test_reserved_endpoint_params() -> None:
    reserved = voice_plugin.reserved_endpoint_params()
    assert set(reserved) == {"voice", "voice_id"}
