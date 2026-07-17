"""Unit tests for Hatcher's venv-build and .mcp.json-generation steps (Cβ-3, #327)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher


def _cfg(tmp_path: Path, **extra) -> HatchConfig:
    return HatchConfig(
        being_name="TestBeing",
        primary_human_name="Tester",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        **extra,
    )


def _stub_venv_builder(calls: list) -> callable:
    """Stub for _venv_builder: records calls; returns expected stable path."""
    def _build(target: str) -> Path:
        calls.append(target)
        return Path.home() / f".{target}" / ".venv"
    return _build


def _stub_mcp_gen(calls: list, vault: Path) -> callable:
    """Stub for _mcp_json_gen: records calls; writes a valid .mcp.json."""
    def _gen(**kwargs) -> Path:
        calls.append(kwargs)
        p = vault / ".testbeing" / ".mcp.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"mcpServers": {"agent-core-busproxy": {"command": "/fake/.venv/bin/python", "args": []}}}),
            encoding="utf-8",
        )
        return p
    return _gen


class TestBuildBeingVenvStep:
    def test_venv_builder_called_with_being_name_lower(self, tmp_path: Path) -> None:
        calls: list[str] = []
        cfg = _cfg(tmp_path)
        gen_calls: list = []
        hatcher = Hatcher(
            cfg,
            _venv_builder=_stub_venv_builder(calls),
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        )
        hatcher.hatch()

        assert "testbeing" in calls, f"expected 'testbeing' in venv_builder calls, got {calls}"

    def test_stable_venv_path_tracked_for_rollback(self, tmp_path: Path) -> None:
        calls: list[str] = []
        cfg = _cfg(tmp_path)
        gen_calls: list = []

        hatcher = Hatcher(
            cfg,
            _venv_builder=_stub_venv_builder(calls),
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        )
        hatcher.hatch()

        stable = Path.home() / ".testbeing" / ".venv"
        assert stable in hatcher._tracked_writes

    def test_venv_builder_not_called_on_init_missing(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        gen_calls: list = []
        # First hatch (normal)
        Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        calls: list[str] = []
        cfg_topup = cfg.model_copy(update={"init_missing": True})
        Hatcher(
            cfg_topup,
            _venv_builder=_stub_venv_builder(calls),
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        assert calls == [], f"venv_builder must not be called on init_missing; got {calls}"


class TestMcpJsonGenStep:
    def test_mcp_json_gen_called_after_venv_build(self, tmp_path: Path) -> None:
        venv_calls: list[str] = []
        gen_calls: list = []
        cfg = _cfg(tmp_path)

        call_order: list[str] = []

        def recording_venv(target: str) -> Path:
            call_order.append("venv")
            venv_calls.append(target)
            return Path.home() / f".{target}" / ".venv"

        def recording_gen(**kwargs) -> Path:
            call_order.append("mcp_gen")
            gen_calls.append(kwargs)
            p = cfg.resolved_vault_root() / ".mcp.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
            return p

        Hatcher(cfg, _venv_builder=recording_venv, _mcp_json_gen=recording_gen).hatch()

        assert call_order.index("venv") < call_order.index("mcp_gen"), (
            "venv must be built before .mcp.json is generated"
        )

    def test_mcp_json_path_tracked_for_rollback(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        mcp_path = cfg.resolved_vault_root() / ".mcp.json"
        gen_calls: list = []

        hatcher = Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        )
        hatcher.hatch()

        assert mcp_path in hatcher._tracked_writes

    def test_mcp_json_gen_not_called_on_init_missing(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        gen_calls: list = []

        Hatcher(
            cfg,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        gen_calls.clear()
        cfg_topup = cfg.model_copy(update={"init_missing": True})
        Hatcher(
            cfg_topup,
            _venv_builder=lambda t: Path.home() / f".{t}" / ".venv",
            _mcp_json_gen=_stub_mcp_gen(gen_calls, tmp_path),
        ).hatch()

        assert gen_calls == [], "mcp_json_gen must not be called on init_missing"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink rollback test")
class TestRollbackSymlink:
    def test_rollback_removes_stable_venv_symlink(self, tmp_path: Path) -> None:
        """_rollback() must unlink a symlink without following it into the venv content."""
        import os

        # Create a fake stable symlink target (doesn't need to be a real venv)
        venv_target = tmp_path / ".fake_venv_content"
        venv_target.mkdir()
        stable = tmp_path / ".testbeing_stable_link"
        os.symlink(venv_target, stable)

        hatcher = Hatcher.__new__(Hatcher)
        hatcher._tracked_writes = [stable]

        hatcher._rollback()

        assert not stable.exists() and not stable.is_symlink(), (
            "rollback must remove the stable symlink"
        )
        assert venv_target.exists(), (
            "rollback must NOT remove the symlink target (GC is C2-3's job)"
        )
