from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from agent_core.bus.envelope import EventPayload
from agent_core.bus.runner import build_bus_from_config
from agent_core.endpoints.handoff_jobs import HandoffJobRequest, HandoffJobsEndpoint


@pytest.mark.asyncio
async def test_handoff_jobs_endpoint_writes_status_and_publishes_ready(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_path = vault_root / "transcript.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text):
                return "# Handoff\n"

            monkeypatch.setattr(
                type(endpoint),
                "_extract_handoff",
                _fake_extract,
            )
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-123",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["job_id"]

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "ready"
            assert status["session_id"] == "session-123"
            assert status["job_id"] == body["job_id"]
            assert "content_sha256" in status
            assert handoff_path.exists()

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            for _ in range(40):
                if any(
                    env.kind == "Event"
                    and isinstance(env.payload, EventPayload)
                    and env.payload.type == "HandoffReady"
                    and env.payload.data.get("job_id") == body["job_id"]
                    for env in stub_ep.inbox
                ):
                    break
                await asyncio.sleep(0.05)
            assert any(
                env.kind == "Event"
                and isinstance(env.payload, EventPayload)
                and env.payload.type == "HandoffReady"
                and env.payload.data.get("job_id") == body["job_id"]
                for env in stub_ep.inbox
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_jobs_endpoint_writes_extractor_output(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_path = vault_root / "transcript.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"
    extracted = "# Extracted handoff\n\n- source: test\n"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text):
                return extracted

            monkeypatch.setattr(
                type(endpoint),
                "_extract_handoff",
                _fake_extract,
            )
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-extract",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(40):
                if handoff_path.exists():
                    break
                await asyncio.sleep(0.05)

            assert handoff_path.read_text(encoding="utf-8") == extracted.rstrip() + "\n"
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_jobs_endpoint_rejects_path_outside_vault_root(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_path = vault_root / "transcript.jsonl"
    transcript_path.write_text("{}", encoding="utf-8")

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-esc",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(tmp_path / "outside-handoff.md"),
                "handoff_status_path": str(vault_root / "handoff-status.json"),
                "transcript_path": str(transcript_path),
                "requested_at": datetime.now(UTC).isoformat(),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 403
            assert "escapes vault_root" in resp.json()["error"]
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_extract_handoff_success_uses_model_output(monkeypatch):
    endpoint = HandoffJobsEndpoint(name="handoff-jobs")
    req = HandoffJobRequest(
        session_id="session-1",
        event="SessionEnd",
        agent_name="pepper",
        vault_root="/vault",
        handoff_path="/vault/handoff.md",
        handoff_status_path="/vault/handoff-status.json",
        transcript_path="/vault/transcript.jsonl",
        requested_at=datetime.now(UTC),
    )
    expected = "# Handoff Note\n\n- extracted: yes"

    fake_sdk = AsyncMock(return_value=expected)
    monkeypatch.setattr(endpoint, "_call_agent_sdk", fake_sdk)

    actual = await endpoint._extract_handoff(req, "transcript text")
    assert actual == expected


@pytest.mark.asyncio
async def test_extract_handoff_fallback_on_exception(monkeypatch):
    endpoint = HandoffJobsEndpoint(name="handoff-jobs")
    req = HandoffJobRequest(
        session_id="session-fallback",
        event="PreCompact",
        agent_name="pepper",
        vault_root="/vault",
        handoff_path="/vault/handoff.md",
        handoff_status_path="/vault/handoff-status.json",
        transcript_path="/vault/transcript.jsonl",
        requested_at=datetime.now(UTC),
    )

    fake_sdk = AsyncMock(side_effect=RuntimeError("sdk boom"))
    monkeypatch.setattr(endpoint, "_call_agent_sdk", fake_sdk)

    fallback = await endpoint._extract_handoff(req, "transcript text")
    assert fallback.startswith("# Handoff (pepper)")
    assert "- session_id: session-fallback" in fallback
    assert "- event: PreCompact" in fallback
    assert "Extraction fallback used: sdk boom" in fallback


@pytest.mark.asyncio
async def test_worker_retries_then_marks_failed(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_path = vault_root / "transcript.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
      max_attempts: 2
      retry_backoff_seconds: 0
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    attempts = {"count": 0}

    async def always_fail_extract(self, req, transcript_text):
        attempts["count"] += 1
        raise RuntimeError("forced extract failure")

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint
            monkeypatch.setattr(type(endpoint), "_extract_handoff", always_fail_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-fail",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "failed":
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "failed"
            assert status["session_id"] == "session-fail"
            assert status["job_id"] == job_id
            assert status["error"] == "forced extract failure"
            assert attempts["count"] == 2

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            for _ in range(40):
                if any(
                    env.kind == "Event"
                    and isinstance(env.payload, EventPayload)
                    and env.payload.type == "HandoffFailed"
                    and env.payload.data.get("job_id") == job_id
                    for env in stub_ep.inbox
                ):
                    break
                await asyncio.sleep(0.05)
            assert any(
                env.kind == "Event"
                and isinstance(env.payload, EventPayload)
                and env.payload.type == "HandoffFailed"
                and env.payload.data.get("job_id") == job_id
                for env in stub_ep.inbox
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
