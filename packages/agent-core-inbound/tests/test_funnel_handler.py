"""FastAPI handler for the Tailscale Funnel HTTPS endpoint.

Validates GitHub webhook signatures (X-Hub-Signature-256) and
translates the JSON payload into a typed GitHubEvent that gets
handed to Router.receive().
"""
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router
from fastapi.testclient import TestClient

_WEBHOOK_SECRET = b"test-secret-for-signing"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(_WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()


@pytest.fixture
def app_and_published(tmp_path: Path):
    published: list[dict[str, Any]] = []

    def bus_publish(*, to, kind, payload, urgency):
        published.append({"to": to, "kind": kind, "payload": payload, "urgency": urgency})

    cfg = tmp_path / "g.toml"
    cfg.write_text(
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "PR review requested on foreman"
""",
        encoding="utf-8",
    )
    connector = GitHubConnector(config_path=cfg)
    audit = AuditLog(path=tmp_path / "audit.jsonl")
    router = Router(
        connectors={"github": connector},
        bus_publish=bus_publish,
        audit=audit,
    )
    app = build_funnel_app(
        router=router,
        webhook_secret=_WEBHOOK_SECRET,
        target_being="wren",
    )
    return app, published


def test_signed_pr_review_requested_publishes(app_and_published):
    app, published = app_and_published
    client = TestClient(app)

    body_obj = {
        "action": "review_requested",
        "pull_request": {"number": 387},
        "repository": {"full_name": "jeffrichley/foreman"},
        "requested_reviewer": {"login": "wrenrichley"},
    }
    body = json.dumps(body_obj).encode("utf-8")

    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-id-1",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert len(published) == 1
    assert published[0]["urgency"] == "red"
    assert published[0]["payload"]["source"] == "github"


def test_missing_signature_rejected(app_and_published):
    app, published = app_and_published
    client = TestClient(app)
    body = json.dumps({"action": "review_requested"}).encode("utf-8")
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-id-2",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert published == []


def test_bad_signature_rejected(app_and_published):
    app, published = app_and_published
    client = TestClient(app)
    body = json.dumps({"action": "review_requested"}).encode("utf-8")
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-id-3",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert published == []


def test_unhandled_event_type_returns_204(app_and_published):
    """GitHub sends many event types we don't model. Return 204 (don't
    error, don't publish) so GitHub's delivery cadence stays clean."""
    app, published = app_and_published
    client = TestClient(app)
    body = json.dumps({"action": "started", "starred_at": "..."}).encode("utf-8")
    resp = client.post(
        "/github",
        content=body,
        headers={
            "X-GitHub-Event": "watch",  # GitHub event type we don't model
            "X-GitHub-Delivery": "delivery-id-4",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert published == []
