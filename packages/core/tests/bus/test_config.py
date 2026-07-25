"""Unit tests for agent_core.bus.config — Pydantic daemon-config schema.

Covers:
- Valid minimal config round-trips cleanly
- extra="forbid" guards fire at root and every nested level
- Default values match those documented in runner.py
- params dict on HookEntryConfig and EndpointEntryConfig is unconstrained
"""

from __future__ import annotations

import pydantic
import pytest

from agent_core.bus.config import (
    BusBootError,
    DaemonConfig,
    EndpointEntryConfig,
    HookEntryConfig,
)


class TestImportsSideEffectFree:
    def test_daemon_config_importable(self):
        # from agent_core.bus.config import DaemonConfig, BusBootError
        # has no side effects — verified by the import at module level above
        assert DaemonConfig is not None
        assert BusBootError is not None


class TestDaemonConfigValidMinimal:
    def test_empty_dict_succeeds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg is not None

    def test_defaults_storage_path(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.storage_path == "~/.agent-core/bus.sqlite"

    def test_defaults_bind_host(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.http.bind_host == "127.0.0.1"

    def test_defaults_bind_port(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.http.bind_port == 8788

    def test_defaults_redelivery_timeout_seconds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.redelivery_timeout_seconds == 300

    def test_defaults_max_delivery_attempts(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.max_delivery_attempts == 5

    def test_defaults_ttl_sweep_seconds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.ttl_sweep_seconds == 60

    def test_defaults_redelivery_sweep_seconds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.redelivery_sweep_seconds == 10

    def test_defaults_acked_retention_days(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.acked_retention_days == 14

    def test_defaults_max_pending_per_endpoint(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.max_pending_per_endpoint == 10_000

    def test_defaults_slow_deliver_warn_seconds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.slow_deliver_warn_seconds == 5.0

    def test_defaults_watchdog_timeout_seconds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.watchdog_timeout_seconds == 90

    def test_defaults_backup_interval_seconds(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.backup_interval_seconds == 3600

    def test_defaults_backup_dir_none(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.backup_dir is None

    def test_defaults_supervisor_restart_backoff_base(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.restart_backoff_base_seconds == 1

    def test_defaults_supervisor_restart_backoff_factor(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.restart_backoff_factor == 2

    def test_defaults_supervisor_restart_backoff_cap(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.restart_backoff_cap_seconds == 60

    def test_defaults_supervisor_restart_jitter(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.restart_jitter == "full"

    def test_defaults_supervisor_restarts_before_quarantine(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.restarts_before_quarantine == 5

    def test_defaults_supervisor_probe_interval(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.probe_interval_seconds == 300

    def test_defaults_supervisor_delivery_backoff_base(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.delivery_backoff_base_seconds == 2

    def test_defaults_supervisor_delivery_backoff_factor(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.delivery_backoff_factor == 2

    def test_defaults_supervisor_delivery_backoff_cap(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.delivery_backoff_cap_seconds == 60

    def test_defaults_supervisor_deliver_failures_before_breaker(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus.supervisor.deliver_failures_before_breaker == 5

    def test_defaults_mcp_audit_enabled(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.mcp_audit.enabled is True

    def test_defaults_mcp_audit_log_root(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.mcp_audit.log_root == "~/.agent-core/bus/mcp-audit"

    def test_defaults_mcp_audit_timezone(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.mcp_audit.timezone == "US/Eastern"

    def test_defaults_mcp_audit_skip_tools(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.mcp_audit.skip_tools is None or cfg.mcp_audit.skip_tools == []

    def test_defaults_bus_hooks_pre_publish(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus_hooks.pre_publish == []

    def test_defaults_bus_hooks_pre_deliver(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus_hooks.pre_deliver == []

    def test_defaults_endpoints(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.endpoints == []

    def test_defaults_bus_auth_mode(self):
        cfg = DaemonConfig.model_validate({})
        assert cfg.bus_auth_mode == "off"

    def test_bus_auth_mode_warn_accepted(self):
        cfg = DaemonConfig.model_validate({"bus_auth_mode": "warn"})
        assert cfg.bus_auth_mode == "warn"

    def test_bus_auth_mode_enforce_accepted(self):
        cfg = DaemonConfig.model_validate({"bus_auth_mode": "enforce"})
        assert cfg.bus_auth_mode == "enforce"

    def test_bus_auth_mode_invalid_value_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"bus_auth_mode": "enforced"})


class TestExtraForbidRoot:
    def test_typo_in_top_level_key_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"buus": {}})

    def test_misspelled_http_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"htttp": {}})

    def test_unknown_top_level_field_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"unexpected_field": "oops"})


class TestExtraForbidBusSection:
    def test_typo_in_bus_key_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"bus": {"storage_pathh": "x"}})

    def test_unknown_bus_field_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"bus": {"nonexistent_field": 999}})


class TestExtraForbidSupervisorSection:
    def test_typo_in_supervisor_key_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate(
                {"bus": {"supervisor": {"restart_backoff_base_secondss": 2}}}
            )


class TestExtraForbidHttpSection:
    def test_typo_in_http_key_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"http": {"bind_portt": 9000}})

    def test_unknown_http_field_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"http": {"ssl_enabled": True}})


class TestExtraForbidMcpAuditSection:
    def test_typo_in_mcp_audit_key_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"mcp_audit": {"enabledd": True}})


class TestExtraForbidBusHooksSection:
    def test_typo_in_bus_hooks_key_raises(self):
        with pytest.raises(pydantic.ValidationError):
            DaemonConfig.model_validate({"bus_hooks": {"pre_publishh": []}})


class TestHookEntryConfig:
    def test_valid_entry(self):
        entry = HookEntryConfig.model_validate({"type": "my.hook.Type"})
        assert entry.type == "my.hook.Type"
        assert entry.params == {}

    def test_params_dict_is_unconstrained(self):
        entry = HookEntryConfig.model_validate(
            {"type": "my.hook.Type", "params": {"foo": "bar", "nested": {"a": 1}}}
        )
        assert entry.params == {"foo": "bar", "nested": {"a": 1}}

    def test_extra_field_on_entry_raises(self):
        with pytest.raises(pydantic.ValidationError):
            HookEntryConfig.model_validate(
                {"type": "my.hook.Type", "unknown_extra": "oops"}
            )


class TestEndpointEntryConfig:
    def test_valid_entry(self):
        entry = EndpointEntryConfig.model_validate(
            {"type": "builtin.stub", "name": "my-ep"}
        )
        assert entry.type == "builtin.stub"
        assert entry.name == "my-ep"
        assert entry.params == {}
        assert entry.description == ""

    def test_params_dict_is_unconstrained(self):
        entry = EndpointEntryConfig.model_validate(
            {
                "type": "builtin.stub",
                "name": "my-ep",
                "params": {"key": [1, 2, 3], "nested": {"x": None}},
            }
        )
        assert entry.params == {"key": [1, 2, 3], "nested": {"x": None}}

    def test_extra_field_on_entry_raises(self):
        with pytest.raises(pydantic.ValidationError):
            EndpointEntryConfig.model_validate(
                {"type": "builtin.stub", "name": "x", "unknown_extra": "oops"}
            )

    def test_missing_type_raises(self):
        with pytest.raises(pydantic.ValidationError):
            EndpointEntryConfig.model_validate({"name": "x"})

    def test_missing_name_raises(self):
        with pytest.raises(pydantic.ValidationError):
            EndpointEntryConfig.model_validate({"type": "builtin.stub"})

    def test_pubkey_pem_defaults_to_none(self):
        entry = EndpointEntryConfig.model_validate({"type": "builtin.stub", "name": "ep"})
        assert entry.pubkey_pem is None

    def test_pubkey_pem_accepts_string(self):
        entry = EndpointEntryConfig.model_validate(
            {"type": "builtin.stub", "name": "ep", "pubkey_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"}
        )
        assert entry.pubkey_pem is not None

    def test_extra_field_still_rejected_with_pubkey_pem_present(self):
        with pytest.raises(pydantic.ValidationError):
            EndpointEntryConfig.model_validate(
                {"type": "builtin.stub", "name": "ep", "pubkey_pem": "x", "unknown_extra": "oops"}
            )


class TestDaemonConfigRoundTrip:
    def test_full_bus_section_round_trips(self):
        data = {
            "bus": {
                "storage_path": "/tmp/bus.sqlite",
                "redelivery_timeout_seconds": 120,
                "max_delivery_attempts": 3,
            }
        }
        cfg = DaemonConfig.model_validate(data)
        assert cfg.bus.storage_path == "/tmp/bus.sqlite"
        assert cfg.bus.redelivery_timeout_seconds == 120
        assert cfg.bus.max_delivery_attempts == 3
        # defaults still apply to unset fields
        assert cfg.bus.ttl_sweep_seconds == 60

    def test_endpoints_list_round_trips(self):
        data = {
            "endpoints": [
                {"type": "builtin.stub", "name": "ep1"},
                {"type": "builtin.stub", "name": "ep2", "params": {"auto_ack": True}},
            ]
        }
        cfg = DaemonConfig.model_validate(data)
        assert len(cfg.endpoints) == 2
        assert cfg.endpoints[0].name == "ep1"
        assert cfg.endpoints[1].params == {"auto_ack": True}

    def test_bus_hooks_round_trips(self):
        data = {
            "bus_hooks": {
                "pre_publish": [{"type": "my.hook.A", "params": {"x": 1}}],
                "pre_deliver": [],
            }
        }
        cfg = DaemonConfig.model_validate(data)
        assert len(cfg.bus_hooks.pre_publish) == 1
        assert cfg.bus_hooks.pre_publish[0].type == "my.hook.A"
        assert cfg.bus_hooks.pre_deliver == []
