"""Tests for BusHandle — the per-endpoint surface to the bus."""

import asyncio
from datetime import UTC, datetime

from agent_core.bus.envelope import EndpointInfo, Envelope, TextMessagePayload
from agent_core.bus.handle import BusHandle


class _RecordingBus:
    def __init__(self):
        self.published: list[tuple[Envelope, str | list[str] | None]] = []
        self.acks: list[str] = []
        self.nacks: list[tuple[str, bool]] = []
        self.directory = [
            EndpointInfo(name="agent-pepper", description="P"),
            EndpointInfo(name="discord", description="D"),
        ]

    async def _enqueue(self, envelope: Envelope, to=None) -> None:
        self.published.append((envelope, to))

    async def _ack(self, envelope_id: str) -> None:
        self.acks.append(envelope_id)

    async def _nack(self, envelope_id: str, requeue: bool) -> None:
        self.nacks.append((envelope_id, requeue))

    def _endpoints(self) -> list[EndpointInfo]:
        return list(self.directory)


def _envelope(**overrides) -> Envelope:
    fields = dict(
        id="e1",
        correlation_id="c1",
        to="agent-pepper",
        kind="TextMessage",
        payload=TextMessagePayload(text="hi"),
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
    )
    fields.update(overrides)
    return Envelope(**fields)


class TestBusHandlePublish:
    async def test_stamps_from(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "agent-pepper")
        env = _envelope(from_="not-pepper")  # caller tries to spoof
        await handle.publish(env)
        published, _ = bus.published[0]
        assert published.from_ == "agent-pepper"  # bus overwrote

    async def test_stamps_when_unset(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "discord")
        env = _envelope()  # from_ defaults to ""
        await handle.publish(env)
        assert bus.published[0][0].from_ == "discord"

    async def test_passes_to_override(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "discord")
        env = _envelope()
        await handle.publish(env, to=["a", "b"])
        assert bus.published[0][1] == ["a", "b"]

    async def test_does_not_mutate_caller_envelope(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "agent-pepper")
        env = _envelope(from_="not-pepper")
        original_from = env.from_
        await handle.publish(env)
        assert env.from_ == original_from  # caller's copy is untouched


class TestBusHandleAckNack:
    async def test_ack_delegates(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.ack("e1")
        assert bus.acks == ["e1"]

    async def test_nack_delegates_with_requeue_default(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.nack("e1")
        assert bus.nacks == [("e1", True)]

    async def test_nack_with_no_requeue(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        await handle.nack("e1", requeue=False)
        assert bus.nacks == [("e1", False)]


class TestBusHandleEndpoints:
    def test_endpoints_returns_directory_snapshot(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        infos = handle.endpoints()
        assert {i.name for i in infos} == {"agent-pepper", "discord"}

    def test_endpoints_is_independent_copy(self):
        bus = _RecordingBus()
        handle = BusHandle(bus, "x")
        infos = handle.endpoints()
        infos.clear()
        # Mutating the returned list must not affect later calls.
        assert len(handle.endpoints()) == 2


class TestBusHandleSpawn:
    async def test_spawn_complete_removes_from_task_set(self):
        """Completed task is removed from the internal tracked set."""
        handle = BusHandle(_RecordingBus(), "x")

        async def _noop() -> None:
            pass

        task = handle.spawn(_noop())
        await asyncio.gather(task, return_exceptions=True)
        assert task not in handle._tasks

    async def test_spawn_raise_invokes_failure_hook_exactly_once(self):
        """Task raising invokes the injected hook exactly once."""
        failures: list[BaseException] = []
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=failures.append)

        async def _boom() -> None:
            raise ValueError("oops")

        task = handle.spawn(_boom())
        await asyncio.gather(task, return_exceptions=True)
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)

    async def test_spawn_raise_does_not_propagate_to_loop(self):
        """Exception in a spawned task does NOT bubble to the event loop."""
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=lambda _: None)

        async def _boom() -> None:
            raise RuntimeError("contained")

        task = handle.spawn(_boom())
        await asyncio.gather(task, return_exceptions=True)
        assert task.done() and not task.cancelled()

    async def test_drain_cancels_task_and_hook_not_fired(self):
        """CancelledError on shutdown is not treated as a failure."""
        failures: list[BaseException] = []
        handle = BusHandle(_RecordingBus(), "x", on_task_failure=failures.append)

        async def _hang() -> None:
            await asyncio.sleep(1000)

        task = handle.spawn(_hang())
        await handle._drain_tasks()
        assert task.cancelled()
        assert failures == []

    async def test_drain_cancels_all_pending_tasks_and_clears_set(self):
        """_drain_tasks() cancels every outstanding task and empties the set."""
        handle = BusHandle(_RecordingBus(), "x")
        started = asyncio.Event()

        async def _long() -> None:
            started.set()
            await asyncio.sleep(1000)

        t1 = handle.spawn(_long())
        t2 = handle.spawn(_long())
        await started.wait()
        await handle._drain_tasks()
        assert t1.cancelled()
        assert t2.cancelled()
        assert len(handle._tasks) == 0
