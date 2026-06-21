# Inbound Notifications v1.a (GitHub → Wren) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the v1.a slice of the inbound-notifications design — a deny-by-default GitHub → Wren push pipeline using Tailscale Funnel, with the reusable Connector + Router substrate that v1.b (Gmail) and v1.c (Calendar) will plug into.

**Architecture:** New package `packages/agent-core-inbound/` carries the Connector protocol, Router (de-dupe + rate-limit + audit log + bus delivery), GitHub connector (TOML-policy classifier), and Tailscale Funnel FastAPI handler. Bus's `Notification` envelope kind lands in `core/bus/envelope.py` because that's where `BUILTIN_KINDS` lives; everything else is in the new package. Endpoint registers with the daemon via `agent_core.yaml` like the existing discord/voice/scheduler endpoints.

**Tech stack:** Python 3.12, Pydantic for typed envelope/config, FastAPI + uvicorn for the Funnel HTTPS endpoint, hmac/sha256 for GitHub webhook signature verification, watchdog (already in repo deps) for TOML mtime reload, tomllib (stdlib) for config parse, pytest for tests.

**Spec reference:** `docs/superpowers/specs/2026-06-20-inbound-notifications-design.md`. v1.b (Gmail) and v1.c (Calendar) are explicitly out of scope; their connectors will reuse this substrate in their own plans.

---

## File structure

```
packages/
  core/src/agent_core/bus/
    envelope.py                                     # MODIFY: add NotificationPayload + BUILTIN_KINDS entry
  core/tests/bus/
    test_envelope_notification.py                   # CREATE: round-trip + validation tests

  agent-core-inbound/                               # CREATE: new package
    pyproject.toml
    README.md
    src/agent_core_inbound/
      __init__.py
      types.py                                      # Allow, Deny, Tier enum, ConnectorEvent base
      protocol.py                                   # Connector Protocol
      audit.py                                      # JSONL audit log writer
      router.py                                     # Router: receive() → classify → de-dupe → rate-limit → bus
      github_event.py                               # Typed GitHub event shapes (subset)
      github_allowance.py                           # TOML loader + Pydantic AllowanceConfig + mtime reload
      github_connector.py                           # GitHubConnector implementing Connector
      funnel_handler.py                             # FastAPI app for Tailscale Funnel HTTPS endpoint
      endpoint.py                                   # Daemon endpoint wrapper (start/stop lifecycle)
      testing/
        __init__.py
        fake_connector.py                           # In-memory Connector for Router tests
    tests/
      conftest.py
      test_types.py
      test_protocol.py
      test_audit.py
      test_router.py
      test_github_allowance.py
      test_github_connector.py
      test_funnel_handler.py
      test_endpoint_integration.py                  # End-to-end: simulated webhook → bus
```

---

## Task 1: Add Notification envelope kind to core/bus/envelope.py

**Files:**
- Modify: `packages/core/src/agent_core/bus/envelope.py`
- Create: `packages/core/tests/bus/test_envelope_notification.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/bus/test_envelope_notification.py
"""Notification envelope kind: round-trip + validation.

Notification is the bus envelope shape that the inbound-notifications router
publishes when an external event is classified Allow by a per-source
connector. See docs/superpowers/specs/2026-06-20-inbound-notifications-design.md.
"""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import (
    BUILTIN_KINDS,
    Envelope,
    NotificationPayload,
)


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def test_notification_kind_in_builtin_kinds():
    assert "Notification" in BUILTIN_KINDS


def test_notification_payload_minimal_round_trip():
    payload = NotificationPayload(
        source="github",
        reason="PR review requested on foreman",
        landed_at=_stamp(),
        body={"pr_number": 387, "repo": "jeffrichley/foreman"},
    )
    assert payload.kind == "Notification"
    assert payload.source == "github"
    assert payload.poll_discovered_at is None


def test_notification_envelope_round_trip():
    env = Envelope(
        id="env-1",
        correlation_id="corr-1",
        to="wren",
        kind="Notification",
        payload=NotificationPayload(
            source="github",
            reason="PR review requested on foreman",
            landed_at=_stamp(),
            body={"pr_number": 387},
        ),
        urgency="red",
        created_at=_stamp(),
    )
    dumped = env.model_dump()
    rebuilt = Envelope.model_validate(dumped)
    assert rebuilt.kind == "Notification"
    assert rebuilt.payload.source == "github"  # type: ignore[union-attr]
    assert rebuilt.urgency == "red"


def test_notification_payload_rejects_unknown_source_no_validation():
    # source is a free-form string at the bus layer; per-source validation
    # is the connector's job. The bus only enforces presence + non-empty.
    payload = NotificationPayload(
        source="gmail",
        reason="email from a known correspondent",
        landed_at=_stamp(),
        body={},
    )
    assert payload.source == "gmail"


def test_notification_payload_requires_reason():
    with pytest.raises(ValidationError):
        NotificationPayload(  # type: ignore[call-arg]
            source="github",
            landed_at=_stamp(),
            body={},
        )


def test_notification_payload_with_poll_discovered_at():
    landed = _stamp()
    discovered = datetime(2026, 6, 20, 22, 0, 30, tzinfo=UTC)
    payload = NotificationPayload(
        source="gmail",
        reason="email from a known correspondent",
        landed_at=landed,
        poll_discovered_at=discovered,
        body={"message_id": "abc"},
    )
    assert payload.poll_discovered_at == discovered


def test_envelope_kind_mismatch_rejected_for_notification():
    with pytest.raises(ValidationError, match="does not match payload kind"):
        Envelope(
            id="env-1",
            correlation_id="corr-1",
            to="wren",
            kind="TextMessage",  # mismatched
            payload=NotificationPayload(
                source="github",
                reason="x",
                landed_at=_stamp(),
                body={},
            ),
            created_at=_stamp(),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/core && uv run --no-sync pytest tests/bus/test_envelope_notification.py -v`
Expected: FAIL with `ImportError: cannot import name 'NotificationPayload'`

- [ ] **Step 3: Implement NotificationPayload + add to discriminated union + BUILTIN_KINDS**

In `packages/core/src/agent_core/bus/envelope.py`, add the new payload model below `AcknowledgmentPayload` and update the union + BUILTIN_KINDS set:

```python
class NotificationPayload(BaseModel):
    """External-source inbound notification.

    Published by the inbound-notifications router when a per-source
    connector classifies an event Allow. ``source`` is the connector
    name ("github", "gmail", "calendar"). ``reason`` is the
    connector-supplied human-readable justification (audit trail).
    ``landed_at`` is when the source event happened (e.g., the
    PR ``updated_at`` for GitHub, the ``Date:`` header for Gmail).
    ``poll_discovered_at`` is set only for cycle-based connectors
    (Gmail IMAP IDLE, calendar polls); absent for push sources.
    ``body`` is the connector-specific payload — the being's
    inbox-render handler dispatches on ``source`` to format it.

    See docs/superpowers/specs/2026-06-20-inbound-notifications-design.md.
    """

    kind: Literal["Notification"] = "Notification"
    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    landed_at: datetime
    poll_discovered_at: datetime | None = None
    body: dict[str, Any] = Field(default_factory=dict)
```

Then update the union and the kinds set:

```python
EnvelopePayload = Annotated[
    TextMessagePayload
    | EventPayload
    | ToolInvocationPayload
    | CancellationPayload
    | ProgressPayload
    | AcknowledgmentPayload
    | NotificationPayload,
    Field(discriminator="kind"),
]

BUILTIN_KINDS: frozenset[str] = frozenset({
    "TextMessage",
    "Event",
    "ToolInvocation",
    "Cancellation",
    "Progress",
    "Acknowledgment",
    "Notification",
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/core && uv run --no-sync pytest tests/bus/test_envelope_notification.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run full bus test suite to check no regression**

Run: `cd packages/core && uv run --no-sync pytest tests/bus -q`
Expected: all bus tests pass, no regression on existing kind validations.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/agent_core/bus/envelope.py packages/core/tests/bus/test_envelope_notification.py
git commit -m "feat(bus): add Notification envelope kind for inbound-notifications router"
```

---

## Task 2: Create agent-core-inbound package skeleton

**Files:**
- Create: `packages/agent-core-inbound/pyproject.toml`
- Create: `packages/agent-core-inbound/README.md`
- Create: `packages/agent-core-inbound/src/agent_core_inbound/__init__.py`
- Create: `packages/agent-core-inbound/tests/__init__.py`
- Create: `packages/agent-core-inbound/tests/conftest.py`
- Modify: `pyproject.toml` (workspace root, add member)

- [ ] **Step 1: Create the package directory + minimal pyproject.toml**

```toml
# packages/agent-core-inbound/pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agent-core-inbound"
version = "0.1.0"
description = "Inbound notifications router — deny-by-default external-event surface for agent-core beings."
readme = "README.md"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "agent-core",
    "pydantic>=2.0",
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "watchdog>=4.0",
]

[project.optional-dependencies]
test = [
    "pytest",
    "pytest-asyncio",
    "httpx",
]

[tool.uv.sources]
agent-core = { workspace = true }

[tool.hatch.build.targets.wheel]
packages = ["src/agent_core_inbound"]
```

- [ ] **Step 2: Create the package source root and tests root**

```python
# packages/agent-core-inbound/src/agent_core_inbound/__init__.py
"""Inbound notifications — deny-by-default external-event router.

See docs/superpowers/specs/2026-06-20-inbound-notifications-design.md.
"""

__version__ = "0.1.0"
```

```python
# packages/agent-core-inbound/tests/__init__.py
```

```python
# packages/agent-core-inbound/tests/conftest.py
"""Test fixtures for agent-core-inbound."""
```

- [ ] **Step 3: Create a minimal README**

```markdown
# agent-core-inbound

Deny-by-default inbound notifications router for agent-core beings.
External signals (GitHub webhooks, Gmail messages, calendar events)
flow through per-source **connectors** that classify each event as
`Allow{tier, reason}` or `Deny`. The router de-dupes, rate-limits,
delivers via the agent-core bus, and writes an audit log.

See `docs/superpowers/specs/2026-06-20-inbound-notifications-design.md`
in the agent_core repo for the full design.
```

- [ ] **Step 4: Register the package in workspace pyproject.toml**

Add to the workspace root `pyproject.toml` (find the existing `[tool.uv.workspace]` block — should already list `packages/agent-core-discord`, `packages/agent-core-voice`, etc.):

```toml
[tool.uv.workspace]
members = [
    "packages/core",
    "packages/agent-core-briefs",
    "packages/agent-core-busproxy",
    "packages/agent-core-channel",
    "packages/agent-core-discord",
    "packages/agent-core-hatchery",
    "packages/agent-core-inbound",
    "packages/agent-core-voice",
    "packages/agent-core-webcam",
    "packages/credentials",
    "packages/notify",
]
```

(The existing `members` list is the source of truth — read it first, then add the one new line for `agent-core-inbound` in alphabetical order. Do not invent the rest of the list; copy from the existing one.)

- [ ] **Step 5: Sync workspace + verify package installs**

Run: `uv sync`
Expected: workspace resolves, `agent-core-inbound` shows in dependency list.

Run: `uv run --no-sync python -c "import agent_core_inbound; print(agent_core_inbound.__version__)"`
Expected: `0.1.0`

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-inbound pyproject.toml uv.lock
git commit -m "feat(inbound): scaffold agent-core-inbound package"
```

---

## Task 3: Allow/Deny/Tier types + Connector Protocol

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/types.py`
- Create: `packages/agent-core-inbound/src/agent_core_inbound/protocol.py`
- Create: `packages/agent-core-inbound/tests/test_types.py`
- Create: `packages/agent-core-inbound/tests/test_protocol.py`

- [ ] **Step 1: Write the failing types test**

```python
# packages/agent-core-inbound/tests/test_types.py
"""Allow/Deny verdict types + Tier enum + ConnectorEvent base."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def test_tier_values():
    assert Tier.RED.value == "red"
    assert Tier.YELLOW.value == "yellow"
    assert Tier.GREEN.value == "green"


def test_allow_minimal_construction():
    a = Allow(tier=Tier.RED, reason="PR review requested on foreman")
    assert a.tier == Tier.RED
    assert a.reason == "PR review requested on foreman"


def test_allow_requires_reason():
    with pytest.raises(ValidationError):
        Allow(tier=Tier.YELLOW, reason="")  # empty reason rejected


def test_deny_has_no_body():
    d = Deny()
    # Deny carries no data — it is intentionally empty so audit-log
    # writers don't accidentally serialize an event payload alongside
    # a "denied" verdict (privacy + storage).
    assert d.model_dump() == {}


def test_connector_event_id_and_landed_at_required():
    e = ConnectorEvent(
        event_id="github-12345-67890",
        landed_at=_stamp(),
        raw={"action": "review_requested"},
    )
    assert e.event_id == "github-12345-67890"
    assert e.landed_at == _stamp()
    assert e.raw["action"] == "review_requested"


def test_connector_event_id_must_be_non_empty():
    with pytest.raises(ValidationError):
        ConnectorEvent(event_id="", landed_at=_stamp(), raw={})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_types.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement types**

```python
# packages/agent-core-inbound/src/agent_core_inbound/types.py
"""Verdict + tier + event base types for the inbound-notifications router.

Connectors return an Allow{tier, reason} or Deny verdict. Router uses
these to decide what envelope to publish (or skip) and what to write
to the audit log.
"""
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Tier(str, Enum):
    """Urgency tier emitted at source by the connector.

    Set in the connector's matched policy rule, never inferred by the
    router. Maps 1:1 to the bus envelope's urgency field downstream.
    """

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class Allow(BaseModel):
    """Connector verdict: deliver this event at this urgency.

    Reason is the audit-log justification string the connector
    supplied. Required + non-empty so audit lines are never silent.
    """

    tier: Tier
    reason: str = Field(min_length=1)

    model_config = ConfigDict(frozen=True)


class Deny(BaseModel):
    """Connector verdict: drop this event.

    Intentionally empty — Deny carries no reason and no body. The
    audit log records ``verdict=deny`` with no event payload so a
    denied event leaves no privacy-sensitive traces.
    """

    model_config = ConfigDict(frozen=True)


class ConnectorEvent(BaseModel):
    """Base shape every connector-specific event extends.

    ``event_id`` is the stable string the router uses for de-dupe;
    connectors must derive it deterministically from the source
    (GitHub delivery ID, Gmail Message-ID, ICS UID).
    ``landed_at`` is when the source event happened.
    ``raw`` is the connector-specific payload preserved verbatim for
    the downstream Notification envelope body.
    """

    event_id: str = Field(min_length=1)
    landed_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_types.py -v`
Expected: 6 passed.

- [ ] **Step 5: Write the failing Connector protocol test**

```python
# packages/agent-core-inbound/tests/test_protocol.py
"""Connector Protocol — structural typing check."""
from datetime import UTC, datetime

from agent_core_inbound.protocol import Connector
from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


class _StubConnector:
    """Minimal Connector implementation for structural-typing check."""

    name = "stub"

    def classify(self, event: ConnectorEvent, target_being: str) -> Allow | Deny:
        if target_being == "wren":
            return Allow(tier=Tier.GREEN, reason="stub allow for wren")
        return Deny()


def test_stub_satisfies_connector_protocol():
    # Protocol satisfaction is structural; isinstance(x, Connector)
    # requires Connector to be marked @runtime_checkable.
    c: Connector = _StubConnector()
    assert isinstance(c, Connector)
    assert c.name == "stub"


def test_stub_classify_returns_allow_for_wren():
    c = _StubConnector()
    e = ConnectorEvent(
        event_id="evt-1",
        landed_at=datetime(2026, 6, 20, tzinfo=UTC),
        raw={},
    )
    verdict = c.classify(e, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.tier == Tier.GREEN


def test_stub_classify_returns_deny_for_other():
    c = _StubConnector()
    e = ConnectorEvent(
        event_id="evt-2",
        landed_at=datetime(2026, 6, 20, tzinfo=UTC),
        raw={},
    )
    verdict = c.classify(e, "pepper")
    assert isinstance(verdict, Deny)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_protocol.py -v`
Expected: FAIL — `Connector` not importable.

- [ ] **Step 7: Implement Connector Protocol**

```python
# packages/agent-core-inbound/src/agent_core_inbound/protocol.py
"""Connector Protocol — per-source policy module contract.

Each connector parses one external source's events (GitHub webhooks,
Gmail messages, calendar events) and applies a TOML-driven policy
to decide which events reach which being. The router calls
classify(event, target_being) and acts on the returned Allow|Deny.
"""
from typing import Protocol, runtime_checkable

from agent_core_inbound.types import Allow, ConnectorEvent, Deny


@runtime_checkable
class Connector(Protocol):
    """Per-source policy module.

    ``name`` is the source identifier ("github", "gmail", "calendar").
    Used by the router for audit-log lines and as the Notification
    envelope's ``payload.source``.

    ``classify`` decides whether ``event`` should be delivered to
    ``target_being``. Connectors are deny-by-default: any event the
    connector's policy rules do not explicitly match returns Deny.
    """

    name: str

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,
    ) -> Allow | Deny: ...
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_protocol.py -v`
Expected: 3 passed.

- [ ] **Step 9: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/types.py packages/agent-core-inbound/src/agent_core_inbound/protocol.py packages/agent-core-inbound/tests/test_types.py packages/agent-core-inbound/tests/test_protocol.py
git commit -m "feat(inbound): Connector Protocol + Allow/Deny verdict types"
```

---

## Task 4: Audit log JSONL writer

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/audit.py`
- Create: `packages/agent-core-inbound/tests/test_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-inbound/tests/test_audit.py
"""AuditLog: one JSONL line per classification (Allow OR Deny).

Allow entries carry tier + reason + connector + rule_id; Deny entries
carry only timestamp + source + target. A Deny line MUST NOT
serialize the underlying event payload (privacy + storage), so the
writer takes ``connector_name`` and ``target_being`` directly rather
than reading them off an event.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.types import Allow, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def test_audit_log_writes_allow_line(tmp_path: Path):
    log_path = tmp_path / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_allow(
        connector_name="github",
        target_being="wren",
        verdict=Allow(tier=Tier.RED, reason="PR review requested on foreman"),
        rule_id="pr_review_requested_foreman",
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {
        "ts": "2026-06-20T22:00:00+00:00",
        "source": "github",
        "to": "wren",
        "verdict": "allow",
        "tier": "red",
        "rule_id": "pr_review_requested_foreman",
        "reason": "PR review requested on foreman",
    }


def test_audit_log_writes_deny_line(tmp_path: Path):
    log_path = tmp_path / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_deny(connector_name="github", target_being="wren")
    lines = log_path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert entry == {
        "ts": "2026-06-20T22:00:00+00:00",
        "source": "github",
        "to": "wren",
        "verdict": "deny",
    }
    # No reason, no tier, no rule_id, no event body on Deny lines.


def test_audit_log_appends_not_truncates(tmp_path: Path):
    log_path = tmp_path / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_deny(connector_name="github", target_being="wren")
    log.record_deny(connector_name="github", target_being="wren")
    log.record_allow(
        connector_name="github",
        target_being="wren",
        verdict=Allow(tier=Tier.GREEN, reason="x"),
        rule_id="r1",
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def test_audit_log_creates_parent_dir(tmp_path: Path):
    log_path = tmp_path / "nested" / "deeper" / "inbound-audit.jsonl"
    log = AuditLog(path=log_path, clock=lambda: _stamp())
    log.record_deny(connector_name="github", target_being="wren")
    assert log_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_audit.py -v`
Expected: FAIL — `AuditLog` not importable.

- [ ] **Step 3: Implement AuditLog**

```python
# packages/agent-core-inbound/src/agent_core_inbound/audit.py
"""JSONL audit log for inbound-notifications router.

One line per classification. Allow lines carry tier + reason +
rule_id; Deny lines carry only the timestamp + source + target.
Deny intentionally does NOT serialize the event body so a denied
inbound leaves no privacy-sensitive trace.
"""
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_core_inbound.types import Allow


def _default_clock() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    """Append-only JSONL writer keyed by absolute path.

    ``clock`` is injectable so tests can pin the timestamp without
    monkey-patching ``datetime.now``. Parent directories are created
    on first write.
    """

    def __init__(
        self,
        *,
        path: Path,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._path = path
        self._clock = clock

    def record_allow(
        self,
        *,
        connector_name: str,
        target_being: str,
        verdict: Allow,
        rule_id: str,
    ) -> None:
        self._write({
            "ts": self._clock().isoformat(),
            "source": connector_name,
            "to": target_being,
            "verdict": "allow",
            "tier": verdict.tier.value,
            "rule_id": rule_id,
            "reason": verdict.reason,
        })

    def record_deny(self, *, connector_name: str, target_being: str) -> None:
        self._write({
            "ts": self._clock().isoformat(),
            "source": connector_name,
            "to": target_being,
            "verdict": "deny",
        })

    def _write(self, entry: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_audit.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/audit.py packages/agent-core-inbound/tests/test_audit.py
git commit -m "feat(inbound): JSONL audit log writer (allow + deny)"
```

---

## Task 5: Fake Connector for Router tests

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/testing/__init__.py`
- Create: `packages/agent-core-inbound/src/agent_core_inbound/testing/fake_connector.py`

- [ ] **Step 1: Create the testing module**

```python
# packages/agent-core-inbound/src/agent_core_inbound/testing/__init__.py
"""Public test helpers for downstream packages.

The router substrate ships a fake Connector so other inbound-notifications
connectors (and downstream packages) can exercise the router without
implementing a full classify() loop.
"""
from agent_core_inbound.testing.fake_connector import FakeConnector

__all__ = ["FakeConnector"]
```

```python
# packages/agent-core-inbound/src/agent_core_inbound/testing/fake_connector.py
"""In-memory connector for Router tests + downstream contract tests."""
from agent_core_inbound.protocol import Connector
from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


class FakeConnector:
    """Connector whose verdict is configured per-instance.

    ``verdicts`` keys are ``(event_id, target_being)`` tuples; the
    value is the Allow|Deny to return. Unknown keys default to Deny
    (matches the deny-by-default policy a real connector enforces).
    ``rule_id_for`` returns the rule_id the router will log alongside
    an Allow verdict.
    """

    name = "fake"

    def __init__(self) -> None:
        self.verdicts: dict[tuple[str, str], Allow | Deny] = {}
        self.rule_ids: dict[tuple[str, str], str] = {}
        self.classify_calls: list[tuple[str, str]] = []

    def allow(
        self,
        *,
        event_id: str,
        target_being: str,
        tier: Tier = Tier.GREEN,
        reason: str = "test allow",
        rule_id: str = "test_rule",
    ) -> None:
        self.verdicts[(event_id, target_being)] = Allow(tier=tier, reason=reason)
        self.rule_ids[(event_id, target_being)] = rule_id

    def deny(self, *, event_id: str, target_being: str) -> None:
        self.verdicts[(event_id, target_being)] = Deny()

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,
    ) -> Allow | Deny:
        key = (event.event_id, target_being)
        self.classify_calls.append(key)
        return self.verdicts.get(key, Deny())

    def rule_id_for(self, *, event_id: str, target_being: str) -> str:
        return self.rule_ids.get((event_id, target_being), "unknown")


# Static check: FakeConnector satisfies the Connector Protocol.
_FAKE: Connector = FakeConnector()
```

- [ ] **Step 2: Quick import smoke**

Run: `cd packages/agent-core-inbound && uv run --no-sync python -c "from agent_core_inbound.testing import FakeConnector; c = FakeConnector(); c.allow(event_id='e1', target_being='wren'); print(c.classify_calls)"`
Expected: `[]` (no calls yet).

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/testing
git commit -m "feat(inbound): FakeConnector for router + downstream tests"
```

---

## Task 6: Router — receive → classify → bus delivery

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/router.py`
- Create: `packages/agent-core-inbound/tests/test_router.py`

The router is the spec's "pure plumbing" component. This task builds the happy path: receive → classify via connector → publish Notification envelope. De-dupe and rate-limit land in the next two tasks.

- [ ] **Step 1: Write the failing test (happy path + deny path)**

```python
# packages/agent-core-inbound/tests/test_router.py
"""Router: receive → classify → bus delivery + audit log."""
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.router import Router
from agent_core_inbound.testing import FakeConnector
from agent_core_inbound.types import ConnectorEvent, Tier


class _FakeBus:
    """Captures published envelopes for assertion."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(
        self,
        *,
        to: str,
        kind: str,
        payload: dict[str, Any],
        urgency: str,
    ) -> None:
        self.published.append({
            "to": to,
            "kind": kind,
            "payload": payload,
            "urgency": urgency,
        })


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def _event(event_id: str = "evt-1") -> ConnectorEvent:
    return ConnectorEvent(
        event_id=event_id,
        landed_at=_stamp(),
        raw={"pr_number": 387, "repo": "jeffrichley/foreman"},
    )


def _router(
    *,
    tmp_path: Path,
    connector: FakeConnector,
    bus: _FakeBus,
) -> Router:
    audit = AuditLog(path=tmp_path / "audit.jsonl", clock=lambda: _stamp())
    return Router(
        connectors={"fake": connector},
        bus_publish=bus.publish,
        audit=audit,
        clock=lambda: _stamp(),
    )


def test_allow_publishes_notification_envelope(tmp_path: Path):
    connector = FakeConnector()
    connector.allow(
        event_id="evt-1",
        target_being="wren",
        tier=Tier.RED,
        reason="PR review requested on foreman",
        rule_id="pr_review_requested_foreman",
    )
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    assert len(bus.published) == 1
    pub = bus.published[0]
    assert pub["to"] == "wren"
    assert pub["kind"] == "Notification"
    assert pub["urgency"] == "red"
    assert pub["payload"]["source"] == "fake"
    assert pub["payload"]["reason"] == "PR review requested on foreman"
    assert pub["payload"]["body"] == {"pr_number": 387, "repo": "jeffrichley/foreman"}


def test_allow_writes_audit_line(tmp_path: Path):
    connector = FakeConnector()
    connector.allow(
        event_id="evt-1",
        target_being="wren",
        tier=Tier.RED,
        reason="PR review requested on foreman",
        rule_id="pr_review_requested_foreman",
    )
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "allow" in lines[0]
    assert "pr_review_requested_foreman" in lines[0]


def test_deny_does_not_publish(tmp_path: Path):
    connector = FakeConnector()  # no allow rule configured → defaults to Deny
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    assert bus.published == []


def test_deny_writes_audit_line(tmp_path: Path):
    connector = FakeConnector()  # no allow rule configured → defaults to Deny
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    router.receive(
        connector_name="fake",
        target_being="wren",
        event=_event(),
    )

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "deny" in lines[0]


def test_unknown_connector_raises(tmp_path: Path):
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=FakeConnector(), bus=bus)

    with pytest.raises(KeyError, match="unknown connector"):
        router.receive(
            connector_name="never-registered",
            target_being="wren",
            event=_event(),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_router.py -v`
Expected: FAIL — `Router` not importable.

- [ ] **Step 3: Implement Router (happy path only, no de-dupe / rate-limit yet)**

```python
# packages/agent-core-inbound/src/agent_core_inbound/router.py
"""Router — receive external events, classify via connector, deliver via bus.

Pure plumbing per the design: it does NOT classify (the connector does),
it does NOT hold per-source config (the connector does), it does NOT
decide urgency (the connector does). It owns: dispatch to the connector,
de-dupe across redeliveries, rate-limit, publish to the bus, write to
the audit log.

This task implements receive() + classify routing + bus delivery + audit.
De-dupe and rate-limit land in tasks 7 and 8.
"""
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.protocol import Connector
from agent_core_inbound.types import Allow, ConnectorEvent, Deny


# The router's call into the bus. Real wiring uses the agent-core
# BusHandle; tests inject a fake. Keeping this as a callable rather
# than an interface avoids dragging the BusHandle dependency into the
# router substrate's test surface.
BusPublish = Callable[..., None]


def _default_clock() -> datetime:
    return datetime.now(UTC)


class Router:
    """De-dupe + rate-limit + classify + deliver + log.

    ``connectors`` maps connector name → Connector instance. The router
    dispatches to ``connectors[connector_name]`` on each receive(). A
    missing key raises KeyError — this surfaces wiring bugs early
    rather than silently dropping events.
    """

    def __init__(
        self,
        *,
        connectors: dict[str, Connector],
        bus_publish: BusPublish,
        audit: AuditLog,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._connectors = connectors
        self._bus_publish = bus_publish
        self._audit = audit
        self._clock = clock

    def receive(
        self,
        *,
        connector_name: str,
        target_being: str,
        event: ConnectorEvent,
    ) -> None:
        connector = self._connectors.get(connector_name)
        if connector is None:
            raise KeyError(f"unknown connector {connector_name!r}")

        verdict = connector.classify(event, target_being)
        if isinstance(verdict, Deny):
            self._audit.record_deny(
                connector_name=connector.name,
                target_being=target_being,
            )
            return

        assert isinstance(verdict, Allow)
        rule_id = self._extract_rule_id(
            connector=connector,
            event_id=event.event_id,
            target_being=target_being,
        )
        self._audit.record_allow(
            connector_name=connector.name,
            target_being=target_being,
            verdict=verdict,
            rule_id=rule_id,
        )

        # Publish Notification envelope. Body carries the connector-specific
        # raw payload preserved verbatim.
        self._bus_publish(
            to=target_being,
            kind="Notification",
            payload={
                "kind": "Notification",
                "source": connector.name,
                "reason": verdict.reason,
                "landed_at": event.landed_at.isoformat(),
                "body": event.raw,
            },
            urgency=verdict.tier.value,
        )

    @staticmethod
    def _extract_rule_id(
        *,
        connector: Connector,
        event_id: str,
        target_being: str,
    ) -> str:
        # Optional helper hook: connectors that want rich audit logs can
        # expose rule_id_for(); the router falls back to "unknown" when
        # the connector doesn't provide one. This keeps Connector's
        # required surface minimal (just name + classify).
        rule_id_for = getattr(connector, "rule_id_for", None)
        if callable(rule_id_for):
            try:
                return rule_id_for(event_id=event_id, target_being=target_being)
            except Exception:
                return "unknown"
        return "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_router.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/router.py packages/agent-core-inbound/tests/test_router.py
git commit -m "feat(inbound): Router classify + bus delivery + audit log"
```

---

## Task 7: Router de-dupe

**Files:**
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/router.py`
- Modify: `packages/agent-core-inbound/tests/test_router.py`

Adds an in-memory bounded-size set of recently-seen `(connector_name, event_id, target_being)` triples. A redelivery (GitHub retry, IMAP re-poll on reconnect) hits the de-dupe cache and is silently dropped (no audit entry — de-dupe of an already-allowed event is structural, not a denial).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_router.py`:

```python
def test_redelivered_event_is_deduped_and_not_republished(tmp_path: Path):
    connector = FakeConnector()
    connector.allow(
        event_id="evt-redeliver",
        target_being="wren",
        tier=Tier.RED,
        reason="PR review requested on foreman",
        rule_id="r1",
    )
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    ev = ConnectorEvent(
        event_id="evt-redeliver",
        landed_at=_stamp(),
        raw={"x": 1},
    )

    router.receive(connector_name="fake", target_being="wren", event=ev)
    router.receive(connector_name="fake", target_being="wren", event=ev)
    router.receive(connector_name="fake", target_being="wren", event=ev)

    # First delivery publishes; the next two are dropped by de-dupe.
    assert len(bus.published) == 1
    assert len(connector.classify_calls) == 1  # connector not re-called for dupes


def test_dedupe_is_scoped_to_target_being(tmp_path: Path):
    # Same event_id, different target_being → distinct entries. This is
    # the gmail-fan-out shape (one email reaches both Pepper and Jeff)
    # so the router cannot collapse them.
    connector = FakeConnector()
    connector.allow(event_id="evt-shared", target_being="wren", reason="r")
    connector.allow(event_id="evt-shared", target_being="pepper", reason="r")
    bus = _FakeBus()
    router = _router(tmp_path=tmp_path, connector=connector, bus=bus)

    ev = ConnectorEvent(event_id="evt-shared", landed_at=_stamp(), raw={})

    router.receive(connector_name="fake", target_being="wren", event=ev)
    router.receive(connector_name="fake", target_being="pepper", event=ev)

    assert len(bus.published) == 2
    assert {p["to"] for p in bus.published} == {"wren", "pepper"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_router.py -v`
Expected: FAIL — duplicates re-publish.

- [ ] **Step 3: Add bounded LRU de-dupe to Router**

Replace the existing `Router` class in `router.py` — add the `_seen` ordered-dict and the de-dupe check at the top of `receive()`:

```python
from collections import OrderedDict


# Cap on the LRU of recently-seen (connector, event_id, target_being)
# triples. 4096 is generous enough that GitHub's ~5 retry delivery
# window will never roll out under realistic burst, but small enough
# that a stuck-source scenario can't grow memory without bound.
_DEDUPE_CAPACITY: int = 4096


class Router:
    def __init__(
        self,
        *,
        connectors: dict[str, Connector],
        bus_publish: BusPublish,
        audit: AuditLog,
        clock: Callable[[], datetime] = _default_clock,
        dedupe_capacity: int = _DEDUPE_CAPACITY,
    ) -> None:
        self._connectors = connectors
        self._bus_publish = bus_publish
        self._audit = audit
        self._clock = clock
        self._seen: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._dedupe_capacity = dedupe_capacity

    def receive(
        self,
        *,
        connector_name: str,
        target_being: str,
        event: ConnectorEvent,
    ) -> None:
        connector = self._connectors.get(connector_name)
        if connector is None:
            raise KeyError(f"unknown connector {connector_name!r}")

        # De-dupe: skip if we've delivered this (connector, event_id,
        # target_being) recently. Skipped events do NOT write to the
        # audit log — they are structurally identical to the prior
        # delivery, which already has its own audit line.
        dedupe_key = (connector_name, event.event_id, target_being)
        if dedupe_key in self._seen:
            self._seen.move_to_end(dedupe_key)
            return

        verdict = connector.classify(event, target_being)
        if isinstance(verdict, Deny):
            self._audit.record_deny(
                connector_name=connector.name,
                target_being=target_being,
            )
            return

        assert isinstance(verdict, Allow)
        rule_id = self._extract_rule_id(
            connector=connector,
            event_id=event.event_id,
            target_being=target_being,
        )
        self._audit.record_allow(
            connector_name=connector.name,
            target_being=target_being,
            verdict=verdict,
            rule_id=rule_id,
        )
        self._bus_publish(
            to=target_being,
            kind="Notification",
            payload={
                "kind": "Notification",
                "source": connector.name,
                "reason": verdict.reason,
                "landed_at": event.landed_at.isoformat(),
                "body": event.raw,
            },
            urgency=verdict.tier.value,
        )

        # Record successful delivery in the de-dupe cache. Evict the
        # oldest entry when the cache is full.
        self._seen[dedupe_key] = None
        if len(self._seen) > self._dedupe_capacity:
            self._seen.popitem(last=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_router.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/router.py packages/agent-core-inbound/tests/test_router.py
git commit -m "feat(inbound): bounded-LRU de-dupe on (connector, event_id, target_being)"
```

---

## Task 8: Router rate-limit

**Files:**
- Modify: `packages/agent-core-inbound/src/agent_core_inbound/router.py`
- Modify: `packages/agent-core-inbound/tests/test_router.py`

Per-(source, target) token-bucket rate limiter. A bursty source can't flood a being's inbox; over-quota events are denied and logged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_router.py`:

```python
def test_rate_limit_caps_per_source_per_target(tmp_path: Path):
    # Configure rate limit to 3 events / minute per (source, target).
    # The 4th allow-classified event is rejected by the rate limiter
    # and logged as a deny.
    connector = FakeConnector()
    for i in range(5):
        connector.allow(event_id=f"evt-{i}", target_being="wren", reason="r")
    bus = _FakeBus()
    audit = AuditLog(path=tmp_path / "audit.jsonl", clock=lambda: _stamp())
    router = Router(
        connectors={"fake": connector},
        bus_publish=bus.publish,
        audit=audit,
        clock=lambda: _stamp(),
        rate_limits={("fake", "wren"): (3, 60.0)},  # 3 per 60s
    )

    for i in range(5):
        router.receive(
            connector_name="fake",
            target_being="wren",
            event=ConnectorEvent(event_id=f"evt-{i}", landed_at=_stamp(), raw={}),
        )

    # Only 3 reached the bus.
    assert len(bus.published) == 3
    # The other 2 are recorded as deny in the audit log (rate-limited
    # events count as denials so the operator sees the throttle is
    # firing).
    deny_lines = [ln for ln in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines() if '"verdict":"deny"' in ln]
    assert len(deny_lines) == 2


def test_rate_limit_default_is_no_limit(tmp_path: Path):
    connector = FakeConnector()
    for i in range(50):
        connector.allow(event_id=f"evt-{i}", target_being="wren", reason="r")
    bus = _FakeBus()
    audit = AuditLog(path=tmp_path / "audit.jsonl", clock=lambda: _stamp())
    router = Router(
        connectors={"fake": connector},
        bus_publish=bus.publish,
        audit=audit,
        clock=lambda: _stamp(),
        # rate_limits omitted → no caps.
    )

    for i in range(50):
        router.receive(
            connector_name="fake",
            target_being="wren",
            event=ConnectorEvent(event_id=f"evt-{i}", landed_at=_stamp(), raw={}),
        )
    assert len(bus.published) == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_router.py -v`
Expected: FAIL — `Router.__init__()` doesn't accept `rate_limits`.

- [ ] **Step 3: Add token-bucket rate-limiter to Router**

In `router.py`, add the rate-limiter class above `Router` and thread it through:

```python
from collections import OrderedDict, deque


class _TokenBucket:
    """Sliding-window count: N events allowed per ``window_seconds``.

    Implemented as a deque of event timestamps; on each consume() we
    drop entries older than the window and check the remaining count.
    Simple and correct for the ~10s-of-events-per-minute scale we
    actually care about.
    """

    def __init__(self, *, capacity: int, window_seconds: float) -> None:
        self._capacity = capacity
        self._window = window_seconds
        self._stamps: deque[datetime] = deque()

    def consume(self, now: datetime) -> bool:
        cutoff = now.timestamp() - self._window
        while self._stamps and self._stamps[0].timestamp() < cutoff:
            self._stamps.popleft()
        if len(self._stamps) >= self._capacity:
            return False
        self._stamps.append(now)
        return True


class Router:
    def __init__(
        self,
        *,
        connectors: dict[str, Connector],
        bus_publish: BusPublish,
        audit: AuditLog,
        clock: Callable[[], datetime] = _default_clock,
        dedupe_capacity: int = _DEDUPE_CAPACITY,
        rate_limits: dict[tuple[str, str], tuple[int, float]] | None = None,
    ) -> None:
        self._connectors = connectors
        self._bus_publish = bus_publish
        self._audit = audit
        self._clock = clock
        self._seen: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._dedupe_capacity = dedupe_capacity
        self._buckets: dict[tuple[str, str], _TokenBucket] = {}
        for key, (capacity, window) in (rate_limits or {}).items():
            self._buckets[key] = _TokenBucket(
                capacity=capacity, window_seconds=window,
            )
```

In `receive()`, after the de-dupe check and AFTER `verdict = connector.classify(...)` confirms `Allow`, insert the bucket check:

```python
        assert isinstance(verdict, Allow)

        # Rate limit: per-(source, target) token bucket. Over-quota
        # events count as denials so operators see the throttle fired.
        bucket = self._buckets.get((connector_name, target_being))
        if bucket is not None and not bucket.consume(self._clock()):
            self._audit.record_deny(
                connector_name=connector.name,
                target_being=target_being,
            )
            return

        rule_id = self._extract_rule_id(...)
        # ... existing allow path continues unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_router.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/router.py packages/agent-core-inbound/tests/test_router.py
git commit -m "feat(inbound): per-(source, target) token-bucket rate limit"
```

---

## Task 9: GitHub event shape + AllowanceConfig TOML schema

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/github_event.py`
- Create: `packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py`
- Create: `packages/agent-core-inbound/tests/test_github_allowance.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-inbound/tests/test_github_allowance.py
"""GitHub allowance TOML schema + loader."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_core_inbound.github_allowance import (
    AllowanceConfig,
    AllowRule,
    load_allowance,
)
from agent_core_inbound.types import Tier


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_empty_allowance(tmp_path: Path):
    p = _write(tmp_path / "g.toml", "")
    cfg = load_allowance(p)
    assert cfg.allow == []


def test_load_single_rule(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
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
    cfg = load_allowance(p)
    assert len(cfg.allow) == 1
    rule = cfg.allow[0]
    assert rule.rule_id == "pr_review_requested_foreman"
    assert rule.event == "pull_request_review_requested"
    assert rule.repo == "jeffrichley/foreman"
    assert rule.reviewer == "wrenrichley"
    assert rule.tier == Tier.RED
    assert rule.reason == "PR review requested on foreman"


def test_load_rejects_unknown_tier(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "x"
event = "issue_comment"
tier = "purple"
reason = "x"
""",
    )
    with pytest.raises(ValidationError):
        load_allowance(p)


def test_rule_id_unique_across_rules_enforced(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "duplicate"
event = "issue_comment"
tier = "yellow"
reason = "a"

[[allow]]
rule_id = "duplicate"
event = "pull_request_review_requested"
tier = "red"
reason = "b"
""",
    )
    with pytest.raises(ValidationError, match="duplicate rule_id"):
        load_allowance(p)


def test_rule_id_required(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
event = "issue_comment"
tier = "yellow"
reason = "x"
""",
    )
    with pytest.raises(ValidationError):
        load_allowance(p)


def test_reason_required(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "x"
event = "issue_comment"
tier = "yellow"
""",
    )
    with pytest.raises(ValidationError):
        load_allowance(p)


def test_body_contains_optional(tmp_path: Path):
    p = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "mention_in_agent_core"
event = "issue_comment"
repo = "jeffrichley/agent_core"
body_contains = "@wrenrichley"
tier = "yellow"
reason = "@-mention in agent_core issue thread"
""",
    )
    cfg = load_allowance(p)
    assert cfg.allow[0].body_contains == "@wrenrichley"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_github_allowance.py -v`
Expected: FAIL — `AllowanceConfig` not importable.

- [ ] **Step 3: Implement GitHub event shapes**

```python
# packages/agent-core-inbound/src/agent_core_inbound/github_event.py
"""Typed GitHub webhook event subset for v1.a.

The full GitHub webhook payload is a large open shape; v1.a needs only
the slice the policy rules examine. Connector parses the raw JSON into
one of these typed shapes; unknown actions / unsupported event types
fall through to GitHubUnknownEvent which the policy always denies.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_core_inbound.types import ConnectorEvent


class GitHubEvent(ConnectorEvent):
    """Common base; ``action`` and ``repo_full_name`` are the two
    fields every event we care about carries.
    """

    action: str
    repo_full_name: str  # "jeffrichley/foreman"
    event_type: str      # "pull_request" | "issue_comment" | ...
    model_config = ConfigDict(extra="allow")


class GitHubPullRequestReviewRequestedEvent(GitHubEvent):
    event_type: Literal["pull_request"] = "pull_request"
    action: Literal["review_requested"] = "review_requested"
    pr_number: int
    requested_reviewer_login: str | None = None


class GitHubIssueCommentEvent(GitHubEvent):
    event_type: Literal["issue_comment"] = "issue_comment"
    action: Literal["created", "edited", "deleted"]
    issue_number: int
    comment_body: str = ""
    comment_author_login: str = ""


class GitHubIssuesLabeledEvent(GitHubEvent):
    event_type: Literal["issues"] = "issues"
    action: Literal["labeled"] = "labeled"
    issue_number: int
    label_name: str
```

```python
# packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py
"""TOML-driven GitHub allowance policy.

The principal being (Wren) edits this file directly to manage their
GitHub inbound rules. The router watches mtime and reloads on change
(see Task 10).
"""
from __future__ import annotations

import tomllib
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_core_inbound.types import Tier


class AllowRule(BaseModel):
    """One allowance rule. First-match-wins in classify()."""

    rule_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    tier: Tier
    reason: str = Field(min_length=1)

    # Optional match constraints. All present constraints must match
    # for the rule to apply. Missing constraints are wildcard.
    repo: str | None = None
    reviewer: str | None = None
    label_name: str | None = None
    body_contains: str | None = None

    model_config = ConfigDict(extra="forbid")


class AllowanceConfig(BaseModel):
    """The complete allowance policy."""

    allow: list[AllowRule] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _enforce_unique_rule_ids(self) -> "AllowanceConfig":
        counts = Counter(r.rule_id for r in self.allow)
        dups = [rid for rid, n in counts.items() if n > 1]
        if dups:
            raise ValueError(f"duplicate rule_id(s): {', '.join(sorted(dups))}")
        return self


def load_allowance(path: Path) -> AllowanceConfig:
    """Read + validate the TOML at ``path``.

    Returns an empty config (allow=[]) for an empty or missing-rules
    file. Validation errors propagate as ``pydantic.ValidationError``
    for the caller to log / surface.
    """
    if not path.exists():
        return AllowanceConfig(allow=[])
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return AllowanceConfig.model_validate(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_github_allowance.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_event.py packages/agent-core-inbound/src/agent_core_inbound/github_allowance.py packages/agent-core-inbound/tests/test_github_allowance.py
git commit -m "feat(inbound): GitHub event types + TOML allowance config schema"
```

---

## Task 10: GitHubConnector — rules engine + mtime reload

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/github_connector.py`
- Create: `packages/agent-core-inbound/tests/test_github_connector.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-inbound/tests/test_github_connector.py
"""GitHubConnector: first-match-wins rule eval + mtime reload."""
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.github_event import (
    GitHubIssueCommentEvent,
    GitHubPullRequestReviewRequestedEvent,
)
from agent_core_inbound.types import Allow, Deny, Tier


def _stamp() -> datetime:
    return datetime(2026, 6, 20, 22, 0, 0, tzinfo=UTC)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_pr_review_requested_allowed(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
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
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-1",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=387,
        requested_reviewer_login="wrenrichley",
        raw={"pr_number": 387},
    )
    verdict = conn.classify(event, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.tier == Tier.RED
    assert verdict.reason == "PR review requested on foreman"


def test_pr_review_requested_wrong_reviewer_denied(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "x"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-2",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=387,
        requested_reviewer_login="other-bot",
        raw={},
    )
    assert isinstance(conn.classify(event, "wren"), Deny)


def test_body_contains_match_required(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "mention"
event = "issue_comment"
repo = "jeffrichley/agent_core"
body_contains = "@wrenrichley"
tier = "yellow"
reason = "@-mention in agent_core issue thread"
""",
    )
    conn = GitHubConnector(config_path=cfg)

    matching = GitHubIssueCommentEvent(
        event_id="gh-3",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/agent_core",
        action="created",
        issue_number=200,
        comment_body="hey @wrenrichley please look at this",
        comment_author_login="jeffrichley",
        raw={},
    )
    non_matching = GitHubIssueCommentEvent(
        event_id="gh-4",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/agent_core",
        action="created",
        issue_number=200,
        comment_body="random comment",
        comment_author_login="someone",
        raw={},
    )
    assert isinstance(conn.classify(matching, "wren"), Allow)
    assert isinstance(conn.classify(non_matching, "wren"), Deny)


def test_first_match_wins(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "specific"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "specific"

[[allow]]
rule_id = "fallback"
event = "pull_request_review_requested"
tier = "yellow"
reason = "fallback"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-5",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    verdict = conn.classify(event, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.reason == "specific"  # first match


def test_rule_id_for_returns_matched_rule_id(tmp_path: Path):
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "PR review requested on foreman"
""",
    )
    conn = GitHubConnector(config_path=cfg)
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-6",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    conn.classify(event, "wren")
    assert conn.rule_id_for(event_id="gh-6", target_being="wren") == "pr_review_requested_foreman"


def test_mtime_reload_picks_up_new_rule(tmp_path: Path):
    cfg = _write(tmp_path / "g.toml", "")
    conn = GitHubConnector(config_path=cfg)

    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-7",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    assert isinstance(conn.classify(event, "wren"), Deny)

    # Sleep briefly to guarantee mtime jumps a whole second (Windows
    # filesystem resolution).
    time.sleep(1.1)

    _write(
        cfg,
        """
[[allow]]
rule_id = "added_after_first_load"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "new rule"
""",
    )
    verdict = conn.classify(event, "wren")
    assert isinstance(verdict, Allow)
    assert verdict.reason == "new rule"


def test_unknown_target_being_does_not_match_allow(tmp_path: Path):
    # v1.a only routes to wren. A connector configured against Wren's
    # allowance file should deny if someone calls classify with
    # target_being other than "wren". The being-binding is not in the
    # TOML rule shape (yet) — the router level binding is one connector
    # per being. So a defensive deny here is the right shape for v1.a.
    cfg = _write(
        tmp_path / "g.toml",
        """
[[allow]]
rule_id = "x"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
tier = "red"
reason = "x"
""",
    )
    conn = GitHubConnector(config_path=cfg, principal_being="wren")
    event = GitHubPullRequestReviewRequestedEvent(
        event_id="gh-8",
        landed_at=_stamp(),
        repo_full_name="jeffrichley/foreman",
        pr_number=1,
        raw={},
    )
    assert isinstance(conn.classify(event, "pepper"), Deny)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_github_connector.py -v`
Expected: FAIL — `GitHubConnector` not importable.

- [ ] **Step 3: Implement GitHubConnector**

```python
# packages/agent-core-inbound/src/agent_core_inbound/github_connector.py
"""GitHubConnector — TOML-policy classifier with mtime reload.

One connector instance per principal being. ``principal_being`` is
the only target_being for which the connector will return Allow;
calls with any other target_being deny without evaluating rules.
This enforces the "one connector per being" v1.a binding without
needing a multi-being constraint in the TOML rule shape.
"""
from __future__ import annotations

from pathlib import Path
from typing import cast

from agent_core_inbound.github_allowance import (
    AllowanceConfig,
    AllowRule,
    load_allowance,
)
from agent_core_inbound.github_event import (
    GitHubEvent,
    GitHubIssueCommentEvent,
    GitHubIssuesLabeledEvent,
    GitHubPullRequestReviewRequestedEvent,
)
from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


# Maps event-type discriminator → policy rule's ``event`` field.
_EVENT_KEYS: dict[type[GitHubEvent], str] = {
    GitHubPullRequestReviewRequestedEvent: "pull_request_review_requested",
    GitHubIssueCommentEvent: "issue_comment",
    GitHubIssuesLabeledEvent: "issues_labeled",
}


class GitHubConnector:
    name = "github"

    def __init__(
        self,
        *,
        config_path: Path,
        principal_being: str = "wren",
    ) -> None:
        self._config_path = config_path
        self._principal_being = principal_being
        self._config: AllowanceConfig = AllowanceConfig(allow=[])
        self._loaded_mtime: float | None = None
        self._last_matched_rule_id: dict[tuple[str, str], str] = {}
        self._reload_if_needed()

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,
    ) -> Allow | Deny:
        if target_being != self._principal_being:
            return Deny()
        if not isinstance(event, GitHubEvent):
            return Deny()
        self._reload_if_needed()
        rule = self._first_matching_rule(event)
        if rule is None:
            return Deny()
        self._last_matched_rule_id[(event.event_id, target_being)] = rule.rule_id
        return Allow(tier=rule.tier, reason=rule.reason)

    def rule_id_for(self, *, event_id: str, target_being: str) -> str:
        return self._last_matched_rule_id.get((event_id, target_being), "unknown")

    def _reload_if_needed(self) -> None:
        if not self._config_path.exists():
            return
        current_mtime = self._config_path.stat().st_mtime
        if self._loaded_mtime is not None and current_mtime <= self._loaded_mtime:
            return
        self._config = load_allowance(self._config_path)
        self._loaded_mtime = current_mtime

    def _first_matching_rule(self, event: GitHubEvent) -> AllowRule | None:
        event_key = _event_key_for(event)
        for rule in self._config.allow:
            if rule.event != event_key:
                continue
            if rule.repo is not None and rule.repo != event.repo_full_name:
                continue
            if rule.reviewer is not None:
                if not isinstance(event, GitHubPullRequestReviewRequestedEvent):
                    continue
                if event.requested_reviewer_login != rule.reviewer:
                    continue
            if rule.label_name is not None:
                if not isinstance(event, GitHubIssuesLabeledEvent):
                    continue
                if event.label_name != rule.label_name:
                    continue
            if rule.body_contains is not None:
                if not isinstance(event, GitHubIssueCommentEvent):
                    continue
                if rule.body_contains not in event.comment_body:
                    continue
            return rule
        return None


def _event_key_for(event: GitHubEvent) -> str:
    for type_, key in _EVENT_KEYS.items():
        if isinstance(event, type_):
            return key
    return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_github_connector.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/github_connector.py packages/agent-core-inbound/tests/test_github_connector.py
git commit -m "feat(inbound): GitHubConnector rules engine + mtime-driven reload"
```

---

## Task 11: Funnel HTTPS handler — FastAPI + signature verification + event parsing

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/funnel_handler.py`
- Create: `packages/agent-core-inbound/tests/test_funnel_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/agent-core-inbound/tests/test_funnel_handler.py
"""FastAPI handler for the Tailscale Funnel HTTPS endpoint.

Validates GitHub webhook signatures (X-Hub-Signature-256) and
translates the JSON payload into a typed GitHubEvent that gets
handed to Router.receive().
"""
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_funnel_handler.py -v`
Expected: FAIL — `build_funnel_app` not importable.

- [ ] **Step 3: Implement the FastAPI handler**

```python
# packages/agent-core-inbound/src/agent_core_inbound/funnel_handler.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_funnel_handler.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/funnel_handler.py packages/agent-core-inbound/tests/test_funnel_handler.py
git commit -m "feat(inbound): FastAPI Funnel handler with HMAC-SHA256 verification"
```

---

## Task 12: End-to-end integration test

**Files:**
- Create: `packages/agent-core-inbound/tests/test_endpoint_integration.py`

A single test exercises the full chain: simulated GitHub webhook → signature verify → parse → connector classify (against a real TOML file on disk) → router de-dupe → router rate-limit → audit log → bus publish.

- [ ] **Step 1: Write the integration test**

```python
# packages/agent-core-inbound/tests/test_endpoint_integration.py
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

import pytest
from fastapi.testclient import TestClient

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router


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
    assert pub["urgency"] == "red"
    assert pub["payload"]["reason"] == "PR review requested on foreman"
    assert pub["payload"]["body"]["pull_request"]["number"] == 387

    # Audit log shows the allow line with rule_id.
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "pr_review_requested_foreman" in lines[0]
    assert '"verdict":"allow"' in lines[0]


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
    assert '"verdict":"deny"' in lines[0]
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd packages/agent-core-inbound && uv run --no-sync pytest tests/test_endpoint_integration.py -v`
Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-inbound/tests/test_endpoint_integration.py
git commit -m "test(inbound): end-to-end webhook -> classify -> bus publish chain"
```

---

## Task 13: Daemon endpoint wrapper + agent_core.yaml registration

**Files:**
- Create: `packages/agent-core-inbound/src/agent_core_inbound/endpoint.py`
- Read first: `packages/core/src/agent_core/endpoints/scheduler.py` (pattern reference)

The endpoint wrapper integrates the inbound-notifications router with the daemon's lifecycle so `agent-core bus run` starts/stops it cleanly alongside discord, voice, etc.

- [ ] **Step 1: Read the existing endpoint pattern**

Run: `head -80 packages/core/src/agent_core/endpoints/scheduler.py`

Confirm what `start()`/`stop()` shape the daemon expects and how endpoints get their config block from `agent_core.yaml`. Different agent-core versions slightly differ in this contract; the daemon's endpoint loader is the source of truth, not stale doc.

- [ ] **Step 2: Implement the endpoint wrapper**

```python
# packages/agent-core-inbound/src/agent_core_inbound/endpoint.py
"""Daemon endpoint wrapper for the inbound-notifications router.

Wires the FastAPI Funnel handler + Router into the daemon lifecycle.
Configuration is sourced from the endpoint's block in agent_core.yaml:

  endpoints:
    inbound:
      module: agent_core_inbound.endpoint
      class: InboundEndpoint
      args:
        target_being: wren
        listen_host: 127.0.0.1
        listen_port: 8765
        webhook_secret_env: FOREMAN_GITHUB_WEBHOOK_SECRET
        github_allowance_path: ~/.wren/.config/inbound/github-allowance.toml
        audit_log_path: ~/.wren/state/inbound-audit.jsonl
        rate_limit_per_minute: 30

Tailscale Funnel is configured OUT-OF-PROCESS via the operator's
`tailscale funnel <port>` command pointing at ``listen_port``. The
endpoint binds to ``listen_host`` (default 127.0.0.1) so the only
public reach is through the tailnet-issued Funnel URL.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn

from agent_core_inbound.audit import AuditLog
from agent_core_inbound.funnel_handler import build_funnel_app
from agent_core_inbound.github_connector import GitHubConnector
from agent_core_inbound.router import Router


class InboundEndpoint:
    """Daemon-lifecycle wrapper for the inbound-notifications router."""

    def __init__(
        self,
        *,
        bus_handle,  # agent_core.bus.handle.BusHandle (avoid hard import here)
        target_being: str,
        listen_host: str,
        listen_port: int,
        webhook_secret_env: str,
        github_allowance_path: str,
        audit_log_path: str,
        rate_limit_per_minute: int = 30,
    ) -> None:
        self._bus = bus_handle
        self._target_being = target_being
        self._listen_host = listen_host
        self._listen_port = listen_port

        secret = os.environ.get(webhook_secret_env)
        if not secret:
            raise RuntimeError(
                f"inbound endpoint: env var {webhook_secret_env} not set "
                f"(needed for GitHub webhook HMAC signature verification)"
            )
        self._webhook_secret = secret.encode("utf-8")

        connector = GitHubConnector(
            config_path=Path(github_allowance_path).expanduser(),
            principal_being=target_being,
        )
        audit = AuditLog(path=Path(audit_log_path).expanduser())
        self._router = Router(
            connectors={"github": connector},
            bus_publish=self._bus_publish_adapter,
            audit=audit,
            rate_limits={("github", target_being): (rate_limit_per_minute, 60.0)},
        )
        self._app = build_funnel_app(
            router=self._router,
            webhook_secret=self._webhook_secret,
            target_being=target_being,
        )
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the FastAPI server in a background task."""
        config = uvicorn.Config(
            self._app,
            host=self._listen_host,
            port=self._listen_port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._serve_task = asyncio.create_task(self._server.serve())

    async def stop(self) -> None:
        """Stop the server gracefully."""
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._serve_task.cancel()

    def _bus_publish_adapter(
        self,
        *,
        to: str,
        kind: str,
        payload: dict,
        urgency: str,
    ) -> None:
        """Bridge between the Router's bus_publish callable and the
        daemon's BusHandle.send() API.

        Daemons differ in BusHandle method names across versions; the
        endpoint loader's runtime check during `agent-core bus run` is
        where this gets wired live. The adapter signature stays stable
        for the Router substrate.
        """
        self._bus.send(  # type: ignore[union-attr]
            to=to,
            kind=kind,
            payload=payload,
            urgency=urgency,
        )
```

- [ ] **Step 3: Manual lifecycle smoke (no test — daemon integration is operator-driven)**

This task does NOT add a pytest because the endpoint runs an asyncio uvicorn server inside the daemon's event loop, which is intrinsically integration territory (live ports, signal handling, bus wiring). The next task documents the smoke procedure operators run by hand.

Run a quick type-check pass instead to catch obvious mistakes:

Run: `cd packages/agent-core-inbound && uv run --no-sync mypy src/agent_core_inbound/endpoint.py`
Expected: clean (note: `bus_handle: BusHandle` is typed as `object` here to avoid the hard dep; runtime duck-typing in the adapter is intentional).

- [ ] **Step 4: Commit**

```bash
git add packages/agent-core-inbound/src/agent_core_inbound/endpoint.py
git commit -m "feat(inbound): daemon endpoint wrapper (FastAPI under uvicorn)"
```

---

## Task 14: Smoke procedure documentation

**Files:**
- Modify: `packages/agent-core-inbound/README.md`

Operator-facing smoke procedure: how to wire the endpoint into a real daemon + Tailscale Funnel and verify against `jeffrichley/foreman`. This is documentation, not code; it's the runbook the operator follows when bringing v1.a online.

- [ ] **Step 1: Add the operator smoke section to README**

Append to `packages/agent-core-inbound/README.md`:

```markdown
## Bringing v1.a online (operator runbook)

### 1. Generate the GitHub webhook secret

Pick any high-entropy string; e.g.:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it as an env var in the daemon's environment (e.g., your `~/.agent-core/.env` or systemd unit):

```bash
FOREMAN_GITHUB_WEBHOOK_SECRET=<paste-here>
```

### 2. Write Wren's allowance file

`~/.wren/.config/inbound/github-allowance.toml`:

```toml
[[allow]]
rule_id = "pr_review_requested_foreman"
event = "pull_request_review_requested"
repo = "jeffrichley/foreman"
reviewer = "wrenrichley"
tier = "red"
reason = "PR review requested on foreman"
```

### 3. Configure the endpoint in agent_core.yaml

```yaml
endpoints:
  inbound:
    module: agent_core_inbound.endpoint
    class: InboundEndpoint
    args:
      target_being: wren
      listen_host: 127.0.0.1
      listen_port: 8765
      webhook_secret_env: FOREMAN_GITHUB_WEBHOOK_SECRET
      github_allowance_path: ~/.wren/.config/inbound/github-allowance.toml
      audit_log_path: ~/.wren/state/inbound-audit.jsonl
      rate_limit_per_minute: 30
```

### 4. Start Tailscale Funnel

```bash
tailscale funnel 8765
```

Note the issued `https://router.<tailnet>.ts.net` URL.

### 5. Configure the GitHub webhook

In the `jeffrichley/foreman` repo settings → Webhooks → Add webhook:

- Payload URL: `https://router.<tailnet>.ts.net/github`
- Content type: `application/json`
- Secret: the same value you stored in `FOREMAN_GITHUB_WEBHOOK_SECRET`
- Which events: `Pull request reviews` (specifically `Pull request review requested`)

### 6. Smoke test

On any PR in `jeffrichley/foreman`, request a review from `@wrenrichley`. Within ~10s:

- `~/.wren/state/inbound-audit.jsonl` gains an `allow` line with `rule_id=pr_review_requested_foreman`.
- Wren's bus inbox receives a `Notification` envelope (urgency `red`).

If you instead see a `deny` line, double-check `reviewer = "wrenrichley"` in the allowance TOML against the actual reviewer GitHub login.

### Troubleshooting

- **All POSTs land 401:** the env var secret does not match the GitHub webhook secret. Re-paste both.
- **Webhook delivers but no bus envelope:** check the audit log first. If `deny` lines appear, the allowance rule isn't matching — verify `event`, `repo`, `reviewer` fields against the actual webhook payload (visible in GitHub's webhook delivery history).
- **No audit log writes at all:** the endpoint isn't seeing the POST. Confirm Tailscale Funnel is active (`tailscale funnel status`) and the daemon log shows `InboundEndpoint started on 127.0.0.1:8765`.
```

- [ ] **Step 2: Commit**

```bash
git add packages/agent-core-inbound/README.md
git commit -m "docs(inbound): operator runbook for bringing v1.a online"
```

---

## Task 15: Final just check sweep

- [ ] **Step 1: Run the full repo gate**

Run: `just check`
Expected: ruff + mypy + lint-imports + pytest all green; 85% coverage gate met.

If any task's tests added uncovered branches, push branch coverage by extending the relevant `tests/test_*.py` with the missing case. Do NOT lower the coverage gate.

- [ ] **Step 2: Verify branch is committed cleanly**

Run: `git status`
Expected: clean working tree, branch shows 14+ commits ahead of `origin/feat/inbound-notifications-spec` (one per task).

- [ ] **Step 3: Push the branch**

```bash
GH_TOKEN=$(python C:/Users/jeffr/.wren/.claude/skills/creds-management/scripts/creds.py --being wren get github --keyring --password)
git push "https://wrenrichley:${GH_TOKEN}@github.com/jeffrichley/agent_core.git" feat/inbound-notifications-spec:feat/inbound-notifications-spec
```

Pre-push hook runs `just check` again as a defensive gate — should still be green.

(Note: this push is the only one the plan authorizes; intermediate task commits stay local until this final push.)

---

## Out of scope (do not implement in this plan)

- **v1.b — Gmail → Pepper.** The IMAP IDLE connector, `email-allowance.toml` schema, two-timestamp envelope shape (`poll_discovered_at`) wiring beyond the bus-layer field already in NotificationPayload. Gets its own plan.
- **v1.c — Calendar.** Reserved slot in the spec sequence; design TBD post-v1.a operational signal.
- **Connector hot-reload via watchdog.** The mtime-on-classify check in Task 10 is sufficient for v1.a. A watchdog-based push reload is a Task-15-style polish item, not on the critical path.
- **Public dashboards / metrics endpoint.** Audit log is the v1.a observability surface.
- **GitHub App authentication beyond webhook verification.** v1.a is one-way (inbound only); outbound GitHub calls remain a separate concern.
- **Anti-abuse on the Funnel URL beyond signature verification.** If the URL leaks and traffic from non-GitHub IPs appears, signatures will fail and audit logs will show the deny spike — operational signal first, mitigation second.

---

## Plan self-review (filled in)

**Spec coverage:**

| Spec section | Task |
|---|---|
| Framing principle: deny by default | Task 6 (Router default), Task 10 (Connector default) |
| Connector Protocol | Task 3 |
| Router responsibilities (de-dupe, rate-limit, deliver, audit) | Tasks 4, 6, 7, 8 |
| Per-source connector as policy module | Tasks 9, 10 |
| Urgency tier set at source | Tasks 3 (Tier enum), 9 (rule schema), 10 (classify reads rule.tier) |
| Trust boundary (Tailscale Funnel) | Tasks 11 (HMAC verify), 13 (bind 127.0.0.1) |
| Cross-being signals = bus kinds, not webhooks | Out-of-scope by design (this plan only adds inbound) |
| Drift-honest envelope (two timestamps) | Task 1 (NotificationPayload carries `poll_discovered_at` for v1.b) |
| v1.a — GitHub → Wren push via Funnel | Tasks 9, 10, 11, 12, 13, 14 |
| v1.b, v1.c | Out of scope (own plans later) |
| Envelope shape | Task 1 |
| Audit log | Task 4 |

**Placeholder scan:** No "TBD", "TODO", or vague-requirement lines in any task. All code blocks contain the actual code. All test commands have expected outputs. Smoke procedure is concrete.

**Type consistency:**

- `Tier`, `Allow`, `Deny`, `ConnectorEvent` defined in Task 3, used unchanged in Tasks 4, 5, 6, 9, 10.
- `Connector` Protocol from Task 3 implemented by `FakeConnector` (Task 5) and `GitHubConnector` (Task 10). `rule_id_for` is an optional method on the Protocol's structural surface (Router uses `getattr` with fallback).
- `Router.__init__` signature evolves across Tasks 6, 7, 8 — each task shows the full revised `__init__` (not a delta), so a subagent reading any single task gets the complete current shape.
- `BusPublish = Callable[..., None]` — kept as `...` rather than `Callable[..., None]` with named kwargs because Python's typing doesn't enforce keyword-only signatures on `Callable`. The fake and the daemon adapter both use the same kwarg names.
- `build_funnel_app(router, webhook_secret, target_being)` signature matches the endpoint wrapper's usage in Task 13.

---

**Plan complete.** Save location: `docs/superpowers/plans/2026-06-20-inbound-notifications-v1a-implementation.md` (this file).
