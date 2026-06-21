"""Daemon endpoint wrapper for the inbound-notifications router.

Wires the FastAPI Funnel handler + Router into the daemon lifecycle.
Configuration is sourced from the endpoint's block in agent_core.yaml:

  endpoints:
    inbound:
      module: agent_core_inbound.endpoint
      class: InboundEndpoint
      args:
        target_being: wren
        listen_host: 127.0.0.1
        listen_port: 8765
        webhook_secret_env: FOREMAN_GITHUB_WEBHOOK_SECRET
        github_allowance_path: ~/.wren/.config/inbound/github-allowance.toml
        audit_log_path: ~/.wren/state/inbound-audit.jsonl
        rate_limit_per_minute: 30

Tailscale Funnel is configured OUT-OF-PROCESS via the operator's
`tailscale funnel <port>` command pointing at ``listen_port``. The
endpoint binds to ``listen_host`` (default 127.0.0.1) so the only
public reach is through the tailnet-issued Funnel URL.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router


class InboundEndpoint:
    """Daemon-lifecycle wrapper for the inbound-notifications router."""

    def __init__(
        self,
        *,
        bus_handle,  # agent_core.bus.handle.BusHandle (avoid hard import here)
        target_being: str,
        listen_host: str,
        listen_port: int,
        webhook_secret_env: str,
        github_allowance_path: str,
        audit_log_path: str,
        rate_limit_per_minute: int = 30,
    ) -> None:
        self._bus = bus_handle
        self._target_being = target_being
        self._listen_host = listen_host
        self._listen_port = listen_port

        secret = os.environ.get(webhook_secret_env)
        if not secret:
            raise RuntimeError(
                f"inbound endpoint: env var {webhook_secret_env} not set "
                f"(needed for GitHub webhook HMAC signature verification)"
            )
        self._webhook_secret = secret.encode("utf-8")

        connector = GitHubConnector(
            config_path=Path(github_allowance_path).expanduser(),
            principal_being=target_being,
        )
        audit = AuditLog(path=Path(audit_log_path).expanduser())
        self._router = Router(
            connectors={"github": connector},
            bus_publish=self._bus_publish_adapter,
            audit=audit,
            rate_limits={("github", target_being): (rate_limit_per_minute, 60.0)},
        )
        self._app = build_funnel_app(
            router=self._router,
            webhook_secret=self._webhook_secret,
            target_being=target_being,
        )
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the FastAPI server in a background task."""
        config = uvicorn.Config(
            self._app,
            host=self._listen_host,
            port=self._listen_port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._serve_task = asyncio.create_task(self._server.serve())

    async def stop(self) -> None:
        """Stop the server gracefully."""
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            except TimeoutError:
                self._serve_task.cancel()

    def _bus_publish_adapter(
        self,
        *,
        to: str,
        kind: str,
        payload: dict,
        urgency: str,
    ) -> None:
        """Bridge between the Router's bus_publish callable and the
        daemon's BusHandle.send() API.

        Daemons differ in BusHandle method names across versions; the
        endpoint loader's runtime check during `agent-core bus run` is
        where this gets wired live. The adapter signature stays stable
        for the Router substrate.
        """
        self._bus.send(
            to=to,
            kind=kind,
            payload=payload,
            urgency=urgency,
        )
