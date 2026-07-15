"""Unit tests for agent_core.daemon.config_hygiene (Cα-3, issue #321)."""

from __future__ import annotations

from pathlib import Path

from agent_core.daemon.config_hygiene import (
    check_fragment_drift,
    find_debris_files,
    run_config_hygiene,
)

# ---------------------------------------------------------------------------
# find_debris_files
# ---------------------------------------------------------------------------

class TestFindDebrisFiles:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert find_debris_files(tmp_path) == []

    def test_detects_bak_in_root(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_dated_bak_suffix(self, tmp_path: Path) -> None:
        debris = tmp_path / "wren.yaml.bak-20260702-with-voice"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_cleanup_dated(self, tmp_path: Path) -> None:
        debris = tmp_path / "testbeing.yaml.cleanup-2026-05-10"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_pre_suffix(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.pre-voice"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_bare_bak(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_bare_cleanup(self, tmp_path: Path) -> None:
        debris = tmp_path / "something.yaml.cleanup"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_ignores_plain_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "agent_core.yaml").write_text("bus: {}")
        assert find_debris_files(tmp_path) == []

    def test_detects_debris_in_endpoints_d(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        debris = endpoints_d / "wren.yaml.bak-20260702-with-voice"
        debris.write_text("")
        assert find_debris_files(tmp_path) == [debris]

    def test_detects_debris_in_both_dirs(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        root_debris = tmp_path / "agent_core.yaml.bak"
        frag_debris = endpoints_d / "testbeing.yaml.cleanup-2026-05-10"
        root_debris.write_text("")
        frag_debris.write_text("")
        result = find_debris_files(tmp_path)
        assert root_debris in result
        assert frag_debris in result

    def test_no_endpoints_d_dir_still_works(self, tmp_path: Path) -> None:
        # endpoints.d doesn't exist — no error
        assert find_debris_files(tmp_path) == []

    def test_results_sorted(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        b = endpoints_d / "z.yaml.bak"
        a = endpoints_d / "a.yaml.bak"
        a.write_text("")
        b.write_text("")
        result = find_debris_files(tmp_path)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# check_fragment_drift
# ---------------------------------------------------------------------------

class TestCheckFragmentDrift:
    def _make_endpoints_d(self, config_dir: Path) -> Path:
        d = config_dir / "endpoints.d"
        d.mkdir()
        return d

    def test_no_endpoints_d_returns_empty(self, tmp_path: Path) -> None:
        assert check_fragment_drift(tmp_path) == []

    def test_clean_fragment_returns_no_warnings(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "wren.yaml").write_text(
            "endpoints:\n  - type: builtin.stub\n    name: wren-stub\n"
        )
        assert check_fragment_drift(tmp_path) == []

    def test_fragment_with_reserved_bus_key_is_flagged(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "agent_core-cputest.yaml").write_text(
            "bus:\n  storage_path: /tmp/bus.sqlite\nendpoints: []\n"
        )
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 1
        assert "agent_core-cputest.yaml" in messages[0]
        assert "bus" in messages[0]

    def test_all_four_reserved_keys_are_caught(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "bad.yaml").write_text(
            "bus: {}\nhttp: {}\nbus_hooks: {}\nmcp_audit: {}\nendpoints: []\n"
        )
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 1
        assert "bus" in messages[0]
        assert "http" in messages[0]
        assert "bus_hooks" in messages[0]
        assert "mcp_audit" in messages[0]

    def test_yaml_parse_error_produces_message(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "broken.yaml").write_text(": invalid: yaml: content [[\n")
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 1
        assert "broken.yaml" in messages[0]
        assert "parse error" in messages[0].lower()

    def test_debris_files_are_excluded_from_drift_check(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        # A debris file that happens to contain reserved keys must not trigger drift
        (endpoints_d / "old.yaml.bak-20260101").write_text("bus: {}\n")
        assert check_fragment_drift(tmp_path) == []

    def test_multiple_fragments_each_produce_own_message(self, tmp_path: Path) -> None:
        endpoints_d = self._make_endpoints_d(tmp_path)
        (endpoints_d / "alpha.yaml").write_text("bus: {}\nendpoints: []\n")
        (endpoints_d / "beta.yaml").write_text("http: {}\nendpoints: []\n")
        messages = check_fragment_drift(tmp_path)
        assert len(messages) == 2


# ---------------------------------------------------------------------------
# run_config_hygiene
# ---------------------------------------------------------------------------

class TestRunConfigHygiene:
    def test_no_fix_does_not_delete_debris(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        report = run_config_hygiene(tmp_path, fix=False)
        assert len(report.debris_found) == 1
        assert report.debris_removed == []
        assert debris.exists(), "fix=False must not delete anything"

    def test_fix_deletes_debris_files(self, tmp_path: Path) -> None:
        debris = tmp_path / "agent_core.yaml.bak"
        debris.write_text("")
        report = run_config_hygiene(tmp_path, fix=True)
        assert len(report.debris_found) == 1
        assert len(report.debris_removed) == 1
        assert not debris.exists()

    def test_has_issues_false_when_clean(self, tmp_path: Path) -> None:
        report = run_config_hygiene(tmp_path, fix=False)
        assert not report.has_issues

    def test_has_issues_true_when_debris_found(self, tmp_path: Path) -> None:
        (tmp_path / "x.yaml.bak").write_text("")
        report = run_config_hygiene(tmp_path, fix=False)
        assert report.has_issues

    def test_has_issues_true_when_drift_found(self, tmp_path: Path) -> None:
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        (endpoints_d / "bad.yaml").write_text("bus: {}\nendpoints: []\n")
        report = run_config_hygiene(tmp_path, fix=False)
        assert report.has_issues
        assert report.drift_messages

    def test_drift_is_report_only_even_with_fix(self, tmp_path: Path) -> None:
        """--fix never auto-modifies YAML fragments (schema drift is manual-review-only)."""
        endpoints_d = tmp_path / "endpoints.d"
        endpoints_d.mkdir()
        bad = endpoints_d / "bad.yaml"
        bad.write_text("bus: {}\nendpoints: []\n")
        run_config_hygiene(tmp_path, fix=True)
        # The drifted fragment must still exist after --fix
        assert bad.exists()

    def test_fix_with_multiple_debris_removes_all(self, tmp_path: Path) -> None:
        files = [
            tmp_path / "a.yaml.bak",
            tmp_path / "b.yaml.bak-20260101",
            tmp_path / "c.yaml.cleanup-2026-05-10",
        ]
        for f in files:
            f.write_text("")
        report = run_config_hygiene(tmp_path, fix=True)
        assert len(report.debris_removed) == 3
        for f in files:
            assert not f.exists()
