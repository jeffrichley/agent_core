"""Tests for DiscordEndpoint access-config hot-reload (issue #190)."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import FakeBusHandle, FakeDiscordClient


async def _start(monkeypatch, tmp_path, *, interval: float = 0.05, path_json: dict | None = None):
    """Start a DiscordEndpoint with an optional access config file at tmp_path."""
    access_path = None
    if path_json is not None:
        p = tmp_path / "access.json"
        p.write_text(json.dumps(path_json), encoding="utf-8")
        access_path = p
    monkeypatch.setenv("X_TOK", "tok")
    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-test",
        token_env="X_TOK",
        access_config_path=access_path,
        access_config_reload_interval=interval,
        _client_factory=lambda **kw: FakeDiscordClient(**kw),
    )
    await ep.start(FakeBusHandle())
    return ep, access_path


@pytest.mark.asyncio
async def test_access_reload_picks_up_added_channel(monkeypatch, tmp_path):
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        p.write_text(json.dumps({"channels": {"100": {}, "200": {}}}), encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        assert "200" in ep._access.channels
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_picks_up_removed_channel(monkeypatch, tmp_path):
    initial = {"channels": {"100": {}, "200": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        p.write_text(json.dumps({"channels": {"100": {}}}), encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        assert "200" not in ep._access.channels
        assert "100" in ep._access.channels
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_keeps_config_on_malformed_json(monkeypatch, tmp_path):
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        original_channels = dict(ep._access.channels)
        # Simulate a mid-edit partial write
        p.write_text("{bad json", encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        assert ep._access.channels == original_channels
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_warns_on_malformed_json(monkeypatch, tmp_path, caplog):
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        p.write_text("{bad json", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            await asyncio.sleep(0.05 + 0.1)
        assert any("access config reload" in rec.message for rec in caplog.records)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_disabled_when_interval_zero(monkeypatch, tmp_path):
    initial = {"channels": {"100": {}}}
    ep, _ = await _start(monkeypatch, tmp_path, interval=0, path_json=initial)
    try:
        assert ep._access_reload_task is None
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_disabled_when_no_path(monkeypatch, tmp_path):
    ep, _ = await _start(monkeypatch, tmp_path, path_json=None)
    try:
        assert ep._access_reload_task is None
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_task_cancelled_on_stop(monkeypatch, tmp_path):
    initial = {"channels": {"100": {}}}
    ep, _ = await _start(monkeypatch, tmp_path, path_json=initial)
    assert ep._access_reload_task is not None
    await ep.stop()
    assert ep._access_reload_task is None


@pytest.mark.asyncio
async def test_access_reload_keeps_config_on_schema_invalid_json(monkeypatch, tmp_path):
    """Valid JSON but invalid AccessConfig schema keeps previous config and task alive."""
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        original_channels = dict(ep._access.channels)
        # "channels" is an int: valid JSON, invalid for AccessConfig (dict(...) raises TypeError)
        p.write_text(json.dumps({"channels": 5}), encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        # Config unchanged
        assert ep._access.channels == original_channels
        # Task still alive — the loop was NOT killed
        assert ep._access_reload_task is not None
        assert not ep._access_reload_task.done()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_warns_on_schema_invalid_json(monkeypatch, tmp_path, caplog):
    """Valid JSON but invalid AccessConfig schema emits a WARNING log."""
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        p.write_text(json.dumps({"channels": 5}), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            await asyncio.sleep(0.05 + 0.1)
        assert any("access config reload" in rec.message for rec in caplog.records)
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_recovers_after_schema_invalid_json(monkeypatch, tmp_path):
    """Loop recovers and picks up a subsequent valid write after a schema-invalid file."""
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        # Write schema-invalid JSON (valid JSON, but channels must be a dict)
        p.write_text(json.dumps({"channels": 5}), encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        # Now write a valid config — loop must NOT have poisoned the mtime on error
        p.write_text(json.dumps({"channels": {"200": {}}}), encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        # Recovery: new channel picked up
        assert "200" in ep._access.channels
    finally:
        await ep.stop()
