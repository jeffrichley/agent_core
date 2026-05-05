"""Boot sequence — load YAML, instantiate endpoints, register, start.

The runner is the only place that imports endpoint classes by string. It also
enforces v1 invariants: loopback-only bind unless an auth hook is configured
(BACKLOG: auth for non-loopback bind).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec
from agent_core.bus.http_host import HTTPHost, MCPHostable
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.bus.protocol import BusHook, Endpoint
from agent_core.plugins.manager import (
    apply_endpoint_wiring,
    collect_reserved_endpoint_params,
    create_plugin_manager,
    get_bus_hook_types,
    get_endpoint_types,
)
from agent_core.plugins.specs import RunnerServices


class BusBootError(Exception):
    """Raised when the runner cannot construct a valid Bus from the config."""


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


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
    plugin_manager.hook.validate_config(raw_config=raw)
    endpoint_types = get_endpoint_types(plugin_manager)
    bus_hook_types = get_bus_hook_types(plugin_manager)

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
    services = RunnerServices(notify_broker=notify_broker)

    # Hooks (no auth-aware filtering yet — Phase 2 will add it).
    # TODO(Phase 2): scan loaded hooks for auth-hook interface and set True.
    # Until then, non-loopback bind is always refused. See BACKLOG.md.
    has_auth_hook = False
    for stage in ("pre_publish", "pre_deliver"):
        for entry in (raw.get("bus_hooks", {}) or {}).get(stage, []) or []:
            if "type" not in entry:
                raise BusBootError(f"hook entry missing required 'type' field: {entry!r}")
            hook_type = str(entry["type"])
            cls = bus_hook_types.get(hook_type)
            if cls is None:
                raise BusBootError(f"unknown bus hook type: {hook_type!r}")
            try:
                instance = cls(**entry.get("params", {}))
            except Exception as exc:
                raise BusBootError(
                    f"bus hook type {hook_type!r} does not satisfy BusHook protocol: {exc}"
                ) from exc
            if not isinstance(instance, BusHook):
                raise BusBootError(f"bus hook type {hook_type!r} does not satisfy BusHook protocol")
            plugin_manager.hook.configure_bus_hook_instance(
                instance=instance,
                stage=stage,
                hook_config=entry,
                services=services,
            )
            bus.register_hook(stage, BusHookSpec(hook=instance, params=entry.get("params", {})))

    # HTTP guardrail.
    http_cfg = raw.get("http", {})
    _validate_http(http_cfg, has_auth_hook)

    # Endpoints.
    # Plugin-managed param names (e.g., briefs_orchestrator) get popped before
    # construction so endpoint classes don't have to swallow them. The full
    # raw config (including the popped keys) still reaches plugins via
    # apply_endpoint_wiring below, where they're actually consumed.
    reserved_params = collect_reserved_endpoint_params(plugin_manager)
    for entry in raw.get("endpoints", []) or []:
        if "type" not in entry:
            raise BusBootError(f"endpoint entry missing required 'type' field: {entry!r}")
        if "name" not in entry:
            raise BusBootError(f"endpoint entry missing required 'name' field: {entry!r}")
        endpoint_type = str(entry["type"])
        cls = endpoint_types.get(endpoint_type)
        if cls is None:
            raise BusBootError(f"unknown endpoint type: {endpoint_type!r}")
        params = entry.get("params", {})
        constructor_params = {k: v for k, v in params.items() if k not in reserved_params}
        # Runner-side convention (not enforced by the Endpoint Protocol):
        # every endpoint class must accept `name` as a constructor kwarg.
        # The Protocol only requires `name` as an *attribute*; this convention
        # is what lets the runner construct from YAML without per-class adapters.
        try:
            instance = cls(name=entry["name"], **constructor_params)
        except Exception as exc:
            raise BusBootError(
                f"endpoint type {endpoint_type!r} does not satisfy Endpoint protocol: {exc}"
            ) from exc
        if not isinstance(instance, Endpoint):
            raise BusBootError(
                f"endpoint type {endpoint_type!r} does not satisfy Endpoint protocol"
            )
        plugin_manager.hook.configure_endpoint_instance(
            instance=instance,
            endpoint_name=entry["name"],
            endpoint_config=entry,
            services=services,
        )
        bus.register(EndpointSpec(endpoint=instance, description=entry.get("description", "")))

    # Cross-endpoint wiring (T19 — cutover #09 follow-up). Once every
    # endpoint in the yaml has been constructed and registered on the bus
    # (but before bus.start), give plugins a chance to install deferred
    # wiring that names sibling endpoints. The briefs plugin uses this to
    # pair a briefs orchestrator with a ClaudeCodeMCPEndpoint named via
    # ``params.briefs_orchestrator``.
    endpoints_by_name: dict[str, Endpoint] = {
        spec.name: spec.endpoint for spec in bus._endpoints_by_name.values()
    }
    raw_endpoint_configs: dict[str, dict[str, Any]] = {
        entry["name"]: entry for entry in (raw.get("endpoints", []) or [])
    }
    apply_endpoint_wiring(
        plugin_manager,
        endpoints=endpoints_by_name,
        raw_endpoint_configs=raw_endpoint_configs,
        services=services,
    )

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
