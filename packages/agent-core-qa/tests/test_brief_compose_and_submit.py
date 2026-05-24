"""Scenario 4: brief framework compose + submit smoke.

Validates the gather → compose → submit_brief chain end-to-end against
the live test daemon.

Why v0.3.0 needs it: brief framework is core to daily ops (Pepper relies
on it for morning_brief); a regression would surface as silent compose
failures with no obvious connection to the release.

Transport choice (Preflight Step 0 finding):
    ``submit_brief`` is an MCP tool registered on the ``qa``
    ``ClaudeCodeMCPEndpoint`` via the T19 ``wire_endpoints_after_registration``
    pluggy hookimpl. The hookimpl pairs any ``ClaudeCodeMCPEndpoint`` whose
    yaml ``params.briefs_orchestrator`` names a
    ``BriefsOrchestratorEndpoint`` with that orchestrator, then appends a
    deferred mounter that calls ``register_briefs_tools()`` at bus start.
    The seven brief tools (``compose_brief``, ``list_sections``,
    ``get_section_spec``, ``validate_section``, ``compress_sections``,
    ``add_extension_section``, ``submit_brief``) are therefore accessible
    at ``/mcp/qa/`` via ``client.call_tool()``.

    Route (a) — direct MCP tool calls on the ``qa`` endpoint — was chosen
    because it has the fewest moving parts: no envelope routing, no bus
    dispatch, just FastMCP Streamable HTTP. The plan's ``ToolInvocation``
    envelope template (route b) would require the bus to dispatch to the
    orchestrator endpoint, which adds a layer with nothing to gain for a
    smoke test.

Payload shape rationale:
    ``compose_brief(brief_type, scope)`` runs the gather pipeline and
    returns a session_token plus ``sections_required``. We call it with
    ``brief_type="morning_brief"`` (the standard playbook expected in the
    test daemon's configured ``playbooks_path``) and ``scope="qa-smoke"``.

    ``submit_brief(token, sections)`` accepts a list of section dicts,
    each with ``section_id`` and ``fields`` (list of ``{name, value}``).
    For the smoke test we pass one field per required section with a
    stub value ``"qa-smoke-probe"`` — enough to satisfy field-presence
    validation for required fields. The test treats both a clean success
    and a known partial failure (validation issues or destination errors)
    as acceptable outcomes; what it asserts is:
    - ``compose_brief`` returned a ``session_token`` (non-empty string)
    - ``submit_brief`` was callable with that token and returned a
      structured result (dict with ``success``, ``validation_issues``,
      ``deliveries`` keys)

    A 500-status from either call signals a tool crash (the bug we're
    guarding against).

    If ``compose_brief`` returns a ``ValueError`` (playbook not found),
    the test skips with a clear message rather than failing — this is an
    operator-side configuration issue, not a framework regression.

    The test SKIPs (via the autouse ``daemon_liveness_required`` fixture)
    when no daemon is reachable.
"""

from __future__ import annotations

import pytest


async def test_brief_compose_and_submit(client):
    """Invoke compose_brief then submit_brief via the qa MCP endpoint.

    Phase 1: call ``compose_brief`` to get a session token.
    Phase 2: call ``submit_brief`` with minimal filled sections.
    Assert both tools respond without crashing (status 200).
    Assert ``submit_brief`` returns a structured result dict.
    """
    # Phase 1: compose_brief — start a brief session.
    compose_result = await client.call_tool(
        "compose_brief",
        arguments={
            "brief_type": "morning_brief",
            "scope": "qa-smoke",
        },
    )
    if compose_result.status_code != 200:
        # Tool raised — could be playbook-not-found (operator config issue)
        # or a genuine framework crash. Surface the error text for diagnosis.
        error_text = compose_result.text[:400]
        if "playbook" in error_text.lower() or "not found" in error_text.lower() or "unknown brief_type" in error_text.lower():
            pytest.skip(
                f"compose_brief: playbook 'morning_brief' not found in the test daemon's "
                f"configured playbooks_path; ensure the daemon is configured with a "
                f"briefs_orchestrator whose playbooks_path contains morning_brief.md. "
                f"Error: {error_text}"
            )
        pytest.fail(
            f"compose_brief tool returned status {compose_result.status_code}; "
            f"expected 200. Error: {error_text}"
        )

    compose_data = compose_result.json()
    assert isinstance(compose_data, dict), (
        f"compose_brief returned non-dict: {type(compose_data).__name__}: {compose_data!r}"
    )

    session_token = compose_data.get("session_token")
    assert session_token and isinstance(session_token, str), (
        f"compose_brief returned no session_token; got: {compose_data!r}"
    )

    # Build a minimal submission using whatever sections are required.
    # Each section gets one stub field so required-field-present checks pass.
    # We use the first field name from compose_data's section list if
    # available; otherwise fall back to "content" which many playbooks use.
    sections_required: list[str] = compose_data.get("sections_required", [])
    if not sections_required:
        # If there are no required sections, include at least one optional
        # section so the brief has content (some playbooks are all-optional).
        sections_optional: list[str] = compose_data.get("sections_optional", [])
        sections_to_fill = sections_optional[:1] if sections_optional else []
    else:
        sections_to_fill = list(sections_required)

    # Build section dicts. We don't know field names from compose_data alone
    # (that requires get_section_spec), so we supply a single field named
    # "content" — enough to exercise the submit path. Validation may still
    # fail if the spec requires a differently-named field, but that's a
    # known partial-failure mode, not a crash.
    minimal_sections = [
        {
            "section_id": sid,
            "fields": [{"name": "content", "value": "qa-smoke-probe"}],
        }
        for sid in sections_to_fill
    ]

    # Phase 2: submit_brief — exercise the validate + fan-out path.
    submit_result = await client.call_tool(
        "submit_brief",
        arguments={
            "token": session_token,
            "sections": minimal_sections,
        },
    )
    assert submit_result.status_code == 200, (
        f"submit_brief tool returned status {submit_result.status_code}; "
        f"expected 200. Error: {submit_result.text[:400]}"
    )

    submit_data = submit_result.json()
    assert isinstance(submit_data, dict), (
        f"submit_brief returned non-dict: {type(submit_data).__name__}: {submit_data!r}"
    )

    # The structured result must have these keys regardless of success.
    for key in ("success", "validation_issues", "deliveries"):
        assert key in submit_data, (
            f"submit_brief result missing key {key!r}; got: {submit_data!r}"
        )

    # Both True (all destinations delivered) and False (validation issues
    # or destination failures) are acceptable smoke outcomes — the brief
    # framework produced a structured response, which is what we're proving.
    # A 500 from the tool call (caught above) would mean the submit path
    # crashed entirely.
    assert isinstance(submit_data["validation_issues"], list), (
        f"submit_brief 'validation_issues' is not a list: {submit_data!r}"
    )
    assert isinstance(submit_data["deliveries"], list), (
        f"submit_brief 'deliveries' is not a list: {submit_data!r}"
    )
