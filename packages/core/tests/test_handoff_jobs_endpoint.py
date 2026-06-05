from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from agent_core.bus.envelope import EventPayload
from agent_core.bus.runner import build_bus_from_config
from agent_core.endpoints.handoff_jobs import (
    HandoffJobRequest,
    HandoffJobsEndpoint,
    _read_transcript_tail,
)


@pytest.mark.asyncio
async def test_handoff_jobs_endpoint_writes_status_and_publishes_ready(tmp_path, monkeypatch):
    # Real Claude Code transcripts live under ~/.claude/projects, NOT inside
    # any agent vault — mirror that here so tests don't paper over the
    # cross-root validation.
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-123.jsonl"
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
            async def _fake_extract(self, req, transcript_text, job_id):
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
                "transcript_root": str(transcript_root),
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
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-extract.jsonl"
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
            async def _fake_extract(self, req, transcript_text, job_id):
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
                "transcript_root": str(transcript_root),
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
async def test_handoff_publishes_to_mailbox_when_distinct_from_agent_name(
    tmp_path, monkeypatch
):
    # Real configs split agent identity (display name like "Pepper" or
    # "testbot") from bus routing (endpoint name like "pepper" or
    # "agent-testbot"). When they differ, the worker MUST publish to the
    # mailbox/endpoint name, not the human identity, otherwise the bus
    # rejects with "publish to unregistered endpoint" (caught on testbot
    # 2026-05-05 when SessionEnd → /internal/handoff-jobs → worker tried
    # to publish to "testbot" while the endpoint is "agent-testbot").
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-mailbox.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
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
    name: agent-testbot
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

            async def _fake_extract(self, req, transcript_text, job_id):
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-mailbox",
                "event": "SessionEnd",
                "agent_name": "testbot",  # human identity
                "mailbox": "agent-testbot",  # bus endpoint — different
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            stub_ep = bus._endpoints_by_name["agent-testbot"].endpoint
            for _ in range(60):
                if any(
                    env.kind == "Event"
                    and isinstance(env.payload, EventPayload)
                    and env.payload.type == "HandoffReady"
                    for env in stub_ep.inbox
                ):
                    break
                await asyncio.sleep(0.05)

            ready_envs = [
                env
                for env in stub_ep.inbox
                if env.kind == "Event"
                and isinstance(env.payload, EventPayload)
                and env.payload.type == "HandoffReady"
            ]
            assert ready_envs, (
                "HandoffReady never reached the agent-testbot endpoint — worker "
                "likely tried to publish to the human identity 'testbot' instead "
                "of the bus endpoint 'agent-testbot'."
            )
            assert ready_envs[0].to == "agent-testbot"
            assert ready_envs[0].payload.data["agent_name"] == "testbot"
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_pepper_capital_p_identity_routes_to_lowercase_mailbox(
    tmp_path, monkeypatch
):
    # Pepper-shaped regression: real Pepper config has agent_name="Pepper"
    # (capitalized — her display identity, what she calls herself in SOUL.md
    # and IDENTITY.md) and bus endpoint "pepper" (lowercase — the routing
    # name registered with the daemon). Without the mailbox split, the
    # worker would publish to "Pepper" and ValueError on an unregistered
    # endpoint. This test locks in that the case-mismatched config works:
    # publish routes to lowercase "pepper", but the agent_name preserved in
    # the HandoffReady payload data stays capitalized "Pepper" so downstream
    # consumers (and Pepper herself when she reads list_pending) see her
    # identity unchanged.
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-pepper.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
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

            async def _fake_extract(self, req, transcript_text, job_id):
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-pepper",
                "event": "SessionEnd",
                "agent_name": "Pepper",  # capital P — display identity
                "mailbox": "pepper",  # lowercase — bus endpoint name
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            for _ in range(60):
                if any(
                    env.kind == "Event"
                    and isinstance(env.payload, EventPayload)
                    and env.payload.type == "HandoffReady"
                    for env in stub_ep.inbox
                ):
                    break
                await asyncio.sleep(0.05)

            ready_envs = [
                env
                for env in stub_ep.inbox
                if env.kind == "Event"
                and isinstance(env.payload, EventPayload)
                and env.payload.type == "HandoffReady"
            ]
            assert ready_envs, (
                "HandoffReady never reached the lowercase 'pepper' endpoint — "
                "worker likely tried to publish to capital-P 'Pepper' (the "
                "human identity) instead of the registered bus endpoint."
            )
            # Routing went to lowercase mailbox — what the bus needs to deliver:
            assert ready_envs[0].to == "pepper"
            # Identity in the payload preserved as capital-P — what Pepper
            # sees when she reads the event from her own inbox:
            assert ready_envs[0].payload.data["agent_name"] == "Pepper"
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_jobs_endpoint_rejects_transcript_outside_transcript_root(tmp_path):
    # transcript_path must live under transcript_root (default: ~/.claude/projects/),
    # not vault_root. Caller can override transcript_root explicitly. This test
    # locks in that an explicit transcript_root rejects a transcript that
    # escapes it — symmetric to the vault_root path-traversal check.
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    rogue_transcript = tmp_path / "elsewhere" / "transcript.jsonl"
    rogue_transcript.parent.mkdir(parents=True, exist_ok=True)
    rogue_transcript.write_text("{}", encoding="utf-8")

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
                "session_id": "session-rogue",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(vault_root / "handoff.md"),
                "handoff_status_path": str(vault_root / "handoff-status.json"),
                "transcript_path": str(rogue_transcript),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 403
            assert "escapes transcript_root" in resp.json()["error"]
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

    async def _fake_call(self, *, req, transcript_text, job_id):
        return expected

    monkeypatch.setattr(HandoffJobsEndpoint, "_call_agent_sdk", _fake_call)

    actual = await endpoint._extract_handoff(req, "transcript text", "test-job-id")
    assert actual == expected


@pytest.mark.asyncio
async def test_extract_handoff_propagates_exception_on_sdk_failure(monkeypatch):
    endpoint = HandoffJobsEndpoint(name="handoff-jobs")
    req = HandoffJobRequest(
        session_id="session-propagate",
        event="PreCompact",
        agent_name="pepper",
        vault_root="/vault",
        handoff_path="/vault/handoff.md",
        handoff_status_path="/vault/handoff-status.json",
        transcript_path="/vault/transcript.jsonl",
        requested_at=datetime.now(UTC),
    )

    async def _raise(*args, **kwargs):
        raise RuntimeError("sdk boom")

    monkeypatch.setattr(HandoffJobsEndpoint, "_call_agent_sdk", _raise)

    with pytest.raises(RuntimeError, match="sdk boom"):
        await endpoint._extract_handoff(req, "transcript text", "test-job-id")


@pytest.mark.asyncio
async def test_worker_retries_then_marks_failed(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-fail.jsonl"
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

    async def always_fail_extract(self, req, transcript_text, job_id):
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
                "transcript_root": str(transcript_root),
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


def test_derive_idempotency_key_includes_transcript_size(tmp_path):
    transcript_a = tmp_path / "a.jsonl"
    transcript_a.write_text("a" * 100, encoding="utf-8")
    transcript_b = tmp_path / "b.jsonl"
    transcript_b.write_text("a" * 200, encoding="utf-8")

    endpoint = HandoffJobsEndpoint(name="test")

    req_a = HandoffJobRequest(
        session_id="session-x",
        event="SessionEnd",
        agent_name="pepper",
        vault_root=str(tmp_path),
        handoff_path=str(tmp_path / "handoff.md"),
        handoff_status_path=str(tmp_path / "handoff-status.json"),
        transcript_path=str(transcript_a),
        transcript_root=str(tmp_path),
        requested_at=datetime.now(UTC),
    )
    req_b = req_a.model_copy(update={"transcript_path": str(transcript_b)})

    key_a = endpoint._derive_idempotency_key(req_a)
    key_b = endpoint._derive_idempotency_key(req_b)

    assert key_a != key_b


def test_derive_idempotency_key_stable_for_same_size(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("hello world", encoding="utf-8")

    endpoint = HandoffJobsEndpoint(name="test")

    req = HandoffJobRequest(
        session_id="session-x",
        event="SessionEnd",
        agent_name="pepper",
        vault_root=str(tmp_path),
        handoff_path=str(tmp_path / "handoff.md"),
        handoff_status_path=str(tmp_path / "handoff-status.json"),
        transcript_path=str(transcript),
        transcript_root=str(tmp_path),
        requested_at=datetime.now(UTC),
    )

    key_first = endpoint._derive_idempotency_key(req)
    key_second = endpoint._derive_idempotency_key(req)

    assert key_first == key_second


def test_derive_idempotency_key_handles_missing_transcript(tmp_path):
    endpoint = HandoffJobsEndpoint(name="test")

    req = HandoffJobRequest(
        session_id="session-x",
        event="SessionEnd",
        agent_name="pepper",
        vault_root=str(tmp_path),
        handoff_path=str(tmp_path / "handoff.md"),
        handoff_status_path=str(tmp_path / "handoff-status.json"),
        transcript_path=str(tmp_path / "does-not-exist.jsonl"),
        transcript_root=str(tmp_path),
        requested_at=datetime.now(UTC),
    )

    # Must not raise; must produce a deterministic key
    key_a = endpoint._derive_idempotency_key(req)
    key_b = endpoint._derive_idempotency_key(req)
    assert key_a == key_b
    assert isinstance(key_a, str)
    assert len(key_a) == 64  # sha256 hex


def test_read_transcript_tail_returns_whole_file_when_small(tmp_path):
    f = tmp_path / "small.jsonl"
    content = '{"a":1}\n{"b":2}\n'
    f.write_text(content, encoding="utf-8")

    result = _read_transcript_tail(f, max_bytes=1024, max_messages=100)
    assert result == content


def test_read_transcript_tail_caps_by_bytes_aligned_to_newline(tmp_path):
    f = tmp_path / "big.jsonl"
    lines = [f'{{"i":{i},"data":"{"x" * 80}"}}' for i in range(100)]
    content = "\n".join(lines) + "\n"
    f.write_text(content, encoding="utf-8")
    assert len(content.encode("utf-8")) > 1024

    result = _read_transcript_tail(f, max_bytes=1024, max_messages=10000)

    assert len(result.encode("utf-8")) <= 1024
    first_line = result.split("\n", 1)[0]
    parsed = json.loads(first_line)
    assert "i" in parsed
    assert result.endswith("\n")


def test_read_transcript_tail_caps_by_message_count(tmp_path):
    f = tmp_path / "many.jsonl"
    lines = [f'{{"i":{i}}}' for i in range(50)]
    content = "\n".join(lines) + "\n"
    f.write_text(content, encoding="utf-8")

    result = _read_transcript_tail(f, max_bytes=10**9, max_messages=10)
    result_lines = [ln for ln in result.split("\n") if ln]
    assert len(result_lines) == 10
    assert json.loads(result_lines[0]) == {"i": 40}
    assert json.loads(result_lines[-1]) == {"i": 49}


def test_read_transcript_tail_drops_partial_first_line_after_byte_seek(tmp_path):
    f = tmp_path / "big.jsonl"
    lines = [f'{{"line":{i},"x":"{"a" * 100}"}}' for i in range(20)]
    content = "\n".join(lines) + "\n"
    f.write_text(content, encoding="utf-8")

    result = _read_transcript_tail(f, max_bytes=250, max_messages=10000)

    for line in result.split("\n"):
        if line:
            json.loads(line)  # raises ValueError on partial JSON


def test_read_transcript_tail_handles_empty_file(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    result = _read_transcript_tail(f, max_bytes=1024, max_messages=100)
    assert result == ""


def test_read_transcript_tail_handles_file_with_no_trailing_newline(tmp_path):
    f = tmp_path / "no_trail.jsonl"
    content = '{"a":1}\n{"b":2}'
    f.write_text(content, encoding="utf-8")
    result = _read_transcript_tail(f, max_bytes=1024, max_messages=100)
    assert '{"a":1}' in result
    assert '{"b":2}' in result


@pytest.mark.asyncio
async def test_handoff_worker_uses_tail_for_oversized_transcripts(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-tail.jsonl"

    # Build a transcript larger than the tail bound so we can see truncation.
    lines = [f'{{"i":{i},"data":"{"x" * 200}"}}\n' for i in range(2000)]
    transcript_path.write_text("".join(lines), encoding="utf-8")

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
      transcript_tail_max_bytes: 4096
      transcript_tail_max_messages: 50
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    received_lengths: list[int] = []
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _capture_extract(self, req, transcript_text, *args, **kwargs):
                received_lengths.append(len(transcript_text.encode("utf-8")))
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _capture_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-tail",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            assert received_lengths, "_extract_handoff was not called"
            assert received_lengths[0] <= 4096
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


def test_summarize_stderr_returns_full_text_when_short():
    text = "first line\nlast line"
    result = HandoffJobsEndpoint._summarize_stderr(text, max_chars=500)
    assert "first line" in result
    assert "last line" in result


def test_summarize_stderr_truncates_with_ellipsis_when_long():
    long_middle = "middle\n" * 1000
    text = f"first line\n{long_middle}last line"
    result = HandoffJobsEndpoint._summarize_stderr(text, max_chars=80)
    assert len(result) <= 80
    assert "first line" in result
    assert "last line" in result
    assert "..." in result


def test_summarize_stderr_skips_empty_lines():
    text = "\n\nfirst real line\n\n\nlast real line\n\n"
    result = HandoffJobsEndpoint._summarize_stderr(text, max_chars=500)
    assert "first real line" in result
    assert "last real line" in result


def test_summarize_stderr_handles_empty_input():
    result = HandoffJobsEndpoint._summarize_stderr("", max_chars=500)
    assert result == ""


def test_capture_subprocess_stderr_extracts_from_chained_exception():
    inner = RuntimeError("subprocess died: file too large")
    outer = RuntimeError("Command failed with exit code 1")
    outer.__cause__ = inner
    result = HandoffJobsEndpoint._capture_subprocess_stderr(outer)
    assert "file too large" in result or "subprocess died" in result


def test_capture_subprocess_stderr_falls_back_to_repr_when_no_chain():
    exc = ValueError("nothing structured here")
    result = HandoffJobsEndpoint._capture_subprocess_stderr(exc)
    assert result
    assert "nothing structured here" in result


@pytest.mark.asyncio
async def test_handoff_worker_marks_failed_when_sdk_raises(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-marked.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
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
      retry_backoff_seconds: 0.0
      jobs_log_dir: {tmp_path / "jobs_logs"}
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

            async def _always_fail(self, *, req, transcript_text):
                raise RuntimeError("subprocess stderr: kaboom")

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _always_fail)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-marked",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "failed"
            assert status.get("error")
            assert "kaboom" in status["error"] or "summarizer failed" in status["error"]
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_worker_does_not_overwrite_handoff_md_when_failed(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-preserve.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    pre_existing = "# Last good handoff\n\n- session: prior\n- decision: keep this content\n"
    handoff_path.write_text(pre_existing, encoding="utf-8")

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
      retry_backoff_seconds: 0.0
      jobs_log_dir: {tmp_path / "jobs_logs"}
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

            async def _always_fail(self, *, req, transcript_text):
                raise RuntimeError("subprocess stderr: kaboom")

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _always_fail)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-preserve",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            assert handoff_path.read_text(encoding="utf-8") == pre_existing
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_worker_truncates_stderr_in_status_error_field(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-trunc.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
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
      retry_backoff_seconds: 0.0
      jobs_log_dir: {tmp_path / "jobs_logs"}
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

            huge_msg = "first line of error\n" + ("noise " * 5000) + "\nlast line of error"

            async def _huge_fail(self, *, req, transcript_text):
                raise RuntimeError(huge_msg)

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _huge_fail)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-trunc",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "failed"
            err = status["error"]
            # Bound: len("summarizer failed: ") + max_chars(500) = 519. Allow 1 char of slack.
            assert len(err) <= 520
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_worker_writes_per_job_log_on_sdk_failure(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-fail.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"
    jobs_log_dir = tmp_path / "jobs_logs"

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
      jobs_log_dir: {jobs_log_dir}
      retry_backoff_seconds: 0.0
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

            async def _failing_inner(self, *, req, transcript_text):
                inner = RuntimeError("subprocess stderr: transcript too large")
                outer = RuntimeError("Command failed with exit code 1")
                outer.__cause__ = inner
                raise outer

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _failing_inner)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-fail",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            log_file = jobs_log_dir / f"{job_id}.log"
            assert log_file.exists(), f"per-job log not written at {log_file}"
            log_content = log_file.read_text(encoding="utf-8")
            assert "transcript too large" in log_content or "exit code 1" in log_content
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_post_job_dedupes_when_transcript_unchanged(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-dedupe.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8"
    )
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
    extract_calls = 0
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _counting_extract(self, req, transcript_text, *args, **kwargs):
                nonlocal extract_calls
                extract_calls += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _counting_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-dedupe",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_first = await client.post(url, json=payload)
                resp_second = await client.post(url, json=payload)

            assert resp_first.status_code == 202
            assert resp_second.status_code == 202
            job_first = resp_first.json()["job_id"]
            job_second = resp_second.json()["job_id"]
            # This dedup-contract stability check would also pass with the
            # OLD key formula (no transcript_size). It proves the rapid-dupe
            # protection survives the #42 fix; the bug-fix proof is in
            # test_post_job_creates_new_job_when_transcript_grows.
            assert job_first == job_second

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            assert extract_calls == 1  # worker ran once, not twice
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_post_job_creates_new_job_when_transcript_grows(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-grow.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"first"}}\n', encoding="utf-8"
    )
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
    extract_calls = 0
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _counting_extract(self, req, transcript_text, *args, **kwargs):
                nonlocal extract_calls
                extract_calls += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _counting_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-grow",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_first = await client.post(url, json=payload)

            # wait for first job to complete so its status becomes ready
            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            # simulate conversation activity by appending to the transcript
            with transcript_path.open("a", encoding="utf-8") as fh:
                fh.write('{"message":{"role":"assistant","content":"second"}}\n')

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_second = await client.post(url, json=payload)

            assert resp_first.status_code == 202
            assert resp_second.status_code == 202
            job_first = resp_first.json()["job_id"]
            job_second = resp_second.json()["job_id"]
            assert job_first != job_second  # transcript grew = new job

            for _ in range(40):
                if extract_calls >= 2:
                    break
                await asyncio.sleep(0.05)

            assert extract_calls == 2, f"expected 2 extract calls, got {extract_calls}"
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


# ----------------------------------------------------------------------
# Issue #136 — publish-side dedupe for SessionEnd HandoffReady storms.
# ----------------------------------------------------------------------


async def _wait_for_extract_count(target: int, counter: dict[str, int], timeout: float = 3.0) -> None:
    """Poll until ``counter['count'] >= target`` or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while counter["count"] < target:
        if asyncio.get_running_loop().time() > deadline:
            return
        await asyncio.sleep(0.05)


def _ready_envelopes(stub_ep) -> list:
    return [
        env
        for env in stub_ep.inbox
        if env.kind == "Event"
        and isinstance(env.payload, EventPayload)
        and env.payload.type == "HandoffReady"
    ]


def _failed_envelopes(stub_ep) -> list:
    return [
        env
        for env in stub_ep.inbox
        if env.kind == "Event"
        and isinstance(env.payload, EventPayload)
        and env.payload.type == "HandoffFailed"
    ]


@pytest.mark.asyncio
async def test_handoff_publish_dedupe_suppresses_consecutive_session_end(
    tmp_path, monkeypatch
):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8"
    )
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
    counter = {"count": 0}
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text, job_id):
                counter["count"] += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            base = {
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_a = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-a",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
                resp_b = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-b",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_a.status_code == 202
            assert resp_b.status_code == 202

            await _wait_for_extract_count(2, counter)
            # Give the worker one more loop iteration to publish.
            await asyncio.sleep(0.15)

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            ready = _ready_envelopes(stub_ep)
            assert len(ready) == 1, (
                f"expected 1 HandoffReady (second suppressed by dedupe), "
                f"got {len(ready)}"
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_publish_dedupe_window_resets(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8"
    )
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
      handoff_publish_dedupe_seconds: 0.05
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    counter = {"count": 0}
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text, job_id):
                counter["count"] += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            base = {
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_a = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-a",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_a.status_code == 202

            await _wait_for_extract_count(1, counter)
            # Sleep past the (very small) dedupe window.
            await asyncio.sleep(0.2)

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_b = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-b",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_b.status_code == 202

            await _wait_for_extract_count(2, counter)
            await asyncio.sleep(0.15)

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            ready = _ready_envelopes(stub_ep)
            assert len(ready) == 2, (
                f"expected 2 HandoffReady (window expired between posts), "
                f"got {len(ready)}"
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_publish_dedupe_zero_disables(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8"
    )
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
      handoff_publish_dedupe_seconds: 0.0
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    counter = {"count": 0}
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text, job_id):
                counter["count"] += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            base = {
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_a = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-a",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
                resp_b = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-b",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_a.status_code == 202
            assert resp_b.status_code == 202

            await _wait_for_extract_count(2, counter)
            await asyncio.sleep(0.15)

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            ready = _ready_envelopes(stub_ep)
            assert len(ready) == 2, (
                f"expected 2 HandoffReady (dedupe disabled with 0.0), "
                f"got {len(ready)}"
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_publish_dedupe_precompact_never_suppressed(
    tmp_path, monkeypatch
):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8"
    )
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
    counter = {"count": 0}
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text, job_id):
                counter["count"] += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            base = {
                "event": "PreCompact",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_a = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-a",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
                resp_b = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-b",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_a.status_code == 202
            assert resp_b.status_code == 202

            await _wait_for_extract_count(2, counter)
            await asyncio.sleep(0.15)

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            ready = _ready_envelopes(stub_ep)
            assert len(ready) == 2, (
                f"expected 2 HandoffReady (PreCompact never suppressed), "
                f"got {len(ready)}"
            )

            # Now also confirm PreCompact didn't poison the SessionEnd
            # bookkeeping slot — a subsequent SessionEnd within the window
            # should still get published once.
            base_se = {**base, "event": "SessionEnd"}
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_c = await client.post(
                    url,
                    json={
                        **base_se,
                        "session_id": "session-c",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_c.status_code == 202
            await _wait_for_extract_count(3, counter)
            await asyncio.sleep(0.15)
            ready_after = _ready_envelopes(stub_ep)
            assert len(ready_after) == 3, (
                f"expected 3 HandoffReady (PreCompact slots don't block "
                f"SessionEnd), got {len(ready_after)}"
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_publish_dedupe_independent_per_mailbox(
    tmp_path, monkeypatch
):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8"
    )
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
  - type: builtin.stub
    name: wren
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    counter = {"count": 0}
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _fake_extract(self, req, transcript_text, job_id):
                counter["count"] += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            base = {
                "event": "SessionEnd",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_a = await client.post(
                    url,
                    json={
                        **base,
                        "agent_name": "pepper",
                        "session_id": "session-pepper",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
                resp_b = await client.post(
                    url,
                    json={
                        **base,
                        "agent_name": "wren",
                        "session_id": "session-wren",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_a.status_code == 202
            assert resp_b.status_code == 202

            await _wait_for_extract_count(2, counter)
            await asyncio.sleep(0.15)

            pepper_ep = bus._endpoints_by_name["pepper"].endpoint
            wren_ep = bus._endpoints_by_name["wren"].endpoint
            assert len(_ready_envelopes(pepper_ep)) == 1, (
                "pepper should receive exactly 1 HandoffReady"
            )
            assert len(_ready_envelopes(wren_ep)) == 1, (
                "wren should receive exactly 1 HandoffReady — cross-mailbox "
                "dedupe is a bug"
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_publish_dedupe_failed_not_suppressed(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8"
    )
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
      max_attempts: 1
      retry_backoff_seconds: 0.0
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    counter = {"count": 0}
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _always_fail(self, req, transcript_text, job_id):
                counter["count"] += 1
                raise RuntimeError("forced extract failure")

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _always_fail)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            base = {
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_a = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-a",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
                resp_b = await client.post(
                    url,
                    json={
                        **base,
                        "session_id": "session-b",
                        "requested_at": datetime.now(UTC).isoformat(),
                    },
                )
            assert resp_a.status_code == 202
            assert resp_b.status_code == 202

            await _wait_for_extract_count(2, counter)
            await asyncio.sleep(0.15)

            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            failed = _failed_envelopes(stub_ep)
            assert len(failed) == 2, (
                f"expected 2 HandoffFailed (failures are signal, not noise), "
                f"got {len(failed)}"
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()

