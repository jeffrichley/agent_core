"""Runner-level integration: writer is shared across endpoints; disabled emits nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

_TWO_ENDPOINT_YAML = """\
bus:
  storage_path: "{storage}"
mcp_audit:
  enabled: true
  log_root: "{audit_root}"
  timezone: "UTC"
  skip_tools: []
endpoints:
  - name: pepper
    type: builtin.claude_code_mcp
    params:
      mount: "/mcp/pepper"
  - name: testbot
    type: builtin.claude_code_mcp
    params:
      mount: "/mcp/testbot"
"""


_DISABLED_YAML = """\
bus:
  storage_path: "{storage}"
mcp_audit:
  enabled: false
  log_root: "{audit_root}"
endpoints:
  - name: pepper
    type: builtin.claude_code_mcp
    params:
      mount: "/mcp/pepper"
"""


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_audit_writer_is_singleton_across_endpoints(tmp_path: Path, build_bus):
    storage = tmp_path / "bus.sqlite"
    audit_root = tmp_path / "audit"
    cfg = _write(
        tmp_path / "config.yaml",
        _TWO_ENDPOINT_YAML.format(storage=storage.as_posix(), audit_root=audit_root.as_posix()),
    )
    bus, _http = await build_bus(cfg)
    await bus.start()
    try:
        endpoints = list(bus._endpoints_by_name.values())
        pepper = next(s.endpoint for s in endpoints if s.endpoint.name == "pepper")
        testbot = next(s.endpoint for s in endpoints if s.endpoint.name == "testbot")

        async with Client(pepper._mcp) as p_client:
            await p_client.call_tool("list_endpoints", {})
        async with Client(testbot._mcp) as t_client:
            await t_client.call_tool("list_endpoints", {})
    finally:
        await bus.stop()

    files = list(audit_root.glob("*.jsonl"))
    assert len(files) == 1, f"expected one shared daily file, got {[f.name for f in files]}"
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    endpoints_seen = {r["endpoint"] for r in rows}
    assert endpoints_seen == {"pepper", "testbot"}


@pytest.mark.asyncio
async def test_audit_disabled_emits_nothing(tmp_path: Path, build_bus):
    storage = tmp_path / "bus.sqlite"
    audit_root = tmp_path / "audit"
    cfg = _write(
        tmp_path / "config.yaml",
        _DISABLED_YAML.format(storage=storage.as_posix(), audit_root=audit_root.as_posix()),
    )
    bus, _http = await build_bus(cfg)
    await bus.start()
    try:
        endpoints = list(bus._endpoints_by_name.values())
        pepper = next(s.endpoint for s in endpoints if s.endpoint.name == "pepper")
        async with Client(pepper._mcp) as client:
            await client.call_tool("list_endpoints", {})
    finally:
        await bus.stop()

    # Audit disabled → no writer constructed → directory never created.
    assert not audit_root.exists(), f"audit_root must not exist, found: {list(audit_root.iterdir())}"
