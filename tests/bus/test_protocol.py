"""Tests for the Endpoint and BusHook protocols."""

from typing import Literal

from agent_core.bus.envelope import Envelope
from agent_core.bus.protocol import BusHook, Endpoint, EndpointUnavailable


class _MinimalEndpoint:
    name = "stub"

    async def start(self, bus) -> None:
        pass

    async def deliver(self, envelope: Envelope) -> None:
        pass

    async def stop(self) -> None:
        pass


class _MinimalHook:
    async def execute(
        self, stage: Literal["pre_publish", "pre_deliver"], envelope: Envelope, params: dict
    ) -> Envelope | None:
        return envelope


class TestProtocols:
    def test_endpoint_runtime_check(self):
        ep = _MinimalEndpoint()
        assert isinstance(ep, Endpoint)

    def test_non_endpoint_fails_check(self):
        class NotAnEndpoint:
            pass

        assert not isinstance(NotAnEndpoint(), Endpoint)

    def test_hook_runtime_check(self):
        hook = _MinimalHook()
        assert isinstance(hook, BusHook)

    def test_endpoint_unavailable_is_exception(self):
        try:
            raise EndpointUnavailable("Discord disconnected")
        except EndpointUnavailable as exc:
            assert str(exc) == "Discord disconnected"
