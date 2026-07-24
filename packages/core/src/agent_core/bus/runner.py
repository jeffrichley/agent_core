"""Boot sequence — load YAML, instantiate endpoints, register, start.

The runner is the only place that imports endpoint classes by string. It also
enforces v1 invariants: loopback-only bind unless an auth hook is configured
(BACKLOG: auth for non-loopback bind).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from agent_core.bus.auth.pubkey_registry import build_pubkey_registry
from agent_core.bus.config import (
    BusBootError,
    DaemonConfig,
    HttpConfig,
)
from agent_core.bus.core import Bus, BusConfig, BusHookSpec, EndpointSpec, SupervisorConfig
from agent_core.bus.http_host import HTTPHost, MCPHostable
from agent_core.bus.notify_broker import NotificationBroker
from agent_core.bus.protocol import BusHook, Endpoint
from agent_core.mcp_audit.writer import MCPAuditWriter
from agent_core.plugins.manager import (
    apply_endpoint_wiring,
    collect_reserved_endpoint_params,
    create_plugin_manager,
    get_bus_hook_types,
    get_endpoint_types,
)
from agent_core.plugins.specs import RunnerServices

logger = logging.getLogger(__name__)


class _EntryBusBootError(BusBootError):
    """Degradable per-entry validation failure.

    Raised instead of plain BusBootError so that the per-entry try/except
    in build_bus_from_config can catch entry-level failures without silencing
    name collisions (which raise plain BusBootError via bus.register()).
    """


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _validate_http(http_cfg: HttpConfig, has_auth_hook: bool) -> None:
    if http_cfg.bind_host not in _LOOPBACK_HOSTS and not has_auth_hook:
        raise BusBootError(
            f"http.bind_host={http_cfg.bind_host!r} is non-loopback but no auth hook is configured. "
            "v1 supports loopback only; see BACKLOG for the auth hook trigger."
        )


async def build_bus_from_config(path: Path) -> tuple[Bus, HTTPHost | None]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    # Conf.d-style merge: every <config_dir>/endpoints.d/*.yaml fragment
    # contributes its `endpoints:` list to the merged set. Sorted glob
    # ensures deterministic load order. Fragments may not override
    # bus/http/bus_hooks/mcp_audit. Endpoint name collisions surface
    # via existing endpoint registration code (loud failure).
    fragments_dir = Path(path).parent / "endpoints.d"
    if fragments_dir.is_dir():
        for fragment_path in sorted(fragments_dir.glob("*.yaml")):
            try:
                fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                logger.error(
                    "endpoints.d fragment %r: YAML parse error — quarantining fragment, "
                    "boot continues: %s",
                    fragment_path.name,
                    exc,
                )
                continue
            fragment_endpoints = fragment.get("endpoints", []) or []
            if not isinstance(fragment_endpoints, list):
                logger.error(
                    "endpoints.d fragment %r: 'endpoints' must be a list, got %r — "
                    "quarantining fragment, boot continues",
                    fragment_path.name,
                    type(fragment_endpoints).__name__,
                )
                continue
            raw.setdefault("endpoints", []).extend(fragment_endpoints)
    plugin_manager = create_plugin_manager()
    plugin_manager.hook.validate_config(raw_config=raw)
    daemon_cfg = DaemonConfig.model_validate(raw)  # named daemon_cfg to avoid collision with cfg = BusConfig(...) below
    pubkey_registry = build_pubkey_registry(daemon_cfg.endpoints)
    endpoint_types = get_endpoint_types(plugin_manager)
    bus_hook_types = get_bus_hook_types(plugin_manager)

    storage_path = Path(daemon_cfg.bus.storage_path).expanduser()
    supervisor = SupervisorConfig(
        restart_backoff_base_seconds=daemon_cfg.bus.supervisor.restart_backoff_base_seconds,
        restart_backoff_factor=daemon_cfg.bus.supervisor.restart_backoff_factor,
        restart_backoff_cap_seconds=daemon_cfg.bus.supervisor.restart_backoff_cap_seconds,
        restart_jitter=daemon_cfg.bus.supervisor.restart_jitter,
        restarts_before_quarantine=daemon_cfg.bus.supervisor.restarts_before_quarantine,
        probe_interval_seconds=daemon_cfg.bus.supervisor.probe_interval_seconds,
        delivery_backoff_base_seconds=daemon_cfg.bus.supervisor.delivery_backoff_base_seconds,
        delivery_backoff_factor=daemon_cfg.bus.supervisor.delivery_backoff_factor,
        delivery_backoff_cap_seconds=daemon_cfg.bus.supervisor.delivery_backoff_cap_seconds,
        deliver_failures_before_breaker=daemon_cfg.bus.supervisor.deliver_failures_before_breaker,
    )
    cfg = BusConfig(
        storage_path=storage_path,
        redelivery_timeout_seconds=daemon_cfg.bus.redelivery_timeout_seconds,
        max_delivery_attempts=daemon_cfg.bus.max_delivery_attempts,
        ttl_sweep_seconds=daemon_cfg.bus.ttl_sweep_seconds,
        redelivery_sweep_seconds=daemon_cfg.bus.redelivery_sweep_seconds,
        acked_retention_days=daemon_cfg.bus.acked_retention_days,
        max_pending_per_endpoint=daemon_cfg.bus.max_pending_per_endpoint,
        slow_deliver_warn_seconds=float(
            os.environ.get("BUS_SLOW_DELIVER_WARN_SECONDS", daemon_cfg.bus.slow_deliver_warn_seconds)
        ),
        watchdog_timeout_seconds=int(
            os.environ.get("BUS_WATCHDOG_TIMEOUT_SECONDS", daemon_cfg.bus.watchdog_timeout_seconds)
        ),
        supervisor=supervisor,
        backup_dir=Path(daemon_cfg.bus.backup_dir).expanduser() if daemon_cfg.bus.backup_dir else None,
        backup_interval_seconds=int(daemon_cfg.bus.backup_interval_seconds),
    )

    bus = Bus(cfg)

    # Broker for /notify/<agent> SSE fan-out. Created here so endpoints
    # constructed below can be wired to publish push summaries through it,
    # and HTTPHost can serve subscribers off the same instance.
    notify_broker = NotificationBroker()

    # MCP audit (top-level `mcp_audit:` YAML block, optional).
    audit_enabled = bool(daemon_cfg.mcp_audit.enabled)
    mcp_audit_writer: MCPAuditWriter | None = None
    mcp_audit_skip_tools: frozenset[str] = frozenset()
    if audit_enabled:
        log_root = daemon_cfg.mcp_audit.log_root
        tz = daemon_cfg.mcp_audit.timezone
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc:
            raise BusBootError(
                f"mcp_audit.timezone is invalid: {tz!r}"
            ) from exc
        mcp_audit_writer = MCPAuditWriter(log_root=log_root, timezone=tz)
        skip_raw = daemon_cfg.mcp_audit.skip_tools
        if not isinstance(skip_raw, list):
            raise BusBootError(
                f"mcp_audit.skip_tools must be a list, got {type(skip_raw).__name__}"
            )
        mcp_audit_skip_tools = frozenset(str(s) for s in skip_raw)

    services = RunnerServices(
        notify_broker=notify_broker,
        mcp_audit_writer=mcp_audit_writer,
        mcp_audit_skip_tools=mcp_audit_skip_tools,
    )

    # Hooks (no auth-aware filtering yet — Phase 2 will add it).
    # TODO(Phase 2): scan loaded hooks for auth-hook interface and set True.
    # Until then, non-loopback bind is always refused. See BACKLOG.md.
    has_auth_hook = False
    for stage in ("pre_publish", "pre_deliver"):
        for entry in getattr(daemon_cfg.bus_hooks, stage):
            hook_type = entry.type
            cls = bus_hook_types.get(hook_type)
            if cls is None:
                raise BusBootError(f"unknown bus hook type: {hook_type!r}")
            try:
                instance = cls(**entry.params)
            except Exception as exc:
                raise BusBootError(
                    f"bus hook type {hook_type!r} does not satisfy BusHook protocol: {exc}"
                ) from exc
            if not isinstance(instance, BusHook):
                raise BusBootError(f"bus hook type {hook_type!r} does not satisfy BusHook protocol")
            plugin_manager.hook.configure_bus_hook_instance(
                instance=instance,
                stage=stage,
                hook_config=entry.model_dump(),
                services=services,
            )
            bus.register_hook(stage, BusHookSpec(hook=instance, params=entry.params))

    # HTTP guardrail.
    _validate_http(daemon_cfg.http, has_auth_hook)

    # Endpoints.
    # Plugin-managed param names (e.g., briefs_orchestrator) get popped before
    # construction so endpoint classes don't have to swallow them. The full
    # raw config (including the popped keys) still reaches plugins via
    # apply_endpoint_wiring below, where they're actually consumed.
    reserved_params = collect_reserved_endpoint_params(plugin_manager)
    for entry in daemon_cfg.endpoints:
        try:
            endpoint_type = entry.type
            cls = endpoint_types.get(endpoint_type)
            if cls is None:
                raise _EntryBusBootError(f"unknown endpoint type: {endpoint_type!r}")
            params = entry.params
            constructor_params = {k: v for k, v in params.items() if k not in reserved_params}
            # Runner-side convention (not enforced by the Endpoint Protocol):
            # every endpoint class must accept `name` as a constructor kwarg.
            # The Protocol only requires `name` as an *attribute*; this convention
            # is what lets the runner construct from YAML without per-class adapters.
            try:
                instance = cls(name=entry.name, **constructor_params)
            except Exception as exc:
                raise _EntryBusBootError(
                    f"endpoint type {endpoint_type!r} does not satisfy Endpoint protocol: {exc}"
                ) from exc
            if not isinstance(instance, Endpoint):
                raise _EntryBusBootError(
                    f"endpoint type {endpoint_type!r} does not satisfy Endpoint protocol"
                )
            plugin_manager.hook.configure_endpoint_instance(
                instance=instance,
                endpoint_name=entry.name,
                endpoint_config=entry.model_dump(),
                services=services,
            )
            try:
                bus.register(
                    EndpointSpec(endpoint=instance, description=entry.description)
                )
            except ValueError as exc:
                # Name collision: loud config conflict, NOT an _EntryBusBootError.
                raise BusBootError(str(exc)) from exc
        except _EntryBusBootError as exc:
            logger.error(
                "endpoint entry name=%r type=%r: %s — skipping entry, boot continues",
                entry.name,
                entry.type,
                exc,
            )
            continue

    # Cross-endpoint wiring (T19 — cutover #09 follow-up). Once every
    # endpoint in the yaml has been constructed and registered on the bus
    # (but before bus.start), give plugins a chance to install deferred
    # wiring that names sibling endpoints. The briefs plugin uses this to
    # pair a briefs orchestrator with a ClaudeCodeMCPEndpoint named via
    # ``params.briefs_orchestrator``.
    endpoints_by_name: dict[str, Endpoint] = {
        spec.name: spec.endpoint for spec in bus._endpoints_by_name.values()
    }
    # Filter to successfully-registered endpoints only: entries that fail per-entry registration
    # (unknown type, protocol violation, duplicate name) are absent from endpoints_by_name and
    # must be excluded here so wire_endpoints_after_registration is not passed phantom entries.
    raw_endpoint_configs: dict[str, dict[str, Any]] = {
        entry.name: entry.model_dump()
        for entry in daemon_cfg.endpoints
        if entry.name in endpoints_by_name
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
        http_host = HTTPHost(
            bind_host=daemon_cfg.http.bind_host,
            bind_port=daemon_cfg.http.bind_port,
            notify_broker=notify_broker,
            notify_snapshot=bus.snapshot_for_agent,
            pubkey_registry=pubkey_registry,
        )
        for h in hostable:
            http_host.mount(h)

    return bus, http_host
