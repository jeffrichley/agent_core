"""SSE client: parse data: lines, retry with backoff on failure."""

from __future__ import annotations

import pytest

from agent_core_channel.sse_client import iter_notify_events


class _FakeStreamResponse:
    """Mimics httpx.Response.aiter_lines() behavior for one batch of lines."""

    def __init__(self, lines: list[str], status_error: Exception | None = None):
        self._lines = lines
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """Mimics httpx.AsyncClient.stream(). Each call yields the next scripted response.

    A response can be a list[str] (lines), a _FakeStreamResponse, or an
    Exception (raised on stream open).
    """

    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[tuple[str, str]] = []

    def stream(self, method: str, url: str):
        self.calls.append((method, url))
        nxt = self._scripted.pop(0)

        class _Cm:
            async def __aenter__(_self):
                if isinstance(nxt, Exception):
                    raise nxt
                if isinstance(nxt, _FakeStreamResponse):
                    return nxt
                return _FakeStreamResponse(nxt)

            async def __aexit__(_self, *exc):
                return False

        return _Cm()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_sse_client_yields_parsed_data_lines():
    client = _FakeAsyncClient(
        scripted=[
            [
                'data: {"meta": {"count": 1}}',
                "",
                'data: {"meta": {"count": 2}}',
                "",
            ]
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="agent-a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=2,
    ):
        events.append(ev)

    assert events == [{"meta": {"count": 1}}, {"meta": {"count": 2}}]
    assert client.calls == [("GET", "http://127.0.0.1:8788/notify/agent-a")]


@pytest.mark.asyncio
async def test_sse_client_reconnects_after_stream_close():
    """When a stream ends, the client immediately reconnects."""
    client = _FakeAsyncClient(
        scripted=[
            ['data: {"first": 1}', ""],  # first stream, then closes
            ['data: {"second": 2}', ""],  # reconnect
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=2,
        backoff_initial=0.001,  # speed up the test
        backoff_max=0.001,
    ):
        events.append(ev)

    assert events == [{"first": 1}, {"second": 2}]
    assert len(client.calls) == 2  # reconnected


@pytest.mark.asyncio
async def test_sse_client_retries_on_connection_error():
    """On exception during stream open, retry with backoff."""
    client = _FakeAsyncClient(
        scripted=[
            ConnectionError("daemon down"),
            ConnectionError("still down"),
            ['data: {"after_retry": true}', ""],
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
        backoff_initial=0.001,
        backoff_max=0.001,
    ):
        events.append(ev)

    assert events == [{"after_retry": True}]
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_sse_client_retries_on_http_error_status():
    """Non-2xx responses should go through the backoff retry path."""
    client = _FakeAsyncClient(
        scripted=[
            _FakeStreamResponse([], status_error=RuntimeError("404 not found")),
            ['data: {"after_retry": true}', ""],
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
        backoff_initial=0.001,
        backoff_max=0.001,
    ):
        events.append(ev)

    assert events == [{"after_retry": True}]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_sse_client_skips_non_data_lines():
    """Comments, empty lines, and other SSE fields are ignored."""
    client = _FakeAsyncClient(
        scripted=[
            [
                ":heartbeat",
                "event: ping",
                "id: 42",
                'data: {"real": true}',
                "",
            ]
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
    ):
        events.append(ev)

    assert events == [{"real": True}]


@pytest.mark.asyncio
async def test_sse_client_handles_malformed_json():
    """Malformed data: lines are logged and skipped, not fatal."""
    client = _FakeAsyncClient(
        scripted=[
            [
                "data: not json at all",
                "",
                'data: {"valid": true}',
                "",
            ]
        ]
    )

    events: list[dict] = []
    async for ev in iter_notify_events(
        agent="a",
        daemon_url="http://127.0.0.1:8788",
        client_factory=lambda: client,
        max_events=1,
    ):
        events.append(ev)

    # The malformed line is dropped; the valid one comes through.
    assert events == [{"valid": True}]
