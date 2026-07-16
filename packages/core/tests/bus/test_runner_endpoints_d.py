"""Tests for endpoints.d/ conf.d-style merging in build_bus_from_config."""

import logging
from pathlib import Path

import pytest

from agent_core.bus.runner import BusBootError, build_bus_from_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_endpoints_d_happy_path_merges_alphabetically(build_bus):
    """All endpoints from main + endpoints.d/*.yaml are present in the built bus.

    Fragments load in sorted-glob order (a.yaml before b.yaml), main first.
    """
    config_path = FIXTURES / "endpoints_d" / "main.yaml"

    bus, _http = await build_bus(config_path)
    try:
        endpoint_names = {ep.name for ep in bus._endpoints()}
        assert endpoint_names == {"main-stub", "fragment-a-stub", "fragment-b-stub"}
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_endpoints_d_collision_raises_loudly():
    """A fragment endpoint with the same name as one already loaded must fail loudly."""
    config_path = FIXTURES / "endpoints_d_collision" / "main.yaml"

    with pytest.raises(BusBootError, match="dup-stub"):
        await build_bus_from_config(config_path)


@pytest.mark.asyncio
async def test_endpoints_d_malformed_fragment_quarantined_boot_continues(build_bus, caplog):
    """A fragment whose `endpoints:` is not a list is quarantined; boot continues."""
    config_path = FIXTURES / "endpoints_d_malformed" / "main.yaml"

    with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
        bus, _http = await build_bus(config_path)
    try:
        assert any("bad.yaml" in r.message for r in caplog.records)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_yaml_parse_error_quarantines_fragment_boot_continues(build_bus, tmp_path, caplog):
    """A YAML-broken fragment is quarantined; other fragments and main endpoints load."""
    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
        'endpoints:\n  - type: builtin.stub\n    name: main-stub\n'
    )
    frag_dir = tmp_path / "endpoints.d"
    frag_dir.mkdir()
    (frag_dir / "broken.yaml").write_text(": invalid: yaml syntax [[[\n")
    (frag_dir / "good.yaml").write_text(
        'endpoints:\n  - type: builtin.stub\n    name: good-frag-stub\n'
    )
    with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
        bus, _http = await build_bus(main_yaml)
    try:
        names = {ep.name for ep in bus._endpoints()}
        assert "main-stub" in names
        assert "good-frag-stub" in names
        assert any("broken.yaml" in r.message for r in caplog.records)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_entry_missing_type_skipped_sibling_loads(build_bus, tmp_path, caplog):
    """Entry missing 'type' is skipped; sibling entries load."""
    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
        'endpoints:\n'
        '  - name: no-type-ep\n    params: {}\n'
        '  - type: builtin.stub\n    name: good-ep\n'
    )
    with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
        bus, _http = await build_bus(main_yaml)
    try:
        names = {ep.name for ep in bus._endpoints()}
        assert "good-ep" in names
        assert "no-type-ep" not in names
        assert any("no-type-ep" in r.message or "'type'" in r.message for r in caplog.records)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_entry_missing_name_skipped_sibling_loads(build_bus, tmp_path, caplog):
    """Entry missing 'name' is skipped; sibling entries load."""
    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
        'endpoints:\n'
        '  - type: builtin.stub\n    params: {}\n'
        '  - type: builtin.stub\n    name: good-ep\n'
    )
    with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
        bus, _http = await build_bus(main_yaml)
    try:
        names = {ep.name for ep in bus._endpoints()}
        assert "good-ep" in names
        assert any("'name'" in r.message or "missing" in r.message for r in caplog.records)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_entry_unknown_type_skipped_sibling_loads(build_bus, tmp_path, caplog):
    """Entry with unknown type is skipped; sibling entries load."""
    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
        'endpoints:\n'
        '  - type: no.such.endpoint.Type\n    name: bad-ep\n'
        '  - type: builtin.stub\n    name: good-ep\n'
    )
    with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
        bus, _http = await build_bus(main_yaml)
    try:
        names = {ep.name for ep in bus._endpoints()}
        assert "good-ep" in names
        assert "bad-ep" not in names
        assert any(
            "bad-ep" in r.message or "no.such.endpoint.Type" in r.message
            for r in caplog.records
        )
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_entry_construction_failure_skipped_sibling_loads(
    build_bus, tmp_path, monkeypatch, caplog
):
    """Entry whose constructor raises is skipped; sibling entries load."""
    from typing import Any

    class _FailEndpoint:
        def __init__(self, *, name: str, **_: Any) -> None:
            raise RuntimeError("intentional constructor failure")

    class _OkEndpoint:
        def __init__(self, *, name: str, **_: Any) -> None:
            self.name = name

        async def start(self, bus) -> None: ...
        async def deliver(self, envelope) -> None: ...
        async def stop(self) -> None: ...

    class _HookImpl:
        @staticmethod
        def register_endpoint_types() -> dict:
            return {"test.fail": _FailEndpoint, "test.ok": _OkEndpoint}

        @staticmethod
        def validate_config(*, raw_config: dict) -> None:
            return None

        @staticmethod
        def register_bus_hook_types() -> dict:
            return {}

        @staticmethod
        def register_hook_tool_types() -> dict:
            return {}

        @staticmethod
        def configure_endpoint_instance(*, instance, endpoint_name, endpoint_config, services):
            return None

        @staticmethod
        def configure_bus_hook_instance(*, instance, stage, hook_config, services):
            return None

        @staticmethod
        def wire_endpoints_after_registration(*, endpoints, raw_endpoint_configs, services):
            return None

        @staticmethod
        def reserved_endpoint_params() -> list:
            return []

        @staticmethod
        def register_bus_log_projectors() -> dict:
            return {}

        @staticmethod
        def register_cli_subapps(app) -> None:
            return None

        @staticmethod
        def register_envelope_renderers() -> dict:
            return {}

    class _FakePM:
        hook = _HookImpl()

    monkeypatch.setattr("agent_core.bus.runner.create_plugin_manager", lambda: _FakePM())

    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        'http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n'
        'endpoints:\n'
        '  - type: test.fail\n    name: will-fail\n'
        '  - type: test.ok\n    name: good-ep\n'
    )
    with caplog.at_level(logging.ERROR, logger="agent_core.bus.runner"):
        bus, _http = await build_bus(main_yaml)
    try:
        names = {ep.name for ep in bus._endpoints()}
        assert "good-ep" in names
        assert "will-fail" not in names
        assert any("will-fail" in r.message for r in caplog.records)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_no_endpoints_d_dir_is_silent_noop(tmp_path, build_bus):
    """If no endpoints.d/ subdir exists alongside the main yaml, no error, no fragments loaded."""
    main_yaml = tmp_path / "agent_core.yaml"
    main_yaml.write_text(
        'bus:\n  storage_path: ":memory:"\n'
        "http:\n  bind_host: 127.0.0.1\n  bind_port: 0\n"
        "endpoints:\n"
        "  - type: builtin.stub\n"
        "    name: only-stub\n"
        '    description: "Solo"\n'
    )

    bus, _http = await build_bus(main_yaml)
    try:
        assert {ep.name for ep in bus._endpoints()} == {"only-stub"}
    finally:
        await bus.stop()
