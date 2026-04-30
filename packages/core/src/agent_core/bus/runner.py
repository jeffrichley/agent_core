"""Boot sequence — load YAML, instantiate endpoints, register, start.

The runner is the only place that imports endpoint classes by string. It also
enforces v1 invariants: loopback-only bind unless an auth hook is configured
(BACKLOG: auth for non-loopback bind).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pluggy
import yaml

from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec
from agent_core.bus.http_host import HTTPHost, MCPHostable
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.bus.protocol import BusHook, Endpoint, NotificationBrokerAwareEndpoint
from agent_core.plugins.manager import create_plugin_manager


class BusBootError(Exception):
    """Raised when the runner cannot construct a valid Bus from the config."""


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _import_class(path: str, plugin_manager: pluggy.PluginManager) -> Any:
    resolved = plugin_manager.hook.resolve_class(class_path=path)
    if resolved is not None:
        return resolved
    module_path, _, class_name = path.rpartition(".")
    if not module_path:
        raise BusBootError(f"invalid class path: {path!r}")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise BusBootError(f"cannot import {module_path!r}: {exc}") from exc
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise BusBootError(f"{module_path!r} has no attribute {class_name!r}") from exc


def _validate_http(http_cfg: dict, has_auth_hook: bool) -> None:
    host = http_cfg.get("bind_host", "127.0.0.1")
    if host not in _LOOPBACK_HOSTS and not has_auth_hook:
        raise BusBootError(
            f"http.bind_host={host!r} is non-loopback but no auth hook is configured. "
            "v1 supports loopback only; see BACKLOG for the auth hook trigger."
        )


async def build_bus_from_config(path: Path) -> tuple[Bus, HTTPHost | None]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    plugin_manager = create_plugin_manager()

    bus_cfg_raw = raw.get("bus", {})
    storage_path = Path(bus_cfg_raw.get("storage_path", "~/.agent-core/bus.sqlite")).expanduser()
    cfg = BusConfig(
        storage_path=storage_path,
        redelivery_timeout_seconds=bus_cfg_raw.get("redelivery_timeout_seconds", 300),
        max_delivery_attempts=bus_cfg_raw.get("max_delivery_attempts", 5),
        ttl_sweep_seconds=bus_cfg_raw.get("ttl_sweep_seconds", 60),
        redelivery_sweep_seconds=bus_cfg_raw.get("redelivery_sweep_seconds", 10),
        acked_retention_days=bus_cfg_raw.get("acked_retention_days", 14),
        max_pending_per_endpoint=bus_cfg_raw.get("max_pending_per_endpoint", 10_000),
    )

    bus = Bus(cfg)

    # Broker for /notify/<agent> SSE fan-out. Created here so endpoints
    # constructed below can be wired to publish push summaries through it,
    # and HTTPHost can serve subscribers off the same instance.
    notify_broker = NotificationBroker()

    # Hooks (no auth-aware filtering yet — Phase 2 will add it).
    # TODO(Phase 2): scan loaded hooks for auth-hook interface and set True.
    # Until then, non-loopback bind is always refused. See BACKLOG.md.
    has_auth_hook = False
    for stage in ("pre_publish", "pre_deliver"):
        for entry in (raw.get("bus_hooks", {}) or {}).get(stage, []) or []:
            if "class" not in entry:
                raise BusBootError(f"hook entry missing required 'class' field: {entry!r}")
            cls = _import_class(entry["class"], plugin_manager)
            try:
                instance = cls(**entry.get("params", {}))
            except Exception as exc:
                raise BusBootError(
                    f"{entry['class']!r} does not satisfy BusHook protocol: {exc}"
                ) from exc
            if not isinstance(instance, BusHook):
                raise BusBootError(f"{entry['class']!r} does not satisfy BusHook protocol")
            bus.register_hook(stage, BusHookSpec(hook=instance, params=entry.get("params", {})))

    # HTTP guardrail.
    http_cfg = raw.get("http", {})
    _validate_http(http_cfg, has_auth_hook)

    # Endpoints.
    for entry in raw.get("endpoints", []) or []:
        if "class" not in entry:
            raise BusBootError(f"endpoint entry missing required 'class' field: {entry!r}")
        if "name" not in entry:
            raise BusBootError(f"endpoint entry missing required 'name' field: {entry!r}")
        cls = _import_class(entry["class"], plugin_manager)
        params = entry.get("params", {})
        # Runner-side convention (not enforced by the Endpoint Protocol):
        # every endpoint class must accept `name` as a constructor kwarg.
        # The Protocol only requires `name` as an *attribute*; this convention
        # is what lets the runner construct from YAML without per-class adapters.
        try:
            instance = cls(name=entry["name"], **params)
        except Exception as exc:
            raise BusBootError(
                f"{entry['class']!r} does not satisfy Endpoint protocol: {exc}"
            ) from exc
        if not isinstance(instance, Endpoint):
            raise BusBootError(f"{entry['class']!r} does not satisfy Endpoint protocol")
        if isinstance(instance, NotificationBrokerAwareEndpoint):
            instance.attach_notify_broker(notify_broker)
        bus.register(EndpointSpec(endpoint=instance, description=entry.get("description", "")))

    hostable: list[MCPHostable] = [
        spec.endpoint
        for spec in bus._endpoints_by_name.values()
        if isinstance(spec.endpoint, MCPHostable)
    ]
    http_host: HTTPHost | None = None
    if hostable:
        host = http_cfg.get("bind_host", "127.0.0.1")
        port = http_cfg.get("bind_port", 8788)
        http_host = HTTPHost(
            bind_host=host,
            bind_port=port,
            notify_broker=notify_broker,
            notify_snapshot=bus.snapshot_for_agent,
        )
        for h in hostable:
            http_host.mount(h)

    return bus, http_host
