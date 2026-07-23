"""Unit tests for agent_core.venv.mcp_config (C2-2, issue #316)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_core.venv.mcp_config import (
    MCP_JSON_NAME,
    build_mcp_config,
    generate_mcp_json,
    mcp_json_needs_repair,
    repair_mcp_json,
    stable_interpreter,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Path.home() at a temp dir so being/daemon homes are isolated."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _expected_python(home: Path, being: str) -> Path:
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"
    return home / f".{being}" / ".venv" / bin_dir / exe


# ---------------------------------------------------------------------------
# stable_interpreter — the headline P0 regression guard
# ---------------------------------------------------------------------------

class TestStableInterpreter:
    def test_points_at_stable_venv(self, home: Path) -> None:
        assert stable_interpreter("wren") == _expected_python(home, "wren")

    def test_is_absolute(self, home: Path) -> None:
        assert stable_interpreter("wren").is_absolute()

    def test_not_version_stamped(self, home: Path) -> None:
        # The whole point of C2-2: no ".venv-<version>" and no "venvs/<ver>".
        text = str(stable_interpreter("pepper"))
        assert ".venv-" not in text
        assert f"{Path('venvs')}" not in text

    def test_per_being_home(self, home: Path) -> None:
        assert stable_interpreter("pepper") == _expected_python(home, "pepper")


# ---------------------------------------------------------------------------
# build_mcp_config — the canonical document
# ---------------------------------------------------------------------------

class TestBuildMcpConfig:
    def test_three_servers_with_expected_keys(self) -> None:
        doc = build_mcp_config("wren", daemon_url="http://127.0.0.1:8789", interpreter=Path("/x/py"))
        assert set(doc["mcpServers"]) == {"agent-core", "agent-core-channel", "notify"}

    def test_every_command_is_the_interpreter(self) -> None:
        doc = build_mcp_config("wren", daemon_url="http://127.0.0.1:8789", interpreter=Path("/x/py"))
        for server in doc["mcpServers"].values():
            assert server["type"] == "stdio"
            assert server["command"] == str(Path("/x/py"))

    def test_busproxy_and_channel_carry_agent_and_url(self) -> None:
        doc = build_mcp_config("wren", daemon_url="http://127.0.0.1:9999", interpreter=Path("/x/py"))
        assert doc["mcpServers"]["agent-core"]["args"] == [
            "-m", "agent_core_busproxy", "--agent", "wren", "--daemon-url", "http://127.0.0.1:9999",
        ]
        assert doc["mcpServers"]["agent-core-channel"]["args"] == [
            "-m", "agent_core_channel", "--agent", "wren", "--daemon-url", "http://127.0.0.1:9999",
        ]

    def test_notify_has_no_agent_or_url(self) -> None:
        doc = build_mcp_config("wren", daemon_url="http://127.0.0.1:8789", interpreter=Path("/x/py"))
        assert doc["mcpServers"]["notify"]["args"] == ["-m", "agent_core_notify.mcp_server"]


# ---------------------------------------------------------------------------
# _daemon_url — port resolution from the daemon config
# ---------------------------------------------------------------------------

class TestDaemonUrl:
    def test_defaults_when_no_config(self, tmp_path: Path) -> None:
        doc = generate_mcp_json("wren", vault_root=tmp_path / "v", daemon_config_dir=tmp_path / "d")
        args = json.loads(doc.read_text())["mcpServers"]["agent-core"]["args"]
        assert args[-1] == "http://127.0.0.1:8789"

    def test_reads_bind_port_from_config(self, tmp_path: Path) -> None:
        dcfg = tmp_path / "d"
        dcfg.mkdir()
        (dcfg / "agent_core.yaml").write_text("http:\n  bind_host: 0.0.0.0\n  bind_port: 9001\n")
        doc = generate_mcp_json("wren", vault_root=tmp_path / "v", daemon_config_dir=dcfg)
        args = json.loads(doc.read_text())["mcpServers"]["agent-core"]["args"]
        # Host is forced to loopback even though the config binds 0.0.0.0.
        assert args[-1] == "http://127.0.0.1:9001"

    def test_malformed_config_falls_back(self, tmp_path: Path) -> None:
        dcfg = tmp_path / "d"
        dcfg.mkdir()
        (dcfg / "agent_core.yaml").write_text(": not valid yaml :\n  - [")
        doc = generate_mcp_json("wren", vault_root=tmp_path / "v", daemon_config_dir=dcfg)
        args = json.loads(doc.read_text())["mcpServers"]["agent-core"]["args"]
        assert args[-1] == "http://127.0.0.1:8789"

    def test_non_mapping_config_falls_back(self, tmp_path: Path) -> None:
        # Valid YAML that parses to a list, not a mapping.
        dcfg = tmp_path / "d"
        dcfg.mkdir()
        (dcfg / "agent_core.yaml").write_text("- just\n- a\n- list\n")
        doc = generate_mcp_json("wren", vault_root=tmp_path / "v", daemon_config_dir=dcfg)
        args = json.loads(doc.read_text())["mcpServers"]["agent-core"]["args"]
        assert args[-1] == "http://127.0.0.1:8789"


# ---------------------------------------------------------------------------
# generate_mcp_json — writing
# ---------------------------------------------------------------------------

class TestGenerateMcpJson:
    def test_writes_file_and_returns_path(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        path = generate_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        assert path == vault / MCP_JSON_NAME
        assert path.is_file()

    def test_creates_missing_parent(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "deep" / "vault"
        path = generate_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        assert path.is_file()

    def test_command_is_the_stable_interpreter(self, home: Path, tmp_path: Path) -> None:
        path = generate_mcp_json("wren", vault_root=tmp_path / "v", daemon_config_dir=tmp_path / "d")
        doc = json.loads(path.read_text())
        assert doc["mcpServers"]["agent-core"]["command"] == str(_expected_python(home, "wren"))

    def test_overwrites_drifted_version_stamped_file(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        vault.mkdir()
        drifted = {
            "mcpServers": {
                "agent-core": {
                    "type": "stdio",
                    "command": r"C:\Users\x\.agent-core\.venv-v0.7.0\Scripts\python.exe",
                    "args": ["-m", "agent_core_busproxy", "--agent", "wren", "--daemon-url", "http://127.0.0.1:8789"],
                }
            }
        }
        (vault / MCP_JSON_NAME).write_text(json.dumps(drifted))
        path = generate_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        doc = json.loads(path.read_text())
        assert ".venv-" not in doc["mcpServers"]["agent-core"]["command"]
        assert set(doc["mcpServers"]) == {"agent-core", "agent-core-channel", "notify"}


# ---------------------------------------------------------------------------
# repair — idempotent drift detection
# ---------------------------------------------------------------------------

class TestRepair:
    def test_needs_repair_when_missing(self, home: Path, tmp_path: Path) -> None:
        assert mcp_json_needs_repair("wren", vault_root=tmp_path / "v", daemon_config_dir=tmp_path / "d")

    def test_needs_repair_when_unparseable(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        vault.mkdir()
        (vault / MCP_JSON_NAME).write_text("{ not json")
        assert mcp_json_needs_repair("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")

    def test_canonical_file_needs_no_repair(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        generate_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        assert not mcp_json_needs_repair("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")

    def test_repair_leaves_canonical_untouched(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        generate_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        before = (vault / MCP_JSON_NAME).read_text()
        path, changed = repair_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        assert changed is False
        assert path.read_text() == before

    def test_repair_rewrites_drift(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        vault.mkdir()
        (vault / MCP_JSON_NAME).write_text('{"mcpServers": {"stale": {}}}')
        path, changed = repair_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        assert changed is True
        assert set(json.loads(path.read_text())["mcpServers"]) == {
            "agent-core", "agent-core-channel", "notify",
        }

    def test_generate_then_repair_is_stable(self, home: Path, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        generate_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        _, changed = repair_mcp_json("wren", vault_root=vault, daemon_config_dir=tmp_path / "d")
        assert changed is False
