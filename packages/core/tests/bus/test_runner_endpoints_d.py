"""Tests for endpoints.d/ conf.d-style merging in build_bus_from_config."""

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
async def test_endpoints_d_malformed_fragment_raises_with_filename():
    """A fragment whose `endpoints:` is not a list must error naming the file."""
    config_path = FIXTURES / "endpoints_d_malformed" / "main.yaml"

    with pytest.raises(BusBootError, match="bad.yaml"):
        await build_bus_from_config(config_path)


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
