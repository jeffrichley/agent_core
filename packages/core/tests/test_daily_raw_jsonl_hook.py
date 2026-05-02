"""Tests for DailyRawJsonlHook — bus traffic → vault daily/raw/*.jsonl."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec
from agent_core.bus.envelope import (
    AcknowledgmentPayload,
    Envelope,
    EventPayload,
    TextMessagePayload,
)
from agent_core.bus_hooks.daily_raw_jsonl import DailyRawJsonlHook


class _Echo:
    def __init__(self, name="x"):
        self.name = name
        self._handle = None

    async def start(self, bus):
        self._handle = bus

    async def deliver(self, envelope: Envelope) -> None:
        await self._handle.ack(envelope.id)

    async def stop(self) -> None:
        pass


def _text_env(text: str, **meta) -> Envelope:
    return Envelope(
        id="e1",
        correlation_id="corr-1",
        from_="discord",
        to="pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text=text),
        metadata=meta,
        created_at=datetime(2099, 6, 15, 14, 30, 0, tzinfo=UTC),
    )


@pytest.fixture
async def bus_with_hook(tmp_path: Path):
    vault = tmp_path / "Memory"
    vault.mkdir(parents=True, exist_ok=True)
    hook = DailyRawJsonlHook(str(vault), timezone="UTC")
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    b.register(EndpointSpec(endpoint=_Echo("pepper")))
    b.register_hook("pre_publish", BusHookSpec(hook=hook, params={}))
    await b.start()
    yield b, vault
    await b.stop()


@pytest.mark.asyncio
async def test_pre_publish_appends_jsonl_row(bus_with_hook, tmp_path: Path):
    bus, vault = bus_with_hook
    fixed = tmp_path / "fixed.jsonl"

    def _fixed_daily_path(_v: Path, _d: str, _tz: str) -> Path:
        return fixed

    with patch(
        "agent_core.bus_hooks.daily_raw_jsonl._daily_log_path",
        side_effect=_fixed_daily_path,
    ):
        ep = bus._endpoints_by_name["pepper"].endpoint
        await ep._handle.publish(_text_env("hello from Discord"))

    lines = fixed.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["dir"] == "bus"
    assert row["src"] == "bus-TextMessage"
    assert row["cid"] == "corr-1"
    # BusHandle.publish stamps ``from_`` with the publishing endpoint name.
    assert row["sender"] == "pepper"
    assert row["content"] == "hello from Discord"
    assert row["ts"].startswith("2099-06-15")


@pytest.mark.asyncio
async def test_skips_acknowledgment_by_default(tmp_path: Path):
    vault = tmp_path / "Memory"
    vault.mkdir(parents=True, exist_ok=True)
    hook = DailyRawJsonlHook(str(vault), timezone="UTC")
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    b.register(EndpointSpec(endpoint=_Echo("a")))
    b.register(EndpointSpec(endpoint=_Echo("b")))
    b.register_hook("pre_publish", BusHookSpec(hook=hook, params={}))
    await b.start()
    fixed = tmp_path / "out.jsonl"
    try:

        def _fixed_daily_path(_v: Path, _d: str, _tz: str) -> Path:
            return fixed

        with patch(
            "agent_core.bus_hooks.daily_raw_jsonl._daily_log_path",
            side_effect=_fixed_daily_path,
        ):
            ep = b._endpoints_by_name["b"].endpoint
            ack = Envelope(
                id="ack1",
                correlation_id="c1",
                from_="a",
                to="b",
                kind="Acknowledgment",
                payload=AcknowledgmentPayload(of="e0"),
                created_at=datetime(2099, 1, 1, tzinfo=UTC),
            )
            await ep._handle.publish(ack)
    finally:
        await b.stop()

    assert not fixed.exists()


@pytest.mark.asyncio
async def test_scheduler_metadata_sets_src(tmp_path: Path):
    vault = tmp_path / "Memory"
    vault.mkdir(parents=True, exist_ok=True)
    hook = DailyRawJsonlHook(str(vault), timezone="UTC")
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    b.register(EndpointSpec(endpoint=_Echo("pepper")))
    b.register_hook("pre_publish", BusHookSpec(hook=hook, params={}))
    await b.start()
    fixed = tmp_path / "sched.jsonl"
    try:

        def _fixed_daily_path(_v: Path, _d: str, _tz: str) -> Path:
            return fixed

        with patch(
            "agent_core.bus_hooks.daily_raw_jsonl._daily_log_path",
            side_effect=_fixed_daily_path,
        ):
            ep = b._endpoints_by_name["pepper"].endpoint
            await ep._handle.publish(
                _text_env("run nightly", scheduler_job="heartbeat-tick"),
            )
    finally:
        await b.stop()

    row = json.loads(fixed.read_text(encoding="utf-8").strip())
    assert row["src"] == "scheduler"
    assert row["content"] == "run nightly"


@pytest.mark.asyncio
async def test_skip_content_substrings(tmp_path: Path):
    vault = tmp_path / "Memory"
    vault.mkdir(parents=True, exist_ok=True)
    hook = DailyRawJsonlHook(
        str(vault),
        timezone="UTC",
        skip_content_substrings=["HEARTBEAT tick"],
    )
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    b.register(EndpointSpec(endpoint=_Echo("pepper")))
    b.register_hook("pre_publish", BusHookSpec(hook=hook, params={}))
    await b.start()
    fixed = tmp_path / "filt.jsonl"
    try:

        def _fixed_daily_path(_v: Path, _d: str, _tz: str) -> Path:
            return fixed

        with patch(
            "agent_core.bus_hooks.daily_raw_jsonl._daily_log_path",
            side_effect=_fixed_daily_path,
        ):
            ep = b._endpoints_by_name["pepper"].endpoint
            await ep._handle.publish(_text_env("HEARTBEAT tick ignored"))
    finally:
        await b.stop()

    assert not fixed.exists()


@pytest.mark.asyncio
async def test_pre_deliver_no_op_when_pre_publish_only(tmp_path: Path):
    vault = tmp_path / "Memory"
    vault.mkdir(parents=True, exist_ok=True)
    hook = DailyRawJsonlHook(str(vault), timezone="UTC")
    b = Bus(BusConfig(storage_path=tmp_path / "bus.sqlite"))
    b.register(EndpointSpec(endpoint=_Echo("pepper")))
    b.register_hook("pre_deliver", BusHookSpec(hook=hook, params={}))
    await b.start()
    fixed = tmp_path / "none.jsonl"
    try:

        def _fixed_daily_path(_v: Path, _d: str, _tz: str) -> Path:
            return fixed

        with patch(
            "agent_core.bus_hooks.daily_raw_jsonl._daily_log_path",
            side_effect=_fixed_daily_path,
        ):
            ep = b._endpoints_by_name["pepper"].endpoint
            await ep._handle.publish(_text_env("only pre_publish logs"))
    finally:
        await b.stop()

    assert not fixed.exists()


@pytest.mark.asyncio
async def test_event_payload_serialized(bus_with_hook, tmp_path: Path):
    bus, _vault = bus_with_hook
    fixed = tmp_path / "evt.jsonl"

    def _fixed_daily_path(_v: Path, _d: str, _tz: str) -> Path:
        return fixed

    with patch(
        "agent_core.bus_hooks.daily_raw_jsonl._daily_log_path",
        side_effect=_fixed_daily_path,
    ):
        ep = bus._endpoints_by_name["pepper"].endpoint
        env = Envelope(
            id="ev1",
            correlation_id="c-ev",
            from_="relay",
            to="pepper",
            kind="Event",
            payload=EventPayload(type="ChannelRelay", data={"channel": "dev"}),
            metadata={"channel_relay": True, "daily_dir": "in"},
            created_at=datetime(2099, 7, 1, tzinfo=UTC),
        )
        await ep._handle.publish(env)

    row = json.loads(fixed.read_text(encoding="utf-8").strip())
    assert row["dir"] == "in"
    assert row["src"] == "channel-relay"
    assert "ChannelRelay" in row["content"]
