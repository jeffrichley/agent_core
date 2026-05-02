from __future__ import annotations

import asyncio
import json

import pytest

from agent_core.bus.envelope import EventPayload
from agent_core.bus.runner import build_bus_from_config
from agent_core.hooks.tools.handoff_writer import HandoffWriter


@pytest.mark.asyncio
async def test_enqueue_hook_to_daemon_end_to_end(tmp_path, monkeypatch):
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

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _fake_extract)

            hook = HandoffWriter()
            result = await asyncio.to_thread(
                hook.execute,
                event="SessionEnd",
                hook_input={"session_id": "session-e2e", "transcript_path": str(transcript_path)},
                params={
                    "agent_name": "pepper",
                    "output_path": str(handoff_path),
                    "handoff_status_path": str(status_path),
                    "vault_root": str(vault_root),
                    "handoff_jobs_url": f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs",
                },
            )
            assert result.heading == "Handoff Job Enqueued"

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "ready"
            assert status["session_id"] == "session-e2e"
            assert handoff_path.exists()

            job_id = status["job_id"]
            stub_ep = bus._endpoints_by_name["pepper"].endpoint
            for _ in range(60):
                if any(
                    env.kind == "Event"
                    and isinstance(env.payload, EventPayload)
                    and env.payload.type == "HandoffReady"
                    and env.payload.data.get("job_id") == job_id
                    for env in stub_ep.inbox
                ):
                    break
                await asyncio.sleep(0.05)

            assert any(
                env.kind == "Event"
                and isinstance(env.payload, EventPayload)
                and env.payload.type == "HandoffReady"
                and env.payload.data.get("job_id") == job_id
                for env in stub_ep.inbox
            )
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
