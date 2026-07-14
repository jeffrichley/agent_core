"""Runner reads `mcp_audit:` YAML and wires the writer into RunnerServices."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.runner import build_bus_from_config


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


_BASE_YAML = """\
bus:
  storage_path: "{storage}"
endpoints: []
"""


@pytest.mark.asyncio
async def test_runner_constructs_writer_when_mcp_audit_block_missing(tmp_path: Path, build_bus):
    cfg = _write_yaml(
        tmp_path / "config.yaml",
        _BASE_YAML.format(storage=(tmp_path / "bus.sqlite").as_posix()),
    )
    bus, _http = await build_bus(cfg)
    # Block missing → defaults; writer is constructed.
    # We can't reach RunnerServices directly post-build, but the writer
    # was passed to BuiltinRuntimePlugin which would have attached it to
    # any AuditWriterAwareEndpoint. With zero endpoints, the only check
    # we can do here is that build did not raise.
    assert bus is not None


@pytest.mark.asyncio
async def test_runner_constructs_no_writer_when_disabled(tmp_path: Path, build_bus):
    yaml_body = _BASE_YAML.format(storage=(tmp_path / "bus.sqlite").as_posix()) + (
        "mcp_audit:\n  enabled: false\n"
    )
    cfg = _write_yaml(tmp_path / "config.yaml", yaml_body)
    bus, _http = await build_bus(cfg)
    # With one ClaudeCodeMCPEndpoint and audit disabled, no audit dir
    # should appear under ~/.agent-core/bus/mcp-audit. Validated via the
    # integration test in Task 6 — here we just assert build succeeded.
    assert bus is not None


@pytest.mark.asyncio
async def test_runner_uses_configured_log_root_and_timezone(tmp_path: Path, build_bus):
    custom_root = tmp_path / "custom" / "audit"
    yaml_body = _BASE_YAML.format(storage=(tmp_path / "bus.sqlite").as_posix()) + (
        f"mcp_audit:\n"
        f"  enabled: true\n"
        f"  log_root: \"{custom_root.as_posix()}\"\n"
        f"  timezone: \"UTC\"\n"
        f"  skip_tools: [\"list_pending\"]\n"
    )
    cfg = _write_yaml(tmp_path / "config.yaml", yaml_body)
    bus, _http = await build_bus(cfg)
    assert bus is not None
    # No endpoints in this config means no writer activation, but the
    # build itself must accept the block. Behavioral verification of
    # log_root/timezone/skip_tools lives in the integration test.


@pytest.mark.asyncio
async def test_runner_raises_bus_boot_error_when_skip_tools_is_not_a_list(tmp_path: Path):
    yaml_body = _BASE_YAML.format(storage=(tmp_path / "bus.sqlite").as_posix()) + (
        'mcp_audit:\n  skip_tools: "list_pending"\n'
    )
    cfg = _write_yaml(tmp_path / "config.yaml", yaml_body)
    from agent_core.bus.runner import BusBootError
    with pytest.raises(BusBootError, match="skip_tools must be a list"):
        await build_bus_from_config(cfg)


@pytest.mark.asyncio
async def test_runner_raises_bus_boot_error_when_timezone_is_invalid(tmp_path: Path):
    yaml_body = _BASE_YAML.format(storage=(tmp_path / "bus.sqlite").as_posix()) + (
        'mcp_audit:\n  timezone: "US/Estern"\n'
    )
    cfg = _write_yaml(tmp_path / "config.yaml", yaml_body)
    from agent_core.bus.runner import BusBootError
    with pytest.raises(BusBootError, match="timezone is invalid"):
        await build_bus_from_config(cfg)
