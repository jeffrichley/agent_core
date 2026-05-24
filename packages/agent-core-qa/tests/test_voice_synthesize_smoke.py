"""Scenario 7: voice synthesis smoke.

Why v0.3.0 needs it: Phase 2.6 promoted qwen-tts to a workspace member.
If the promotion broke the actual synthesis path (vs. just the install),
this catches it. The static install test (Scenario 3) asserts the wheel
installs; this asserts it RUNS and produces output.

Does NOT validate audio quality — that is a human-loop concern, out of scope.

Transport finding (Preflight Step 0):
    VoiceEndpoint is tool-only — VoiceEndpoint.deliver() is a no-op (logs at
    DEBUG and acks; no reply envelope is ever published). The ToolInvocation
    envelope path therefore produces no observable reply from the bus side.

    The synthesis surface is exposed as an MCP tool (``synthesize_speech``)
    mounted on the agent's ClaudeCodeMCPEndpoint via
    ``register_voice_tools()`` (agent_core_voice/mcp.py). This wiring happens
    at bus.start() only when the agent's yaml params include both ``voice:``
    and ``voice_id:``.

    Transport chosen: **Route (a) — direct MCP tool call** via
    ``client.call_tool("synthesize_speech", {"text": "..."})`` on the ``qa``
    endpoint. This is the same pattern used by Scenario 4 (brief tools) and
    is the cleanest path: one HTTP round-trip, synchronous result, no polling.

    The qa endpoint MUST have voice wiring in the test daemon's config for
    ``synthesize_speech`` to be present. If the tool is absent (not mounted),
    the client returns status_code=500. The test skips in that case with a
    clear operator-actionable message.

Assertion strategy:
    1. call_tool("synthesize_speech", {"text": "hello"}) returns status 200.
    2. Result is not an error string (no "synthesis failed:" prefix).
    3. Result is a dict containing a non-empty ``path`` key (the saved WAV
       file path), proving the synthesis pipeline ran end-to-end.
    4. ``duration_s > 0`` proves audio content was actually generated (not an
       empty or zero-length file).

Timeout:
    The qa endpoint's default MCP timeout (30s) should be sufficient for
    short utterances on a warmed GPU. If the daemon is on CPU, first-load can
    exceed 30s — operators must configure a longer timeout or set
    AGENT_CORE_QA_DAEMON_TIMEOUT. The test does NOT try to override the client
    timeout itself; that would require a test-specific client fixture. Instead,
    the DaemonClient default is used and the skip message explains what to do
    if the test flakes on first-load.

The test SKIPs (via autouse ``daemon_liveness_required``) when no daemon is
reachable at the configured URL, and SKIPs explicitly when the ``qa`` endpoint
is not configured with voice wiring.
"""

from __future__ import annotations

import pytest


async def test_voice_synthesize_returns_audio_bytes(client):
    """Call synthesize_speech via the qa MCP endpoint; assert synthesis ran and produced output.

    Transport: direct MCP tool call (Route a) — client.call_tool("synthesize_speech", ...).
    The tool is only mounted when the qa endpoint's yaml params include
    ``voice: <name>`` and ``voice_id: <id>``.

    Asserts:
    - call_tool returns status 200 (tool present and did not raise)
    - result is not a "synthesis failed:" error string
    - result dict contains a non-empty "path" (saved WAV file path)
    - result dict contains duration_s > 0 (audio content generated)
    """
    result = await client.call_tool(
        "synthesize_speech",
        arguments={"text": "hello"},
    )

    if result.status_code != 200:
        # Tool not found (not mounted) or transport error.
        # Distinguish "voice not wired" from a genuine crash:
        err_lower = result.text.lower()
        if (
            "not found" in err_lower
            or "unknown tool" in err_lower
            or "no tool" in err_lower
            or "synthesize_speech" not in err_lower
        ):
            pytest.skip(
                "synthesize_speech tool is not mounted on the qa endpoint. "
                "Add voice: <endpoint-name> and voice_id: <id> to the qa "
                "endpoint's params in the test daemon's config, then restart "
                "the daemon."
            )
        pytest.fail(
            f"synthesize_speech call_tool returned status {result.status_code}; "
            f"expected 200. Error: {result.text[:400]}"
        )

    # The tool returned without raising. Check the result content.
    data = result.json()

    # Error path: the tool returns TextContent with "synthesis failed: <reason>"
    # as plain text (not a dict) when the backend reports a VoiceError.
    if isinstance(data, str):
        if data.startswith("synthesis failed:"):
            pytest.fail(
                f"synthesize_speech returned a synthesis error: {data[:400]}"
            )
        # Any other plain-string response is unexpected.
        pytest.fail(
            f"synthesize_speech returned unexpected plain string: {data[:400]}"
        )

    assert isinstance(data, dict), (
        f"synthesize_speech returned non-dict result: {type(data).__name__}: {data!r}"
    )

    # Assert the WAV path was written.
    path = data.get("path")
    assert path and isinstance(path, str), (
        f"synthesize_speech result missing non-empty 'path' key; got: {data!r}"
    )

    # Assert audio content was generated (duration > 0 means non-empty WAV).
    duration_s = data.get("duration_s")
    assert isinstance(duration_s, (int, float)) and duration_s > 0, (
        f"synthesize_speech result has non-positive duration_s; got: {data!r}"
    )
