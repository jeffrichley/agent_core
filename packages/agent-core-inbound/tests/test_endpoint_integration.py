"""End-to-end: simulated webhook → bus envelope.

Exercises the full v1.a chain. No external network. Validates the
deny-by-default invariant: a webhook for a rule we don't have results
in zero bus publishes and one Deny audit line.
"""
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router
from fastapi.testclient import TestClient

_SECRET = b"e2e-test-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET, body, hashlib.sha256).hexdigest()


def _build(tmp_path: Path, allowance_toml: str):
    published: list[dict[str, Any]] = []

    def bus_publish(*, to, kind, payload, urgency):
        published.append({"to": to, "kind": kind, "payload": payload, "urgency": urgency})

    cfg = tmp_path / "github-allowance.toml"
    cfg.write_text(allowance_toml, encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"

    connector = GitHubConnector(config_path=cfg)
    audit = AuditLog(path=audit_path)
    router = Router(
        connectors={"github": connector},
        bus_publish=bus_publish,
        audit=audit,
    )
    app = build_funnel_app(
        router=router,
        webhook_secret=_SECRET,
        target_being="wren",
    )
    return app, published, audit_path


def test_real_pr_review_requested_full_chain(tmp_path: Path):
    app, published, audit_path = _build(
        tmp_path,
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "PR review requested on foreman"
""",
    )
    client = TestClient(app)
    body = json.dumps({
        "action": "review_requested",
        "pull_request": {"number": 387},
        "repository": {"full_name": "jeffrichley/foreman"},
        "requested_reviewer": {"login": "wrenrichley"},
    }).encode("utf-8")
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "e2e-1",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204

    # Bus got a Notification envelope to wren with urgency=red.
    assert len(published) == 1
    pub = published[0]
    assert pub["to"] == "wren"
    assert pub["kind"] == "Notification"
    assert pub["payload"]["kind"] == "Notification"
    assert pub["payload"]["source"] == "github"
    assert pub["urgency"] == "red"
    assert pub["payload"]["reason"] == "PR review requested on foreman"
    assert pub["payload"]["body"]["pull_request"]["number"] == 387

    # Audit log shows the allow line with rule_id.
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["verdict"] == "allow"
    assert entry["rule_id"] == "pr_review_requested_foreman"


def test_unmatched_event_denied_with_audit(tmp_path: Path):
    app, published, audit_path = _build(
        tmp_path,
        # No rules — empty allowance.
        "",
    )
    client = TestClient(app)
    body = json.dumps({
        "action": "review_requested",
        "pull_request": {"number": 1},
        "repository": {"full_name": "some-other/repo"},
        "requested_reviewer": {"login": "wrenrichley"},
    }).encode("utf-8")
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "e2e-2",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert published == []
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["verdict"] == "deny"
