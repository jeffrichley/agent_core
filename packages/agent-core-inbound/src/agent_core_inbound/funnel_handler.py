"""Tailscale Funnel HTTPS endpoint — receives GitHub webhooks.

Validates X-Hub-Signature-256 via HMAC-SHA256 of the raw body with
the operator-configured webhook secret. Translates the payload into
a generic GitHubEvent and hands it to Router.receive(). The connector's
match engine walks ``raw`` via dotted paths, so every webhook event
parses successfully here — including events GitHub introduces after
this code shipped. Filtering happens at the connector layer, not here.

Bad/missing signatures return 401 — but GitHub treats any non-2xx as a
delivery failure and retries, which means a misconfigured secret will
retry forever; the operator's deploy checklist must verify the secret
pair end-to-end before going live.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Request, status

from agent_core_inbound.github_event import GitHubEvent
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

        # Guard against signed-but-malformed JSON bodies. Returning 400
        # (rather than letting json.JSONDecodeError raise a 500) tells
        # GitHub to stop retrying — a malformed body won't be fixed by
        # redelivery, and 5xx triggers the indefinite retry loop noted
        # in the module docstring.
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="malformed JSON body")

        delivery_id = x_github_delivery or ""
        # event_id falls back to event_type + repo + action when GitHub
        # omits X-GitHub-Delivery so the router's de-dupe key is still
        # stable.
        if delivery_id:
            event_id = f"github-{delivery_id}"
        else:
            repo_full_name = (payload.get("repository") or {}).get(
                "full_name", "",
            )
            action = payload.get("action", "")
            event_id = (
                f"github-{repo_full_name}-{x_github_event or ''}-{action}"
            )

        event = _parse_event(
            {"X-GitHub-Event": x_github_event or ""},
            raw,
            event_id=event_id,
        )

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
    headers: dict[str, str],
    body: bytes,
    *,
    event_id: str,
) -> GitHubEvent:
    """Parse a GitHub webhook into a generic event.

    ``headers`` is the request headers map (case-insensitive lookup of
    ``X-GitHub-Event`` to identify the event type). ``body`` is the raw
    request body; we parse it as JSON and preserve the dict in
    ``GitHubEvent.raw`` for downstream matchers.
    """
    event_type = headers.get("X-GitHub-Event") or headers.get("x-github-event") or ""
    payload = json.loads(body) if body else {}
    return GitHubEvent(
        event_id=event_id,
        landed_at=datetime.now(UTC),
        event_type=event_type,
        action=payload.get("action", ""),
        repo_full_name=(payload.get("repository") or {}).get("full_name", ""),
        raw=payload,
    )
