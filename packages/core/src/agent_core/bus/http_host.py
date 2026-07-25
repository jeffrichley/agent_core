"""Shared Starlette+Uvicorn HTTP host for MCP endpoint adapters.

Endpoints that want to be served over HTTP implement the MCPHostable
Protocol (`mount` + `asgi_app()`). The runner collects them after
endpoint registration and constructs a single HTTPHost with all of
them mounted under their declared paths.

Uvicorn runs on the bus's asyncio event loop. Loopback bind only is
enforced upstream in `bus/runner.py`; this module trusts the host
parameter it receives.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Protocol, cast, runtime_checkable

import uvicorn
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Mount, Route, Router

from agent_core.bus.auth.middleware import BusAuthMiddleware
from agent_core.bus.auth.pubkey_registry import PubkeyRegistry
from agent_core.bus.notify_broker import NotificationBroker

log = logging.getLogger(__name__)


@runtime_checkable
class ASGIApp(Protocol):
    async def __call__(
        self,
        scope: MutableMapping[str, object],
        receive: Callable[[], Awaitable[MutableMapping[str, object]]],
        send: Callable[[MutableMapping[str, object]], Awaitable[None]],
    ) -> None: ...


@runtime_checkable
class MCPHostable(Protocol):
    """An endpoint that wants to be mounted on the shared HTTP host."""

    mount: str

    def asgi_app(self) -> ASGIApp:
        """Return the ASGI application to serve under `self.mount`."""


def _make_lifespan(apps: list[ASGIApp]):
    """Return a Starlette-compatible lifespan context that propagates startup/shutdown
    to every sub-app in *apps*.

    Each app in the list is called with a synthetic ``lifespan`` ASGI scope.  The
    top-level Router's own lifespan context uses this, so uvicorn's single
    ``lifespan.startup`` / ``lifespan.shutdown`` pair fans out to every mount.
    """

    @contextlib.asynccontextmanager
    async def _lifespan(_starlette_app: ASGIApp):
        tasks: list[asyncio.Task] = []
        receive_queues: list[asyncio.Queue] = []
        send_queues: list[asyncio.Queue] = []

        for app in apps:
            rq: asyncio.Queue = asyncio.Queue()
            sq: asyncio.Queue = asyncio.Queue()
            receive_queues.append(rq)
            send_queues.append(sq)

            scope: MutableMapping[str, object] = {
                "type": "lifespan",
                "asgi": cast(object, {"version": "3.0"}),
                "state": cast(object, {}),
            }

            async def _receive(q=rq):
                return await q.get()

            async def _send(msg, q=sq):
                await q.put(msg)

            tasks.append(asyncio.ensure_future(app(scope, _receive, _send)))

        # Fan out startup event and wait for all sub-apps to report ready.
        for rq in receive_queues:
            await rq.put({"type": "lifespan.startup"})

        for sq in send_queues:
            msg = await sq.get()
            if msg["type"] != "lifespan.startup.complete":
                log.warning("Unexpected lifespan startup message: %s", msg)

        try:
            yield
        finally:
            # Fan out shutdown event and wait for completions.
            for rq in receive_queues:
                await rq.put({"type": "lifespan.shutdown"})

            for sq, task in zip(send_queues, tasks, strict=False):
                try:
                    msg = await asyncio.wait_for(sq.get(), timeout=5.0)
                    if msg["type"] != "lifespan.shutdown.complete":
                        log.warning("Unexpected lifespan shutdown message: %s", msg)
                except TimeoutError:
                    log.warning("Timed out waiting for lifespan.shutdown.complete")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:  # pragma: no cover - shutdown race
                    # Expected: we just cancelled this ASGI lifespan task.
                    pass
                except Exception as exc:  # pragma: no cover - defensive shutdown guard
                    log.warning("ASGI lifespan task raised during shutdown: %s", exc)

    return _lifespan


class HTTPHost:
    """Owns one Uvicorn server on the bus's loop.

    Mounts are added before `start()`. After `start()`, the bound port
    is available via `self.port` (useful when bind_port=0 for tests).
    """

    def __init__(
        self,
        *,
        bind_host: str = "127.0.0.1",
        bind_port: int = 8788,
        notify_broker: NotificationBroker | None = None,
        notify_snapshot: Callable[[str], dict | None] | None = None,
        pubkey_registry: PubkeyRegistry | None = None,
        bus_auth_mode: str = "off",
    ):
        self._bind_host = bind_host
        self._requested_port = bind_port
        self._mounts: list[MCPHostable] = []
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None
        self._started = False
        self._notify_broker = notify_broker
        self._notify_snapshot = notify_snapshot
        self._pubkey_registry = pubkey_registry   # consumed by auth middleware (Dβ-2b)
        self._bus_auth_mode = bus_auth_mode

    def mount(self, hostable: MCPHostable) -> None:
        if self._started:
            raise RuntimeError("cannot add mounts after start()")
        self._mounts.append(hostable)

    @property
    def port(self) -> int:
        """The bound port. Only valid after start()."""
        if self._server is None or not self._server.servers:
            return -1
        sockets = self._server.servers[0].sockets
        if not sockets:
            return -1
        return sockets[0].getsockname()[1]

    async def _serve_safely(self) -> None:
        """Wrap uvicorn.Server.serve() to convert SystemExit to OSError.

        Uvicorn calls ``sys.exit(1)`` on startup errors (e.g. port in use).
        ``SystemExit`` is a ``BaseException`` that propagates straight through
        asyncio's task machinery and crashes the event loop.  Converting it to
        ``OSError`` inside the task keeps the exception contained so callers
        can catch it normally.
        """
        assert self._server is not None
        try:
            await self._server.serve()
        except SystemExit as exc:
            raise OSError(f"HTTPHost server exited unexpectedly (code {exc.code})") from exc

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("HTTPHost already running")

        sub_apps = [m.asgi_app() for m in self._mounts]
        mount_prefixes = {m.mount for m in self._mounts}

        routes: list = [Mount(m.mount, app=app) for m, app in zip(self._mounts, sub_apps, strict=False)]
        if self._notify_broker is not None and self._notify_snapshot is not None:
            routes.append(
                Route(
                    "/notify/{agent}",
                    endpoint=self._make_notify_handler(),
                    methods=["GET"],
                )
            )

        router = Router(
            routes=routes,
            redirect_slashes=False,
            lifespan=_make_lifespan(sub_apps),
        )

        # Auth middleware wraps the router. Path normalization in _app runs first
        # (before the middleware) so bare /mcp/<being> → /mcp/<being>/ happens
        # before the being name is extracted.
        _auth_inner = BusAuthMiddleware(
            router,
            pubkey_registry=self._pubkey_registry,
            bus_auth_mode=self._bus_auth_mode,
        )

        # Thin ASGI shim: Starlette's Mount requires a trailing slash to match a
        # bare prefix path (e.g. GET /mcp/foo → regex ^/mcp/foo/.*$ → no match).
        # Rather than issuing a 307 redirect (redirect_slashes=True) we normalise
        # the path inline so clients that hit the exact mount path still get routed
        # correctly without a round-trip redirect.
        async def _app(scope: dict, receive, send) -> None:
            if scope["type"] in ("http", "websocket"):
                path: str = scope.get("path", "")
                if path in mount_prefixes and not path.endswith("/"):
                    scope = {**scope, "path": path + "/"}
            await _auth_inner(scope, receive, send)

        config = uvicorn.Config(
            _app,
            host=self._bind_host,
            port=self._requested_port,
            log_level="warning",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._serve_task = asyncio.create_task(self._serve_safely())

        # Wait until uvicorn reports the server is up (or fails fast).
        for _ in range(200):  # ~10s ceiling
            if self._server.started:
                break
            if self._serve_task.done():
                # Surface any startup error (e.g., port in use).
                # _serve_safely() has already converted SystemExit -> OSError.
                self._serve_task.result()
                raise RuntimeError("HTTPHost serve task exited before startup")
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("HTTPHost did not start within 10s")

        self._started = True
        log.info(
            "HTTPHost listening on %s:%d (%d mount(s))",
            self._bind_host,
            self.port,
            len(self._mounts),
        )

    async def stop(self) -> None:
        if not self._started:
            return
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=10)
            except TimeoutError:
                self._serve_task.cancel()
                try:
                    await self._serve_task
                except asyncio.CancelledError:  # pragma: no cover - shutdown race
                    # Expected: we just cancelled the serve task after a stop timeout.
                    pass
                except Exception as exc:  # pragma: no cover - defensive shutdown guard
                    log.warning("uvicorn serve task raised during shutdown: %s", exc)
        self._server = None
        self._serve_task = None
        self._started = False
        log.info("HTTPHost stopped")

    def _make_notify_handler(self):
        """Build a Starlette endpoint that streams notification events as SSE.

        The closure captures ``broker`` and ``snapshot`` directly so the inner
        ``_notify`` async function does not depend on ``self`` at request time
        (Starlette routes bind their endpoint at construction).
        """
        broker = self._notify_broker
        snapshot = self._notify_snapshot
        assert broker is not None and snapshot is not None  # guard for type-checker

        async def _notify(request: Request) -> StreamingResponse:
            agent = request.path_params["agent"]
            queue = await broker.subscribe(agent)

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    initial = snapshot(agent)
                    if initial is not None:
                        yield f"data: {json.dumps(initial)}\n\n".encode()
                    while True:
                        event = await queue.get()
                        yield f"data: {json.dumps(event)}\n\n".encode()
                finally:
                    await broker.unsubscribe(agent, queue)

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        return _notify
