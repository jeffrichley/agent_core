"""Unit tests for SupervisorConfig and its integration into BusConfig / Bus.start()."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.bus.core import Bus, BusConfig, SupervisorConfig


class TestSupervisorConfigDefaults:
    def test_all_defaults_present(self):
        cfg = SupervisorConfig()
        assert cfg.restart_backoff_base_seconds == 1
        assert cfg.restart_backoff_factor == 2
        assert cfg.restart_backoff_cap_seconds == 60
        assert cfg.restart_jitter == "full"
        assert cfg.restarts_before_quarantine == 5
        assert cfg.probe_interval_seconds == 300
        assert cfg.delivery_backoff_base_seconds == 2
        assert cfg.delivery_backoff_factor == 2
        assert cfg.delivery_backoff_cap_seconds == 60
        assert cfg.deliver_failures_before_breaker == 5


class TestSupervisorConfigOverrides:
    def test_overrides_take(self):
        cfg = SupervisorConfig(
            restart_backoff_base_seconds=3,
            restart_backoff_factor=4,
            restart_backoff_cap_seconds=120,
            restart_jitter="none",
            restarts_before_quarantine=10,
            probe_interval_seconds=600,
            delivery_backoff_base_seconds=5,
            delivery_backoff_factor=3,
            delivery_backoff_cap_seconds=90,
            deliver_failures_before_breaker=8,
        )
        assert cfg.restart_backoff_base_seconds == 3
        assert cfg.restart_backoff_factor == 4
        assert cfg.restart_backoff_cap_seconds == 120
        assert cfg.restart_jitter == "none"
        assert cfg.restarts_before_quarantine == 10
        assert cfg.probe_interval_seconds == 600
        assert cfg.delivery_backoff_base_seconds == 5
        assert cfg.delivery_backoff_factor == 3
        assert cfg.delivery_backoff_cap_seconds == 90
        assert cfg.deliver_failures_before_breaker == 8


class TestSupervisorConfigValidation:
    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("restart_backoff_base_seconds", 0),
            ("restart_backoff_base_seconds", -1),
            ("restart_backoff_cap_seconds", 0),
            ("restart_backoff_cap_seconds", -5),
            ("probe_interval_seconds", 0),
            ("probe_interval_seconds", -100),
            ("delivery_backoff_base_seconds", 0),
            ("delivery_backoff_base_seconds", -3),
            ("delivery_backoff_cap_seconds", 0),
            ("delivery_backoff_cap_seconds", -1),
        ],
    )
    def test_seconds_fields_reject_zero_and_negative(self, field: str, bad_value: int):
        with pytest.raises(ValueError, match=field):
            SupervisorConfig(**{field: bad_value})

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("restart_backoff_factor", 0),
            ("restart_backoff_factor", -1),
            ("delivery_backoff_factor", 0),
            ("delivery_backoff_factor", -2),
        ],
    )
    def test_factor_fields_reject_less_than_one(self, field: str, bad_value: int):
        with pytest.raises(ValueError, match=field):
            SupervisorConfig(**{field: bad_value})

    @pytest.mark.parametrize("bad_value", [0, -1])
    def test_restarts_before_quarantine_rejects_less_than_one(self, bad_value: int):
        with pytest.raises(ValueError, match="restarts_before_quarantine"):
            SupervisorConfig(restarts_before_quarantine=bad_value)

    @pytest.mark.parametrize("bad_value", [0, -1])
    def test_deliver_failures_before_breaker_rejects_less_than_one(self, bad_value: int):
        with pytest.raises(ValueError, match="deliver_failures_before_breaker"):
            SupervisorConfig(deliver_failures_before_breaker=bad_value)

    @pytest.mark.parametrize("bad_value", ["half", "random", "", "FULL"])
    def test_restart_jitter_rejects_unknown_values(self, bad_value: str):
        with pytest.raises(ValueError, match="restart_jitter"):
            SupervisorConfig(restart_jitter=bad_value)

    @pytest.mark.parametrize("good_value", ["full", "equal", "none"])
    def test_restart_jitter_accepts_valid_values(self, good_value: str):
        cfg = SupervisorConfig(restart_jitter=good_value)
        assert cfg.restart_jitter == good_value


class TestBusConfigSupervisorField:
    def test_bus_config_has_supervisor_field(self, tmp_path: Path):
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite")
        assert isinstance(cfg.supervisor, SupervisorConfig)

    def test_bus_config_supervisor_defaults(self, tmp_path: Path):
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite")
        assert cfg.supervisor.restart_backoff_base_seconds == 1
        assert cfg.supervisor.restart_jitter == "full"

    def test_bus_config_supervisor_overridable(self, tmp_path: Path):
        sup = SupervisorConfig(restart_backoff_base_seconds=10)
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite", supervisor=sup)
        assert cfg.supervisor.restart_backoff_base_seconds == 10

    def test_bus_config_max_delivery_attempts_unchanged(self, tmp_path: Path):
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite")
        assert cfg.max_delivery_attempts == 5

    def test_each_instance_gets_fresh_supervisor(self, tmp_path: Path):
        cfg_a = BusConfig(storage_path=tmp_path / "a.sqlite")
        cfg_b = BusConfig(storage_path=tmp_path / "b.sqlite")
        assert cfg_a.supervisor is not cfg_b.supervisor


class TestBusStartSupervisorLog:
    async def test_boot_log_emitted_at_info(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite")
        bus = Bus(cfg)
        import logging

        with caplog.at_level(logging.INFO, logger="agent_core.bus.core"):
            await bus.start()
        await bus.stop()

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("supervisor" in m.lower() for m in info_messages), (
            f"No INFO log containing 'supervisor' found. Records: {info_messages}"
        )

    async def test_boot_log_contains_all_ten_values(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        sup = SupervisorConfig(
            restart_backoff_base_seconds=3,
            restart_backoff_factor=4,
            restart_backoff_cap_seconds=120,
            restart_jitter="equal",
            restarts_before_quarantine=7,
            probe_interval_seconds=600,
            delivery_backoff_base_seconds=5,
            delivery_backoff_factor=3,
            delivery_backoff_cap_seconds=90,
            deliver_failures_before_breaker=9,
        )
        cfg = BusConfig(storage_path=tmp_path / "bus.sqlite", supervisor=sup)
        bus = Bus(cfg)
        import logging

        with caplog.at_level(logging.INFO, logger="agent_core.bus.core"):
            await bus.start()
        await bus.stop()

        # All 10 values must appear somewhere in the INFO log output
        combined = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
        for val in ("3", "4", "120", "equal", "7", "600", "5", "3", "90", "9"):
            assert val in combined, f"Value {val!r} not found in supervisor boot log: {combined!r}"
