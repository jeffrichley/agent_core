"""Gather engine: async-concurrent fetcher execution with per-fetcher
timeout, namespace merging, _errors capture, default 5min timeout."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from agent_core_briefs.engine import FetcherInvocation, gather_context


class _Fixed:
    def __init__(self, type_id: str, namespace: str, payload: dict):
        self.type_id = type_id
        self.namespace = namespace
        self._payload = payload

    async def fetch(self, config, when):
        return dict(self._payload)


class _Slow:
    def __init__(self, type_id: str, namespace: str, delay_s: float):
        self.type_id = type_id
        self.namespace = namespace
        self._delay = delay_s

    async def fetch(self, config, when):
        await asyncio.sleep(self._delay)
        return {"slow": "ok"}


class _Boom:
    type_id = "test.boom"
    namespace = "boom"

    async def fetch(self, config, when):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_single_fetcher_returns_namespaced_payload():
    inv = FetcherInvocation(
        fetcher=_Fixed("test.cal", "calendar", {"events": [{"id": "a"}]}),
        config={},
        timeout_seconds=300,
    )
    ctx = await gather_context([inv], when=datetime.now(UTC))
    assert ctx == {"calendar": {"events": [{"id": "a"}]}}


@pytest.mark.asyncio
async def test_multiple_fetchers_merge_into_separate_namespaces():
    invs = [
        FetcherInvocation(_Fixed("test.cal", "calendar", {"x": 1}), {}, 300),
        FetcherInvocation(_Fixed("test.email", "email", {"y": 2}), {}, 300),
    ]
    ctx = await gather_context(invs, when=datetime.now(UTC))
    assert ctx == {"calendar": {"x": 1}, "email": {"y": 2}}


@pytest.mark.asyncio
async def test_dotted_namespace_creates_nested_dict():
    inv = FetcherInvocation(
        _Fixed("test.streak", "streaks.eod_log", {"current": 14}),
        {},
        300,
    )
    ctx = await gather_context([inv], when=datetime.now(UTC))
    assert ctx == {"streaks": {"eod_log": {"current": 14}}}


@pytest.mark.asyncio
async def test_failing_fetcher_lands_in_errors_namespace():
    invs = [
        FetcherInvocation(_Fixed("test.ok", "good", {"a": 1}), {}, 300),
        FetcherInvocation(_Boom(), {}, 300),
    ]
    ctx = await gather_context(invs, when=datetime.now(UTC))
    assert ctx["good"] == {"a": 1}
    assert "_errors" in ctx
    assert "test.boom" in ctx["_errors"]
    assert "boom" in ctx["_errors"]["test.boom"]["error"]
    assert ctx["_errors"]["test.boom"]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_timeout_isolated_to_offending_fetcher():
    invs = [
        FetcherInvocation(_Fixed("test.ok", "good", {"a": 1}), {}, 300),
        FetcherInvocation(_Slow("test.slow", "slow", delay_s=10), {}, timeout_seconds=0.1),
    ]
    ctx = await gather_context(invs, when=datetime.now(UTC))
    assert ctx["good"] == {"a": 1}
    assert ctx["_errors"]["test.slow"]["type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_concurrent_execution_total_time_bounded_by_slowest():
    slow = FetcherInvocation(_Slow("test.s1", "s1", delay_s=0.5), {}, 5)
    other = FetcherInvocation(_Slow("test.s2", "s2", delay_s=0.5), {}, 5)
    start = time.monotonic()
    await gather_context([slow, other], when=datetime.now(UTC))
    elapsed = time.monotonic() - start
    # Concurrent: ~0.5s. Sequential would be ~1.0s.
    assert elapsed < 0.9, f"gather should run concurrently, got {elapsed}s"
