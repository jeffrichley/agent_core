"""Verify InboundEndpoint satisfies the agent_core Endpoint Protocol.

This is the test the runner does at boot via isinstance(instance, Endpoint).
Catching it at pytest time is way cheaper than at `agent-core bus run` time.
"""
import pytest
from agent_core_inbound.endpoint import InboundEndpoint

from agent_core.bus.protocol import Endpoint


def test_inbound_endpoint_satisfies_protocol(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_INBOUND_SECRET", "test-secret")
    ep = InboundEndpoint(
        name="inbound",
        target_being="wren",
        listen_host="127.0.0.1",
        listen_port=18765,
        webhook_secret_env="TEST_INBOUND_SECRET",
        github_allowance_path=str(tmp_path / "g.toml"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        rate_limit_per_minute=30,
    )
    assert isinstance(ep, Endpoint), "InboundEndpoint must satisfy the Endpoint Protocol"
    assert ep.name == "inbound"


def test_inbound_endpoint_raises_without_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_INBOUND_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="TEST_INBOUND_SECRET"):
        InboundEndpoint(
            name="inbound",
            target_being="wren",
            listen_host="127.0.0.1",
            listen_port=18765,
            webhook_secret_env="TEST_INBOUND_SECRET",
            github_allowance_path=str(tmp_path / "g.toml"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
            rate_limit_per_minute=30,
        )
