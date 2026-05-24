"""Shared fixtures for agent-core-qa scenarios.

Tool-shaped: each fixture is per-test; no module/session-scoped state
beyond what pytest itself manages.
"""

from __future__ import annotations

import os

import pytest

from agent_core_qa.client import DaemonClient


DEFAULT_DAEMON_URL = "http://127.0.0.1:8787"


def pytest_addoption(parser):
    parser.addoption(
        "--daemon-url",
        action="store",
        default=None,
        help="Base URL of the test daemon's HTTP API (default: http://127.0.0.1:8787 "
        "or AGENT_CORE_QA_DAEMON_URL env var)",
    )


@pytest.fixture
def daemon_url(request) -> str:
    """Resolve the daemon URL from CLI flag, env var, or default."""
    flag = request.config.getoption("--daemon-url")
    if flag:
        return flag
    env = os.environ.get("AGENT_CORE_QA_DAEMON_URL")
    if env:
        return env
    return DEFAULT_DAEMON_URL


@pytest.fixture
def client(daemon_url: str) -> DaemonClient:
    """Per-test DaemonClient — no session sharing."""
    return DaemonClient(daemon_url)


@pytest.fixture(autouse=True)
def daemon_liveness_required(request, client: DaemonClient):
    """Skip all scenarios if the test daemon isn't reachable.

    Phase 2.7 failure-class catch: "does the daemon actually start after
    install." If the daemon is down, dependent scenarios skip with a clear
    reason instead of failing with cryptic connection errors.
    """
    # The liveness scenario itself shouldn't skip itself.
    if request.node.name == "test_daemon_liveness":
        return
    try:
        response = client.health_check()
        if response.status_code != 200:
            pytest.skip(
                f"test daemon at {client.base_url} returned "
                f"{response.status_code}; run `agent-core daemon start --instance test` first"
            )
    except Exception as exc:
        pytest.skip(
            f"test daemon not reachable at {client.base_url}: {exc!r}; "
            f"run `agent-core daemon start --instance test` first"
        )
