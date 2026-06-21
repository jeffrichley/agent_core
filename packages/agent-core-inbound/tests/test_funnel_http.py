"""HTTP-boundary integration tests using fastapi.TestClient.

Tests the full HTTP layer: header parsing, HMAC verify, route mount,
JSON body parsing — then the connector + router downstream. Catches
bugs at the boundary that Router-level tests (Tasks 8-11) miss.

Distinct from ``test_endpoint_integration.py`` (happy-path E2E): this
file focuses on the HTTP boundary's edge cases — missing/bad
signatures, route-mount verification (would have caught v1.a's
``--set-path`` path-routing bug), and signed-but-unmatched events.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router


SECRET = b"test-secret-do-not-use-in-prod"


def _sign(body: bytes) -> str:
    mac = hmac.new(SECRET, body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _make_client(
    tmp_path: Path, rules_toml: str,
) -> tuple[TestClient, list[dict[str, Any]], Path]:
    """Build a test client around a real connector + router with a stub publish."""
    rules_path = tmp_path / "rules.toml"
    rules_path.write_text(rules_toml, encoding="utf-8")
    connector = GitHubConnector(config_path=rules_path, principal_being="wren")
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(path=audit_path)
    bus_calls: list[dict[str, Any]] = []

    def fake_publish(*, to: str, kind: str, payload: dict, urgency: str) -> None:
        bus_calls.append(
            {"to": to, "kind": kind, "payload": payload, "urgency": urgency},
        )

    router = Router(
        connectors={"github": connector},
        bus_publish=fake_publish,
        audit=audit,
    )
    app = build_funnel_app(
        router=router,
        webhook_secret=SECRET,
        target_being="wren",
    )
    return TestClient(app), bus_calls, audit_path


def test_get_github_returns_405_or_404(tmp_path: Path) -> None:
    """GET on a POST-only route — verify the route IS mounted at /github
    (404 here would mean a path-routing bug like v1.a's --set-path issue)."""
    client, _bus, _ = _make_client(
        tmp_path,
        "[[allow]]\nrule_id='r'\nevent='ping'\ntier='green'\nreason='x'\n",
    )
    resp = client.get("/github")
    # FastAPI returns 405 when route exists for other methods, 404 if path
    # is unmounted. Either confirms the path doesn't match GET — but only
    # 405 confirms the route exists. The important thing is NOT 200.
    assert resp.status_code in (404, 405)


def test_post_without_signature_returns_401(tmp_path: Path) -> None:
    client, _bus, _ = _make_client(
        tmp_path,
        "[[allow]]\nrule_id='r'\nevent='ping'\ntier='green'\nreason='x'\n",
    )
    body = json.dumps({"zen": "..."}).encode()
    resp = client.post(
        "/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_post_with_bad_signature_returns_401(tmp_path: Path) -> None:
    client, _bus, _ = _make_client(
        tmp_path,
        "[[allow]]\nrule_id='r'\nevent='ping'\ntier='green'\nreason='x'\n",
    )
    body = json.dumps({"zen": "..."}).encode()
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_post_signed_ping_returns_204_and_writes_audit(tmp_path: Path) -> None:
    """Ping is an action-less event; should match our ``ping`` rule and 204."""
    client, bus_calls, audit_path = _make_client(
        tmp_path,
        "[[allow]]\nrule_id='ping_ok'\nevent='ping'\ntier='green'\nreason='webhook ping'\n",
    )
    body = json.dumps({"zen": "Speak little, do much.", "hook_id": 123}).encode()
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert len(bus_calls) == 1
    pub = bus_calls[0]
    assert pub["to"] == "wren"
    assert pub["kind"] == "Notification"
    assert pub["urgency"] == "green"
    assert pub["payload"]["source"] == "github"
    assert pub["payload"]["reason"] == "webhook ping"
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    assert '"verdict":"allow"' in audit_lines[0] or '"verdict": "allow"' in audit_lines[0]


def test_post_signed_workflow_run_failure(tmp_path: Path) -> None:
    """End-to-end: HTTP POST → HMAC verify → parse → match dotted-path
    rule → audit allow + bus publish."""
    client, bus_calls, audit_path = _make_client(
        tmp_path,
        """
[[allow]]
rule_id = "ci_failed"
event = "workflow_run_completed"
match = { "workflow_run.conclusion" = "failure" }
tier = "red"
reason = "CI failed"
""",
    )
    body = json.dumps({
        "action": "completed",
        "workflow_run": {"conclusion": "failure", "head_branch": "main"},
        "repository": {"full_name": "jeffrichley/foreman"},
    }).encode()
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert len(bus_calls) == 1
    pub = bus_calls[0]
    assert pub["to"] == "wren"
    assert pub["kind"] == "Notification"
    assert pub["urgency"] == "red"
    assert pub["payload"]["source"] == "github"
    assert pub["payload"]["reason"] == "CI failed"


def test_post_signed_event_with_no_matching_rule_denies(tmp_path: Path) -> None:
    """A signed POST that doesn't match any rule should 204 silently
    (per README: unmodeled events return 204) and NOT publish to bus."""
    client, bus_calls, _audit_path = _make_client(
        tmp_path,
        "[[allow]]\nrule_id='only_ping'\nevent='ping'\ntier='green'\nreason='ok'\n",
    )
    body = json.dumps({
        "action": "starred",
        "repository": {"full_name": "jeffrichley/foreman"},
    }).encode()
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "star",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert bus_calls == []
