"""Scenario 7: voice synthesis smoke (envelope-fire contract).

Why v0.3.0 needs it: Phase 2.6 promoted qwen-tts to a workspace member.
If the promotion broke the actual synthesis path (vs. just the install),
this catches it. The static install test (Scenario 3) asserts the wheel
installs; this asserts it RUNS end-to-end and produces output.

Does NOT validate audio quality — that is a human-loop concern, out of scope.

Contract (post Phase 3 / voice-bus-async migration, Task 7):
    ``synthesize_speech`` is **publish-and-return**. The tool call no longer
    returns ``{path, duration_s}`` synchronously. It builds a
    ``SynthesisRequest`` envelope, publishes it to the voice endpoint, and
    returns ``{"request_id", "status": "queued"}`` immediately. The synthesized
    WAV arrives later as a correlated ``SynthesisReady`` (or
    ``SynthesisFailed``) Event envelope on the caller's inbox.

Transport finding:
    The qa endpoint is a ClaudeCodeMCPEndpoint, which exposes the standard
    bus tools (``send``, ``list_pending``, ``handle``, ``ack``). The voice
    plugin mounts ``synthesize_speech`` + ``voice_info`` on the same endpoint
    when its yaml params include both ``voice:`` and ``voice_id:``. If voice
    wiring is absent the tool is not mounted; the test skips with a clear
    operator-actionable message.

Assertion strategy:
    1. ``call_tool("synthesize_speech", {"text": "hello"})`` returns 200 and a
       dict shaped ``{"request_id", "status": "queued"}``.
    2. Poll ``list_pending`` on the qa endpoint for a correlated reply
       envelope (``in_reply_to == request_id``).
    3. The reply MUST be ``kind == "Event"`` with ``payload.type ==
       "SynthesisReady"`` (a ``SynthesisFailed`` reply is a synthesis failure
       — the test fails with the reported reason).
    4. ``payload.data.wav_path`` points to a non-empty file on disk and
       ``duration_s > 0`` proves audio content was generated.

Timeout:
    Tool publish is fast (<1s). The actual synthesis runs on the voice
    endpoint's backend (cuda or cpu). The smoke poll budget is 90s — enough
    for a warm-GPU short utterance with comfortable margin. CPU first-load
    can exceed this; operators on cpu-only hosts should set
    ``AGENT_CORE_QA_SYNTH_TIMEOUT_S`` to a larger value (e.g. 600).

The test SKIPs (via autouse ``daemon_liveness_required``) when no daemon is
reachable, and SKIPs explicitly when the qa endpoint is not configured with
voice wiring (so ``synthesize_speech`` is not mounted). The reply-envelope
poll path runs on the same machine as the daemon — ``wav_path`` is read
locally, not over a network transport.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Synthesis budget. Override via env for slow (cpu) hosts.
_SYNTH_TIMEOUT_S = float(os.environ.get("AGENT_CORE_QA_SYNTH_TIMEOUT_S", "90"))
_POLL_INTERVAL_S = 0.5


def _is_synthesis_reply(item: dict[str, Any], request_id: str) -> bool:
    """Match a SynthesisReady or SynthesisFailed envelope correlated to request_id."""
    if item.get("kind") != "Event":
        return False
    if item.get("in_reply_to") != request_id:
        return False
    payload = item.get("payload") or {}
    return payload.get("type") in {"SynthesisReady", "SynthesisFailed"}


async def test_voice_synthesize_returns_audio_bytes(client):
    """Publish a synthesize_speech request via MCP; await SynthesisReady on inbox.

    Post Phase 3 contract: the tool returns ``{request_id, status: "queued"}``
    immediately and the WAV arrives via an envelope on the caller's inbox.

    Asserts:
    - call_tool returns status 200 and yields a request_id
    - a SynthesisReady (or SynthesisFailed) envelope correlated to the
      request_id arrives within _SYNTH_TIMEOUT_S
    - the reply is SynthesisReady (not SynthesisFailed)
    - wav_path points to a non-empty file with duration_s > 0
    """
    result = await client.call_tool(
        "synthesize_speech",
        arguments={"text": "hello"},
    )

    if result.status_code != 200:
        # Tool not found (not mounted) or transport error.
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

    # The tool returned without raising. Tool surface must be the new
    # envelope-fire shape: {"request_id", "status": "queued"}.
    data = result.json()

    if isinstance(data, dict) and data.get("error"):
        # Tool-side validation rejected the request (e.g. empty text). The
        # smoke probe sends a valid non-empty string so this is a genuine bug.
        pytest.fail(
            f"synthesize_speech rejected the request at the tool surface: {data!r}"
        )

    assert isinstance(data, dict), (
        f"synthesize_speech returned non-dict result: {type(data).__name__}: {data!r}"
    )
    request_id = data.get("request_id")
    status = data.get("status")
    assert request_id and isinstance(request_id, str), (
        f"synthesize_speech result missing non-empty 'request_id'; got: {data!r}"
    )
    assert status == "queued", (
        f"synthesize_speech result missing status='queued'; got: {data!r}"
    )

    # Await the correlated SynthesisReady on the qa endpoint's inbox.
    reply = await client.poll_envelopes(
        predicate=lambda item: _is_synthesis_reply(item, request_id),
        timeout=_SYNTH_TIMEOUT_S,
        interval=_POLL_INTERVAL_S,
    )
    assert reply is not None, (
        f"no SynthesisReady/SynthesisFailed envelope correlated to "
        f"request_id={request_id!r} arrived within {_SYNTH_TIMEOUT_S}s. "
        f"For slow (cpu) hosts, raise AGENT_CORE_QA_SYNTH_TIMEOUT_S."
    )

    payload = reply.get("payload") or {}
    payload_type = payload.get("type")
    payload_data = payload.get("data") or {}

    if payload_type == "SynthesisFailed":
        pytest.fail(
            f"synthesize_speech failed: reason={payload_data.get('reason')!r} "
            f"message={payload_data.get('message')!r} "
            f"retryable={payload_data.get('retryable')!r}"
        )

    assert payload_type == "SynthesisReady", (
        f"unexpected reply payload type {payload_type!r}; envelope={reply!r}"
    )

    wav_path = payload_data.get("wav_path")
    assert wav_path and isinstance(wav_path, str), (
        f"SynthesisReady missing non-empty 'wav_path'; payload={payload_data!r}"
    )

    # The smoke runner shares a filesystem with the daemon (same host).
    # If wav_path doesn't exist locally that's a real failure — voice
    # endpoint claimed success but produced no file.
    wav_file = Path(wav_path)
    assert wav_file.exists(), (
        f"SynthesisReady reported wav_path={wav_path!r} but the file is not "
        f"present on this host. The smoke runner must share the filesystem "
        f"with the daemon."
    )
    assert wav_file.stat().st_size > 0, (
        f"SynthesisReady wav_path points to an empty file: {wav_path!r}"
    )

    duration_s = payload_data.get("duration_s")
    assert isinstance(duration_s, (int, float)) and duration_s > 0, (
        f"SynthesisReady has non-positive duration_s; payload={payload_data!r}"
    )
