"""Daemon endpoint wrapper for the inbound-notifications router.

Wires the FastAPI Funnel handler + Router into the bus runner via the
``register_endpoint_types`` hookimpl (see ``plugin.py``).

agent_core.yaml shape:

  endpoints:
    - type: inbound.github
      name: inbound
      params:
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

Inbound is push-only — no peer endpoint addresses envelopes to it on
the bus — so ``deliver`` just warns + acks anything that arrives.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import uvicorn

from agent_core_credentials.secrets import SecretNotFoundError
from agent_core_credentials.secrets import get as get_secret
from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router

if TYPE_CHECKING:
    from agent_core.bus.envelope import Envelope
    from agent_core.bus.handle import BusHandle


log = logging.getLogger(__name__)


class InboundEndpoint:
    """Daemon-lifecycle wrapper for the inbound-notifications router."""

    def __init__(
        self,
        *,
        name: str,
        target_being: str,
        listen_host: str,
        listen_port: int,
        webhook_secret_env: str,
        github_allowance_path: str,
        audit_log_path: str,
        rate_limit_per_minute: int = 30,
    ) -> None:
        self.name = name  # Protocol requires `name` attribute
        self._target_being = target_being
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._webhook_secret_env = webhook_secret_env
        self._github_allowance_path = github_allowance_path
        self._audit_log_path = audit_log_path
        self._rate_limit_per_minute = rate_limit_per_minute

        # Validate secret at construction (fail-loud at config-load,
        # not at first GitHub delivery).
        try:
            secret = get_secret(webhook_secret_env)
        except SecretNotFoundError:
            raise RuntimeError(
                f"inbound endpoint {name!r}: env var {webhook_secret_env} not set "
                f"(needed for GitHub webhook HMAC signature verification)"
            ) from None
        self._webhook_secret = secret.encode("utf-8")

        # Defer Router + funnel app construction to start(); they need
        # the BusHandle for bus_publish_adapter.
        self._handle: BusHandle | None = None
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None

    async def start(self, bus: BusHandle) -> None:
        """Bus is ready — build the Router/app and start uvicorn."""
        self._handle = bus

        connector = GitHubConnector(
            config_path=Path(self._github_allowance_path).expanduser(),
            principal_being=self._target_being,
        )
        audit = AuditLog(path=Path(self._audit_log_path).expanduser())
        router = Router(
            connectors={"github": connector},
            bus_publish=self._bus_publish_adapter,
            audit=audit,
            rate_limits={
                ("github", self._target_being): (self._rate_limit_per_minute, 60.0)
            },
        )
        app = build_funnel_app(
            router=router,
            webhook_secret=self._webhook_secret,
            target_being=self._target_being,
        )
        config = uvicorn.Config(
            app,
            host=self._listen_host,
            port=self._listen_port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._serve_task = asyncio.create_task(self._server.serve())
        log.info(
            "InboundEndpoint(name=%s) started on %s:%s",
            self.name,
            self._listen_host,
            self._listen_port,
        )

    async def deliver(self, envelope: Envelope) -> None:
        """Inbound is push-only — no peer addresses envelopes here.

        * Before start (_handle is None): raises EndpointUnavailable so the bus
          requeues the envelope (transient — the endpoint is not yet ready).
        * After start (_handle is not None): raises RuntimeError so the bus
          dead-letters the misrouted envelope.  Acking would falsely mark the
          envelope as "successfully delivered" and leave no DLQ record.
        """
        if self._handle is None:
            from agent_core.bus.protocol import EndpointUnavailable
            raise EndpointUnavailable(
                f"inbound endpoint {self.name!r} not started"
            )
        log.warning(
            "InboundEndpoint(name=%s) received unexpected envelope kind=%s from=%s — dead-lettering",
            self.name,
            envelope.kind,
            envelope.from_,
        )
        raise RuntimeError(
            f"inbound endpoint {self.name!r} received unexpected envelope kind={envelope.kind!r} "
            f"from={envelope.from_!r}; inbound is push-only"
        )

    async def stop(self) -> None:
        """Graceful shutdown — set should_exit and await up to 5s."""
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            except TimeoutError:
                self._serve_task.cancel()
        self._handle = None
        log.info("InboundEndpoint(name=%s) stopped", self.name)

    def _bus_publish_adapter(
        self,
        *,
        to: str,
        kind: str,
        payload: dict[str, Any],
        urgency: str,
    ) -> None:
        """Bridge Router's bus_publish callable to BusHandle.publish.

        Router calls this sync from inside the FastAPI async handler, so
        we are guaranteed to be on a running event loop. BusHandle.publish
        is async; we schedule it as a task and return immediately so the
        Router's sync path doesn't block on the bus enqueue.

        Construction notes:
        * "Notification" is a BUILTIN_KIND — the Envelope validator
          requires the payload to validate against ``NotificationPayload``
          (a raw dict would be rejected). We model_validate here so any
          payload shape bug surfaces at the call site rather than as an
          opaque Envelope construction error.
        * ``from_`` is left default — the bus stamps it to ``self.name``
          via BusHandle on publish.
        * ``urgency`` is narrowed to ``Literal["green","yellow","red"]``
          at the Envelope layer; the Router passes a str (tier.value),
          so we trust the connector's tier vocabulary here.
        """
        if self._handle is None:
            raise RuntimeError(
                f"inbound endpoint {self.name!r}: bus_publish called before start"
            )

        from agent_core.bus.envelope import Envelope, NotificationPayload

        notification = NotificationPayload.model_validate(payload)
        envelope = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            to=to,
            kind=kind,
            payload=notification,
            urgency=cast(Literal["green", "yellow", "red"], urgency),
            created_at=datetime.now(UTC),
        )
        self._handle.spawn(self._handle.publish(envelope), name="inbound-bus-publish")
