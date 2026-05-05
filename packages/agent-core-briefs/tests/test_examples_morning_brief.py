"""Tripwire: ``docs/examples/playbooks/morning-brief.md`` must run end-to-end.

The example is documentation. If it doesn't actually work, every author
who copy-pastes it (the explicit purpose of an example) hits a
runtime crash on the first cron fire. Step 4 of the cutover #09 test
playbook does parse-only verification of the example; this test goes
further and exercises the path that catches the crash class:

- gather (the example's `now` + `email_stub` fetchers must succeed
  against the example gather YAML; missing `${agent_root}` paths land
  in `_errors`, which is the gather engine's documented behavior),
- resolve_conditional_sections against the gathered context (this is
  where `now.is_friday` / `now.is_weekly_digest_day` evaluate; without
  a `now` namespace they raise PlaybookParseError).

If a future change drops the `now` built-in, removes the
`docs/examples/fetchers/email_stub.py` reference, or breaks the example
gather YAML, this test fires before the regression reaches Pepper.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.engine import gather_context
from agent_core_briefs.orchestrator import BriefsOrchestratorEndpoint
from agent_core_briefs.playbook import parse_playbook, resolve_conditional_sections

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_PLAYBOOK = REPO_ROOT / "docs" / "examples" / "playbooks" / "morning-brief.md"
EXAMPLE_GATHER = REPO_ROOT / "docs" / "examples" / "playbooks" / "morning-gather.yaml"
EXAMPLE_FETCHERS_DIR = REPO_ROOT / "docs" / "examples" / "fetchers"


def test_example_files_exist():
    """Sanity: the example files this test depends on are still where the
    cutover #09 playbook says they are. Catches an accidental rename or
    move that would otherwise make this whole file silently no-op."""
    assert EXAMPLE_PLAYBOOK.exists(), f"missing: {EXAMPLE_PLAYBOOK}"
    assert EXAMPLE_GATHER.exists(), f"missing: {EXAMPLE_GATHER}"
    assert EXAMPLE_FETCHERS_DIR.is_dir(), f"missing: {EXAMPLE_FETCHERS_DIR}"
    assert (EXAMPLE_FETCHERS_DIR / "email_stub.py").exists(), (
        f"missing: {EXAMPLE_FETCHERS_DIR / 'email_stub.py'}"
    )


def test_example_playbook_parses():
    """The example must parse. ``vars_map={agent_root: ...}`` provides a
    stand-in value for ``${agent_root}`` — any string is fine for
    parse-shape verification."""
    pb = parse_playbook(EXAMPLE_PLAYBOOK, vars_map={"agent_root": "/tmp/test"})
    assert len(pb.sections) == 8
    assert len(pb.conditional_sections) == 2
    assert {s.section_id for s in pb.conditional_sections} == {
        "weekly_digest",
        "war_pointer",
    }
    assert len(pb.destinations) == 2
    assert pb.voice == "pepper"


def test_example_runs_through_resolve_conditionals(tmp_path: Path):
    """Build a real orchestrator wired against the example gather YAML
    and run gather + resolve_conditional_sections. The orchestrator
    auto-loads built-in fetchers (cli, filesystem_read, now) from the
    package; the example_fetchers path adds email_stub. The CLI and
    filesystem_read entries in the example gather YAML reach for paths
    that don't exist on the test machine — those land in
    `context["_errors"]`, which is the gather engine's documented
    behavior on fetcher failure. The conditional `when:` expressions
    only need `now`, which the built-in supplies."""
    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=tmp_path,  # not exercised — we parse the playbook ourselves
        vars_map={"agent_root": str(tmp_path)},
        fetcher_paths=[EXAMPLE_FETCHERS_DIR],
    )
    # Built-ins discovered:
    assert "now" in ep._fetcher_catalog
    assert "filesystem_read" in ep._fetcher_catalog
    assert "cli" in ep._fetcher_catalog
    # Example agent-side fetcher discovered:
    assert "email_stub" in ep._fetcher_catalog

    # Parse the playbook with the orchestrator's vars so `${agent_root}`
    # in the gather_config metadata points at a known location.
    # We point at the example gather file directly rather than relying
    # on the metadata (which would resolve to <tmp_path>/Memory/gather/...).
    pb = parse_playbook(EXAMPLE_PLAYBOOK, vars_map={"agent_root": str(tmp_path)})

    # Build invocations against the actual example gather YAML, not the
    # tmp-based path the metadata would have produced. We mirror what
    # _build_invocations does internally: load yaml, walk fetchers list,
    # resolve type_ids against the catalog, construct invocations.
    import yaml as _yaml
    from agent_core_briefs.config import substitute_vars
    from agent_core_briefs.engine import FetcherInvocation

    raw = _yaml.safe_load(EXAMPLE_GATHER.read_text(encoding="utf-8"))
    substituted = substitute_vars(raw, {"agent_root": str(tmp_path)})

    invocations: list[FetcherInvocation] = []
    for entry in substituted["fetchers"]:
        cls = ep._fetcher_catalog[entry["type"]]
        invocations.append(
            FetcherInvocation(
                fetcher=cls(),
                config=entry.get("config") or {},
                timeout_seconds=float(entry.get("timeout_seconds", 30)),
                namespace_override=entry["namespace"],
            )
        )

    when = datetime(2026, 5, 4, 7, 0, tzinfo=UTC)  # Monday morning
    context = asyncio.run(gather_context(invocations, when=when))

    # The example references `now.is_friday`, `now.is_weekly_digest_day`,
    # and `len(email.urgent) > 0` — all three namespaces must resolve.
    assert "now" in context
    assert "email" in context
    # _errors may contain `cli` (gcalcli not installed) and / or
    # `filesystem_read` (path doesn't exist) — that's expected, those
    # are the framework's "fetcher failed" semantic, not a crash.
    # `now` and `email_stub` must NOT be in _errors.
    errors = context.get("_errors", {})
    assert "now" not in errors, f"now fetcher failed: {errors.get('now')}"
    assert "email_stub" not in errors, f"email_stub fetcher failed: {errors.get('email_stub')}"

    # The crash class this test exists to catch: resolve_conditional_sections
    # walks every conditional `when:` expression. If `now` were missing,
    # `now.is_friday` would raise PlaybookParseError. With the built-in
    # `now` registered, both expressions resolve cleanly.
    active_ids = resolve_conditional_sections(pb.conditional_sections, context)
    # Monday is the default weekly_digest_day → weekly_digest active,
    # war_pointer not active.
    assert "weekly_digest" in active_ids
    assert "war_pointer" not in active_ids


def test_example_runs_on_friday():
    """Same shape, different day: on a Friday the war_pointer conditional
    activates and weekly_digest does not. Locks in that the example's
    two conditionals reflect different `when:` predicates (i.e., aren't
    accidentally identical), and that `now.is_friday` works."""
    from agent_core_briefs.fetchers.now import NowFetcher
    from agent_core_briefs.engine import FetcherInvocation

    inv = FetcherInvocation(
        fetcher=NowFetcher(),
        config={"timezone": "UTC"},
        timeout_seconds=5,
        namespace_override="now",
    )
    # Stub email so resolve_conditional doesn't reach for it (it doesn't,
    # but parse the playbook to be sure).
    pb = parse_playbook(EXAMPLE_PLAYBOOK, vars_map={"agent_root": "/tmp/test"})

    friday = datetime(2026, 5, 8, 7, 0, tzinfo=UTC)  # Friday
    context = asyncio.run(gather_context([inv], when=friday))

    active_ids = resolve_conditional_sections(pb.conditional_sections, context)
    assert "war_pointer" in active_ids
    assert "weekly_digest" not in active_ids


def test_example_email_dynamic_color_evaluates():
    """The email_status section uses a dynamic color expression
    `len(email.urgent) > 0`. With the email_stub fetcher returning
    `urgent: []`, the expression evaluates False (and the EMAIL_OK
    color name applies). This is the third site that would crash if
    the email namespace were missing."""
    from agent_core_briefs.engine import FetcherInvocation
    from agent_core_briefs.playbook import _eval_expr  # the simpleeval wrapper

    # Manually load the email_stub fetcher class.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "email_stub_module",
        EXAMPLE_FETCHERS_DIR / "email_stub.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    EmailStubFetcher = module.EmailStubFetcher

    inv = FetcherInvocation(
        fetcher=EmailStubFetcher(),
        config={},
        timeout_seconds=5,
        namespace_override="email",
    )
    when = datetime(2026, 5, 4, 7, 0, tzinfo=UTC)
    context = asyncio.run(gather_context([inv], when=when))
    # The expr from morning-brief.md line 108:
    assert _eval_expr("len(email.urgent) > 0", context) is False


def test_example_only_uses_built_in_or_example_fetcher_types(tmp_path: Path):
    """Every `type:` referenced in the example gather YAML must resolve
    against built-ins + the example fetchers directory. If a future
    change adds a `type: redis` line without shipping a corresponding
    fetcher in either location, this test fires before the example
    silently breaks for someone copying it."""
    import yaml as _yaml

    ep = BriefsOrchestratorEndpoint(
        name="briefs.orchestrator",
        playbooks_path=tmp_path,
        vars_map={"agent_root": str(tmp_path)},
        fetcher_paths=[EXAMPLE_FETCHERS_DIR],
    )
    raw = _yaml.safe_load(EXAMPLE_GATHER.read_text(encoding="utf-8"))
    referenced_types = {entry["type"] for entry in raw["fetchers"]}
    missing = referenced_types - set(ep._fetcher_catalog)
    assert not missing, f"example gather references unknown types: {missing}"


@pytest.mark.skipif(
    not EXAMPLE_PLAYBOOK.exists(),
    reason="example playbook not present (running from a partial checkout?)",
)
def test_example_voice_and_destinations_unchanged():
    """The cutover #09 playbook Step 4 asserts these exact values; the
    example file is a sibling of that test playbook and they must agree.
    If someone changes the example, the test playbook needs to be
    updated too — this test makes that coupling visible."""
    pb = parse_playbook(EXAMPLE_PLAYBOOK, vars_map={"agent_root": "/tmp/test"})
    assert pb.voice == "pepper"
    dest_types = {d["type"] for d in pb.destinations}
    assert dest_types == {"discord_embed", "markdown_file"}
