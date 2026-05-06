"""WebcamEndpoint lifecycle tests — construction, defaults, start/stop, deliver no-op."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent_core_webcam.endpoint import WebcamEndpoint
from agent_core_webcam.fake import FakeCameraBackend

from agent_core.bus.envelope import Envelope, EventPayload
from agent_core.bus.protocol import Endpoint


def test_endpoint_satisfies_bus_endpoint_protocol(endpoint: WebcamEndpoint):
    assert isinstance(endpoint, Endpoint)


def test_endpoint_has_name(endpoint: WebcamEndpoint):
    assert endpoint.name == "webcam-test"


def test_endpoint_defaults_when_paths_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ep = WebcamEndpoint(
        name="webcam-pepper",
        camera_backend=FakeCameraBackend(),
    )
    assert ep.captures_root == tmp_path / ".agent-core" / "webcam" / "webcam-pepper"
    assert ep.audit_log.path == ep.captures_root / "audit.jsonl"
    assert ep.default_camera_index == 0
    assert ep.default_resolution == (1280, 720)
    assert ep.max_resolution == (3840, 2160)
    assert ep.capture_timeout_seconds == 3.0
    assert ep.enabled is True


def test_endpoint_accepts_explicit_overrides(tmp_path):
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "shots",
        audit_log_path=tmp_path / "log.jsonl",
        default_camera_index=1,
        default_resolution=[640, 480],
        max_resolution=[1920, 1080],
        capture_timeout_seconds=5.0,
        enabled=False,
        camera_backend=FakeCameraBackend(),
    )
    assert ep.captures_root == tmp_path / "shots"
    assert ep.audit_log.path == tmp_path / "log.jsonl"
    assert ep.default_camera_index == 1
    assert ep.default_resolution == (640, 480)
    assert ep.max_resolution == (1920, 1080)
    assert ep.capture_timeout_seconds == 5.0
    assert ep.enabled is False


async def test_start_stores_bus_handle_and_stop_clears_it(endpoint: WebcamEndpoint):
    class _FakeHandle:
        pass

    handle = _FakeHandle()
    await endpoint.start(handle)
    assert endpoint._handle is handle
    await endpoint.stop()
    assert endpoint._handle is None


async def test_deliver_is_a_noop_and_acks(endpoint: WebcamEndpoint):
    """Webcam is tool-only; deliver must not crash AND must ack the envelope."""

    class _FakeHandle:
        def __init__(self):
            self.acked: list[str] = []

        async def ack(self, envelope_id: str) -> None:
            self.acked.append(envelope_id)

    handle = _FakeHandle()
    await endpoint.start(handle)

    env = Envelope(
        id="env-1",
        correlation_id="corr-1",
        in_reply_to=None,
        to="webcam-test",
        kind="Event",
        payload=EventPayload(type="Stray", data={}),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    await endpoint.deliver(env)
    assert handle.acked == ["env-1"], "deliver must ack so the bus doesn't redeliver"


def test_resolution_lists_are_normalized_to_tuples(tmp_path):
    """YAML deserializes lists; the endpoint stores tuples for hashability + ordering clarity."""
    ep = WebcamEndpoint(
        name="webcam-test",
        captures_root=tmp_path / "x",
        audit_log_path=tmp_path / "y.jsonl",
        default_resolution=[800, 600],
        max_resolution=[2000, 1500],
        camera_backend=FakeCameraBackend(),
    )
    assert isinstance(ep.default_resolution, tuple)
    assert isinstance(ep.max_resolution, tuple)
