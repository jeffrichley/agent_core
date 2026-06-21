"""Tailscale Funnel HTTPS endpoint — receives GitHub webhooks.

Validates X-Hub-Signature-256 via HMAC-SHA256 of the raw body with
the operator-configured webhook secret. Translates the payload into
a typed GitHubEvent and hands it to Router.receive().

GitHub event types we don't model return 204 (silent drop). Bad/missing
signatures return 401 — but GitHub treats any non-2xx as a delivery
failure and retries, which means a misconfigured secret will retry
forever; the operator's deploy checklist must verify the secret pair
end-to-end before going live.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status

from agent_core_inbound.github_event import (
    GitHubEvent,
    GitHubIssueCommentEvent,
    GitHubIssuesLabeledEvent,
    GitHubPullRequestReviewRequestedEvent,
)
from agent_core_inbound.router import Router


def build_funnel_app(
    *,
    router: Router,
    webhook_secret: bytes,
    target_being: str,
) -> FastAPI:
    """Construct the FastAPI app with the GitHub webhook handler bound.

    Returned app exposes POST /github. Hand to uvicorn / the daemon
    endpoint wrapper for serving.
    """
    app = FastAPI(docs_url=None, redoc_url=None)

    @app.post("/github", status_code=status.HTTP_204_NO_CONTENT)
    async def receive_github(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> None:
        raw = await request.body()
        if not _verify_signature(raw, x_hub_signature_256, webhook_secret):
            raise HTTPException(status_code=401, detail="bad signature")

        payload = await request.json()
        event = _parse_event(
            event_type=x_github_event or "",
            delivery_id=x_github_delivery or "",
            payload=payload,
        )
        if event is None:
            return  # unmodeled event type → silent 204

        router.receive(
            connector_name="github",
            target_being=target_being,
            event=event,
        )

    return app


def _verify_signature(
    body: bytes,
    header_value: str | None,
    secret: bytes,
) -> bool:
    if header_value is None or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    presented = header_value.removeprefix("sha256=")
    return hmac.compare_digest(expected, presented)


def _parse_event(
    *,
    event_type: str,
    delivery_id: str,
    payload: dict[str, Any],
) -> GitHubEvent | None:
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    action = payload.get("action", "")
    landed_at = datetime.now(UTC)
    event_id = f"github-{delivery_id}" if delivery_id else f"github-{repo_full_name}-{action}"

    if event_type == "pull_request" and action == "review_requested":
        return GitHubPullRequestReviewRequestedEvent(
            event_id=event_id,
            landed_at=landed_at,
            repo_full_name=repo_full_name,
            action=action,
            pr_number=payload.get("pull_request", {}).get("number", 0),
            requested_reviewer_login=(payload.get("requested_reviewer") or {}).get("login"),
            raw=payload,
        )
    if event_type == "issue_comment" and action in {"created", "edited", "deleted"}:
        comment = payload.get("comment") or {}
        return GitHubIssueCommentEvent(
            event_id=event_id,
            landed_at=landed_at,
            repo_full_name=repo_full_name,
            action=action,
            issue_number=payload.get("issue", {}).get("number", 0),
            comment_body=comment.get("body", "") or "",
            comment_author_login=(comment.get("user") or {}).get("login", ""),
            raw=payload,
        )
    if event_type == "issues" and action == "labeled":
        return GitHubIssuesLabeledEvent(
            event_id=event_id,
            landed_at=landed_at,
            repo_full_name=repo_full_name,
            action=action,
            issue_number=payload.get("issue", {}).get("number", 0),
            label_name=(payload.get("label") or {}).get("name", ""),
            raw=payload,
        )
    return None
