"""Shared fixtures for agent-core-qa scenarios.

Daemon lifecycle is managed by the session-scoped `source_daemon` fixture
(defined in `agent_core_qa.fixtures`, loaded via `pytest_plugins`). Tests
run against a running daemon without any manual pre-start step.
"""

from __future__ import annotations

import os

import pytest
from agent_core_qa.client import DaemonClient

pytest_plugins = ["agent_core_qa.fixtures"]

DEFAULT_DAEMON_URL = "http://127.0.0.1:8787"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--daemon-url",
        action="store",
        default=None,
        help="Base URL of the test daemon's HTTP API (default: http://127.0.0.1:8787 "
        "or AGENT_CORE_QA_DAEMON_URL env var)",
    )


@pytest.fixture(scope="session", autouse=True)
def auto_daemon(source_daemon: str) -> None:
    """Opt the whole session into the source_daemon lifecycle.

    Depends on `source_daemon` (from agent_core_qa.fixtures) so the daemon is
    started and torn down exactly once per session before any test runs.
    Track A (#394) can declare its own autouse wrapper that calls
    `agent-core daemon install` before yielding to `source_daemon`.
    """


@pytest.fixture(scope="session")
def daemon_url(request: pytest.FixtureRequest) -> str:
    """Resolve the daemon URL from CLI flag, env var, or default.

    Promoted to session scope: the URL is a constant string resolved once per
    session (avoids one redundant resolution call per test).
    """
    flag = request.config.getoption("--daemon-url")
    if flag:
        return str(flag)
    env = os.environ.get("AGENT_CORE_QA_DAEMON_URL")
    if env:
        return env
    return DEFAULT_DAEMON_URL


@pytest.fixture
def client(daemon_url: str) -> DaemonClient:
    """Per-test DaemonClient — no session sharing."""
    return DaemonClient(daemon_url)
