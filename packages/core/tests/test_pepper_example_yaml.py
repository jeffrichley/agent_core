"""Tripwire tests against ``docs/examples/pepper-agent-core.yaml``.

The example pepper config is the canonical wiring referenced by:
- Cutover #01 (IdentityInjector at SessionStart)
- Cutover #02 (HandoffWriter / HandoffInjector at SessionEnd, PreCompact, SessionStart)
- Cutover #07 (TimeInjector on UserPromptSubmit with track_session=true)

The acceptance failure mode that #07 names explicitly is "someone refactors the
pipeline, TimeInjector quietly moves to SessionStart-only, and I start getting
day-of-week wrong without anyone noticing." Unit tests on the tools in
isolation cannot catch a regression where the *registration* drops out of the
yaml. These tests load the real file and assert the wiring stays put.

If you intentionally restructure the example, update these tests too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.hooks.pipeline import Pipeline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_YAML = _REPO_ROOT / "docs" / "examples" / "pepper-agent-core.yaml"
_EXAMPLE_FRAGMENT = _REPO_ROOT / "docs" / "examples" / "endpoints.d" / "pepper.yaml"


@pytest.fixture(scope="module")
def pepper_pipeline() -> Pipeline:
    if not _EXAMPLE_YAML.exists():
        pytest.fail(
            f"docs/examples/pepper-agent-core.yaml not found at {_EXAMPLE_YAML}; "
            "Cutover #07 wiring tests cannot run."
        )
    return Pipeline(_EXAMPLE_YAML)


def _types_for(pipeline: Pipeline, event: str) -> list[str]:
    return [t.type for t in pipeline.config.pipelines.get(event, [])]


class TestPepperExampleYaml:
    """Lock in the canonical SessionStart/UserPromptSubmit/PreCompact/SessionEnd wiring."""

    def test_user_prompt_submit_registers_time_injector(self, pepper_pipeline: Pipeline):
        """Cutover #07 acceptance #1: TimeInjector must fire on every
        UserPromptSubmit so the agent re-anchors on time each turn. The
        documented regression mode (`feedback_day_labels`) is exactly what
        this assertion catches: a refactor that drops TimeInjector from
        UserPromptSubmit would silently break per-turn re-anchoring."""
        assert "builtin.time_injector" in _types_for(pepper_pipeline, "UserPromptSubmit")

    def test_user_prompt_submit_time_injector_has_track_session_true(
        self, pepper_pipeline: Pipeline
    ):
        """Without track_session=true, TimeInjector emits only the absolute
        time on UserPromptSubmit and the per-turn 'Last user turn Xm ago'
        deltas disappear. That is half the value of registering it on this
        event in the first place."""
        ups = pepper_pipeline.config.pipelines["UserPromptSubmit"]
        time_tools = [t for t in ups if t.type == "builtin.time_injector"]
        assert time_tools, "TimeInjector missing from UserPromptSubmit"
        assert time_tools[0].params.get("track_session") is True

    def test_session_start_registers_identity_and_handoff_injectors(
        self, pepper_pipeline: Pipeline
    ):
        """Cutover #07 acceptance #2 (firing of #01's tools). The example
        registers three IdentityInjector entries (SOUL, IDENTITY, preferences)
        and one HandoffInjector. Drop any one of them and the agent's
        SessionStart context is wrong."""
        types = _types_for(pepper_pipeline, "SessionStart")
        assert types.count("builtin.identity_injector") == 3
        assert types.count("builtin.handoff_injector") == 1

    def test_session_end_and_precompact_register_handoff_writer(self, pepper_pipeline: Pipeline):
        """Cutover #07 acceptance #3 (firing of #02's writer). HandoffWriter
        must fire on both SessionEnd and PreCompact — losing either path
        produces silent continuity gaps for the next session."""
        assert "builtin.handoff_writer" in _types_for(pepper_pipeline, "SessionEnd")
        assert "builtin.handoff_writer" in _types_for(pepper_pipeline, "PreCompact")

    def test_user_prompt_submit_pipeline_runs_and_renders_current_time(
        self, pepper_pipeline: Pipeline
    ):
        """End-to-end: Pipeline.run("UserPromptSubmit", ...) actually executes
        TimeInjector and pipeline.render() includes the '## Current Time'
        heading. Catches regressions in render that would silently strip the
        injection (e.g., a future 'drop empty headings' optimization that
        misjudges TimeInjector output)."""
        results = pepper_pipeline.run("UserPromptSubmit", {"session_id": "yaml-tripwire"})
        rendered = pepper_pipeline.render(results)
        assert "## Current Time" in rendered


class TestPepperExampleYamlBusLog:
    """Cutover #04 wiring tripwire."""

    def test_bus_hooks_pre_publish_registers_daily_raw_jsonl(self):
        """The example yaml must register builtin.daily_raw_jsonl on
        pre_publish so Pepper's bus traffic is captured for tomorrow's
        reflection summary. If a future refactor drops this entry, the
        test fails — Pepper would silently stop logging her day."""
        import yaml as pyyaml

        raw = pyyaml.safe_load(_EXAMPLE_YAML.read_text(encoding="utf-8"))
        bus_hooks = (raw or {}).get("bus_hooks", {}) or {}
        pre_publish = bus_hooks.get("pre_publish") or []
        types = [entry.get("type") for entry in pre_publish]
        assert "builtin.daily_raw_jsonl" in types, (
            "Cutover #04 expects the daily JSONL hook on pre_publish"
        )


class TestPepperExampleYamlBriefs:
    """Cutover #09 wiring tripwire — brief framework orchestrator endpoint.

    Pepper's being-endpoints now live in docs/examples/endpoints.d/pepper.yaml
    (Cα-2 migration). These assertions lock the canonical wiring in that
    fragment so regressions surface at test time.
    """

    @pytest.fixture(scope="class")
    def raw_yaml(self) -> dict:
        # Read the fragment directly so these assertions don't depend
        # on the briefs orchestrator being importable as an endpoint type
        # by Pipeline (Pipeline only loads hook-tool pipelines, not bus
        # endpoints — the briefs orchestrator is a bus endpoint).
        # Cα-2: Pepper's being-endpoints migrated from the monolith to
        # endpoints.d/pepper.yaml; this fixture now reads from there.
        import yaml as pyyaml

        return pyyaml.safe_load(_EXAMPLE_FRAGMENT.read_text(encoding="utf-8")) or {}

    def _endpoint_by_name(self, raw: dict, name: str) -> dict | None:
        for entry in raw.get("endpoints") or []:
            if entry.get("name") == name:
                return entry
        return None

    def test_briefs_orchestrator_endpoint_exists(self, raw_yaml: dict):
        """Cutover #09 acceptance: the Pepper fragment must declare a
        builtin.briefs_orchestrator endpoint named ``briefs.pepper``
        so Pepper can receive BriefRequest events on the bus."""
        ep = self._endpoint_by_name(raw_yaml, "briefs.pepper")
        assert ep is not None, (
            "Cutover #09 expects an endpoint named 'briefs.pepper' in the Pepper fragment"
        )
        assert ep.get("type") == "builtin.briefs_orchestrator"

    def test_briefs_orchestrator_has_playbooks_path(self, raw_yaml: dict):
        """The orchestrator must point at a playbooks directory — the brief
        framework loads ``<playbooks_path>/<brief_type>.md`` per request, so
        a missing or empty path means no briefs can be composed."""
        ep = self._endpoint_by_name(raw_yaml, "briefs.pepper")
        assert ep is not None
        params = ep.get("params") or {}
        playbooks_path = params.get("playbooks_path")
        assert isinstance(playbooks_path, str) and playbooks_path, (
            "briefs.pepper.params.playbooks_path must be a non-empty string"
        )

    def test_briefs_orchestrator_has_fetcher_paths(self, raw_yaml: dict):
        """fetcher_paths must list at least one directory — without fetchers
        the gather step has no data sources, so every brief composes against
        empty context. The runner uses fetcher_paths (not fetcher_catalog)."""
        ep = self._endpoint_by_name(raw_yaml, "briefs.pepper")
        assert ep is not None
        params = ep.get("params") or {}
        fetcher_paths = params.get("fetcher_paths")
        assert isinstance(fetcher_paths, list) and len(fetcher_paths) >= 1, (
            "briefs.pepper.params.fetcher_paths must be a list with at least one entry"
        )
        for entry in fetcher_paths:
            assert isinstance(entry, str) and entry, (
                "every fetcher_paths entry must be a non-empty string"
            )

    def test_briefs_orchestrator_has_default_target_agent_pepper(self, raw_yaml: dict):
        """default_target_agent must be ``pepper`` so a self-launched brief
        (compose_brief MCP path with no envelope metadata) is routed back to
        Pepper's MCP endpoint. Mis-routing here would silently drop the
        ComposeBrief envelope on the bus floor."""
        ep = self._endpoint_by_name(raw_yaml, "briefs.pepper")
        assert ep is not None
        params = ep.get("params") or {}
        assert params.get("default_target_agent") == "pepper"

    def test_briefs_orchestrator_has_agent_root_var(self, raw_yaml: dict):
        """``vars.agent_root`` is the substitution anchor for playbook +
        gather config paths (``${agent_root}/Memory/...``). Every Pepper
        playbook references it; a missing entry would surface as a parse-time
        error at the first BriefRequest, not at boot."""
        ep = self._endpoint_by_name(raw_yaml, "briefs.pepper")
        assert ep is not None
        params = ep.get("params") or {}
        vars_block = params.get("vars")
        assert isinstance(vars_block, dict), "briefs.pepper.params.vars must be a mapping"
        agent_root = vars_block.get("agent_root")
        assert isinstance(agent_root, str) and agent_root, (
            "briefs.pepper.params.vars.agent_root must be a non-empty string"
        )

    def test_pepper_claude_code_mcp_endpoint_still_exists(self, raw_yaml: dict):
        """Regression check: Pepper's claude_code_mcp endpoint must exist
        in the fragment. The MCP endpoint is the inbound surface for Claude Code;
        the orchestrator is the bus subscriber for BriefRequest events."""
        ep = self._endpoint_by_name(raw_yaml, "pepper")
        assert ep is not None, (
            "Pepper's claude_code_mcp endpoint must remain in the Pepper fragment"
        )
        assert ep.get("type") == "builtin.claude_code_mcp"

    def test_pepper_mcp_endpoint_references_briefs_orchestrator(self, raw_yaml: dict):
        """T19 cross-endpoint wiring: Pepper's MCP endpoint must name the
        briefs orchestrator via ``params.briefs_orchestrator`` so the runner
        mounts the seven briefs agent tools onto Pepper's MCP session at
        ``bus.start()`` time. Cα-2: the reference is now ``briefs.pepper``
        (correcting the naming inconsistency with the old ``briefs.orchestrator``)."""
        ep = self._endpoint_by_name(raw_yaml, "pepper")
        assert ep is not None
        params = ep.get("params") or {}
        assert params.get("briefs_orchestrator") == "briefs.pepper", (
            "pepper.params.briefs_orchestrator must equal 'briefs.pepper' so "
            "the cross-endpoint wiring hook pairs the MCP session with the orchestrator"
        )

    def test_briefs_orchestrator_destination_paths_or_default(self, raw_yaml: dict):
        """T19: destination_paths is optional but if present must be a
        non-empty list of strings. The orchestrator falls back to
        built-in destinations only when omitted; either shape is
        acceptable. This locks the shape so a future refactor can't
        silently break the param's contract."""
        ep = self._endpoint_by_name(raw_yaml, "briefs.pepper")
        assert ep is not None
        params = ep.get("params") or {}
        if "destination_paths" not in params:
            return  # using the default (built-in destinations only) — fine
        destination_paths = params["destination_paths"]
        assert isinstance(destination_paths, list) and len(destination_paths) >= 1, (
            "briefs.pepper.params.destination_paths must be a list with at "
            "least one entry when present"
        )
        for entry in destination_paths:
            assert isinstance(entry, str) and entry, (
                "every destination_paths entry must be a non-empty string"
            )
