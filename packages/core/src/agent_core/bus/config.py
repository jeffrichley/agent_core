"""Pydantic daemon-config schema for the agent_core bus.

Defines ``BusBootError`` and ``DaemonConfig`` (plus sub-models) for the
top-level daemon YAML.  All models use ``extra="forbid"`` so a typo'd
parameter name is caught at boot with a clear ``pydantic.ValidationError``
instead of a silent ``KeyError`` mid-boot.

Importable by the hatchery for correct-by-construction config generation
(Cluster β dependency — Cα-1 foundation).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BusBootError(Exception):
    """Raised when the runner cannot construct a valid Bus from the config."""


class SupervisorSectionConfig(BaseModel):
    """Maps to the ``bus.supervisor:`` YAML block."""

    model_config = ConfigDict(extra="forbid")

    restart_backoff_base_seconds: int = 1
    restart_backoff_factor: int = 2
    restart_backoff_cap_seconds: int = 60
    restart_jitter: str = "full"
    restarts_before_quarantine: int = 5
    probe_interval_seconds: int = 300
    delivery_backoff_base_seconds: int = 2
    delivery_backoff_factor: int = 2
    delivery_backoff_cap_seconds: int = 60
    deliver_failures_before_breaker: int = 5


class BusSectionConfig(BaseModel):
    """Maps to the ``bus:`` YAML block."""

    model_config = ConfigDict(extra="forbid")

    storage_path: str = "~/.agent-core/bus.sqlite"
    supervisor: SupervisorSectionConfig = Field(default_factory=SupervisorSectionConfig)
    redelivery_timeout_seconds: int = 300
    max_delivery_attempts: int = 5
    ttl_sweep_seconds: int = 60
    redelivery_sweep_seconds: int = 10
    acked_retention_days: int = 14
    max_pending_per_endpoint: int = 10_000
    slow_deliver_warn_seconds: float = 5.0
    watchdog_timeout_seconds: int = 90
    backup_dir: str | None = None
    backup_interval_seconds: int = 3600


class HttpConfig(BaseModel):
    """Maps to the ``http:`` YAML block."""

    model_config = ConfigDict(extra="forbid")

    bind_host: str = "127.0.0.1"
    bind_port: int = 8788


class HookEntryConfig(BaseModel):
    """One entry in ``bus_hooks.pre_publish`` or ``bus_hooks.pre_deliver``."""

    model_config = ConfigDict(extra="forbid")

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class BusHooksConfig(BaseModel):
    """Maps to the ``bus_hooks:`` YAML block."""

    model_config = ConfigDict(extra="forbid")

    pre_publish: list[HookEntryConfig] = Field(default_factory=list)
    pre_deliver: list[HookEntryConfig] = Field(default_factory=list)


class McpAuditConfig(BaseModel):
    """Maps to the ``mcp_audit:`` YAML block."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    log_root: str = "~/.agent-core/bus/mcp-audit"
    timezone: str = "US/Eastern"
    # Typed as Any (not list[Any]) so the runtime isinstance guard in runner.py
    # is the designated validator — preserving the "skip_tools must be a list"
    # error message from test_runner_raises_bus_boot_error_when_skip_tools_is_not_a_list.
    skip_tools: Any = Field(default_factory=list)


class EndpointEntryConfig(BaseModel):
    """One entry in the ``endpoints:`` YAML list."""

    model_config = ConfigDict(extra="forbid")

    type: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class LoggingConfig(BaseModel):
    """Maps to the ``logging:`` YAML block.

    ``format`` selects the log handler: ``'json'`` for structured JSON output
    (production), ``'pretty'`` for human-readable text (dev, default).
    An unknown value raises ``pydantic.ValidationError`` at boot.
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "pretty"] = "pretty"


class DaemonConfig(BaseModel):
    """Root daemon YAML config model.

    All sections are optional with defaults matching the historical
    ``dict.get()`` defaults in ``runner.py``.
    """

    model_config = ConfigDict(extra="forbid")

    bus: BusSectionConfig = Field(default_factory=BusSectionConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    bus_hooks: BusHooksConfig = Field(default_factory=BusHooksConfig)
    mcp_audit: McpAuditConfig = Field(default_factory=McpAuditConfig)
    endpoints: list[EndpointEntryConfig] = Field(default_factory=list)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
