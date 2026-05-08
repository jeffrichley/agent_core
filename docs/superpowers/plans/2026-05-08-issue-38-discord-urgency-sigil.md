# Issue #38 — Discord urgency sigil-prefix replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Discord adapter's regex-based urgency detection (`(?i)\b(urgent|now|stop)\b`) with a two-character sigil prefix (`!` red, `?` yellow). Drop the regex entirely.

**Architecture:** A single-purpose `parse_sigil()` helper in a new `sigil.py` module returns `(urgency, stripped_text)`. The Discord endpoint replaces the existing regex code path with one call to this helper and uses the stripped text in `TextMessagePayload`. The `urgency_red_regex` field is deleted from `AccessConfig`; existing access JSON files containing `urgencyRedRegex` silently ignore the key.

**Tech Stack:** Python 3.12, asyncio, pytest, ruff. Branch: `fix/issue-38-discord-urgency-sigil`.

**Test command:** `uv run pytest packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests` (the bare `uv run pytest` hits a pre-existing multi-package conftest plugin-name collision between `agent-core-discord` and `agent-core-webcam` — drop webcam from the path; this targeted command runs 769+ tests with no collision).

**Spec:** `docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md`

---

## Task 1: `parse_sigil()` helper + unit tests

**Files:**
- Create: `packages/agent-core-discord/src/agent_core_discord/sigil.py`
- Create: `packages/agent-core-discord/tests/test_sigil.py`

### Steps

- [ ] **Step 1: Write the failing parser tests**

Create `packages/agent-core-discord/tests/test_sigil.py`:

```python
"""Unit tests for the sigil-prefix urgency parser.

The parser must:
- Map "!" to red, "?" to yellow, anything else to green.
- Strip the sigil and at most one trailing space from the published text.
- Tolerate leading whitespace before the sigil.
- Treat sigils only at position 0 (after lstrip); mid-message sigils are text.
- Consume at most one sigil; "!!" leaves a literal "!" in the payload.
"""

from __future__ import annotations

import pytest

from agent_core_discord.sigil import parse_sigil


@pytest.mark.parametrize(
    "content, expected",
    [
        # Simple cases
        ("!hello", ("red", "hello")),
        ("?hello", ("yellow", "hello")),
        ("hello", ("green", "hello")),
        # Leading whitespace tolerated
        ("  !hello", ("red", "hello")),
        ("\n!hello", ("red", "hello")),
        ("\t?status", ("yellow", "status")),
        ("  hello", ("green", "  hello")),  # green messages preserve leading whitespace
        # Optional single space after sigil
        ("! hello", ("red", "hello")),
        ("? hello", ("yellow", "hello")),
        # Only ONE space is eaten — second space preserved
        ("!  hello", ("red", " hello")),
        # Multi-char sigil — only the first is consumed
        ("!!hello", ("red", "!hello")),
        ("??hello", ("yellow", "?hello")),
        # First sigil wins; second becomes literal text
        ("!?hello", ("red", "?hello")),
        ("?!hello", ("yellow", "!hello")),
        # Mid-message sigil is not interpreted
        ("hello !world", ("green", "hello !world")),
        ("hello ?world", ("green", "hello ?world")),
        # Edge cases
        ("", ("green", "")),
        ("   ", ("green", "   ")),  # whitespace-only preserved verbatim
        ("!", ("red", "")),  # sigil alone, empty text
        ("?", ("yellow", "")),
        # Sigil + Discord-internal markup preserved
        ("!<@123> help", ("red", "<@123> help")),
        ("?:fire: status", ("yellow", ":fire: status")),
        ("!**bold**", ("red", "**bold**")),
    ],
)
def test_parse_sigil(content: str, expected: tuple[str, str]) -> None:
    assert parse_sigil(content) == expected
```

- [ ] **Step 2: Run the failing tests**

Run: `uv run pytest packages/agent-core-discord/tests/test_sigil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core_discord.sigil'`.

- [ ] **Step 3: Implement the parser**

Create `packages/agent-core-discord/src/agent_core_discord/sigil.py`:

```python
"""Sigil-prefix urgency parser for Discord inbound messages.

Two sigils:
- "!" promotes to red urgency
- "?" promotes to yellow urgency
- Anything else stays green (default)

The sigil character is stripped from the published payload along with any
leading whitespace and at most one space after the sigil. See issue #38
and docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md.
"""

from __future__ import annotations

from typing import Literal

Urgency = Literal["red", "yellow", "green"]

_SIGIL_TO_URGENCY: dict[str, Urgency] = {"!": "red", "?": "yellow"}


def parse_sigil(content: str) -> tuple[Urgency, str]:
    """Parse a sigil-prefixed message into (urgency, stripped_text).

    Returns ("green", content) unchanged when no sigil is present so callers
    that expect the original text on the green path see no surprises.

    When a sigil matches, returns the elevated urgency and the text with
    leading whitespace + the sigil + at most one trailing space removed.
    """
    stripped = content.lstrip()
    if not stripped:
        return "green", content
    sigil = stripped[0]
    if sigil not in _SIGIL_TO_URGENCY:
        return "green", content
    rest = stripped[1:]
    if rest.startswith(" "):
        rest = rest[1:]
    return _SIGIL_TO_URGENCY[sigil], rest
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/agent-core-discord/tests/test_sigil.py -v`
Expected: PASS for all parametrize cases (~24 cases).

- [ ] **Step 5: Run the in-scope suite to confirm no regressions**

Run: `uv run pytest packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests`
Expected: All previously-passing tests still pass (no source change to other paths yet — the regex-based tests in `test_endpoint_urgency.py` still pass because the endpoint is still using the regex). New tests pass on top.

- [ ] **Step 6: Self-review**

Look at the diff. Confirm:
- Two files added: `sigil.py` (source) and `test_sigil.py` (tests).
- No other files modified.
- `parse_sigil` returns the SAME exact tuple shape on every code path: `(Urgency, str)`.
- No leftover `print` statements or commented-out code.

- [ ] **Step 7: Commit**

```bash
git checkout -b fix/issue-38-discord-urgency-sigil
git add packages/agent-core-discord/src/agent_core_discord/sigil.py \
        packages/agent-core-discord/tests/test_sigil.py
git commit -m "feat(discord/sigil): add parse_sigil helper for prefix-based urgency (#38)

Two-sigil parser: '!' -> red, '?' -> yellow, anything else -> green.
Strips sigil + at most one trailing space from the published text.
Tolerates leading whitespace; only the first sigil is consumed.

Pure helper with no Discord dependency — testable as a unit. Endpoint
wiring and AccessConfig cleanup follow in subsequent tasks."
```

---

## Task 2: Wire parser into endpoint + update endpoint urgency tests

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (urgency block at lines ~775-790; payload construction at line ~810)
- Modify: `packages/agent-core-discord/tests/test_endpoint_urgency.py` (rewrite around sigil)

### Steps

- [ ] **Step 1: Replace the regex code path in `endpoint.py`**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, find the urgency block (currently around lines 775-790):

```python
# 6. Build and publish the envelope.
#    Apply the urgency-red regex rule. Empty string disables.
#    Per-message compile is fine for v1 throughput; a future v2
#    can pre-compile in start() if profiling shows it matters.
urgency: Any = "green"
regex = self._access.urgency_red_regex
if regex:
    try:
        if re.search(regex, message.content or ""):
            urgency = "red"
    except re.error:
        log.warning(
            "discord(%s): invalid urgency_red_regex %r — skipping",
            self.name,
            regex,
        )
```

Replace with:

```python
# 6. Build and publish the envelope.
#    Sigil-prefix urgency: '!' -> red, '?' -> yellow, plain -> green.
#    The sigil is stripped from the payload text. See issue #38.
urgency, text = parse_sigil(message.content or "")
```

Find the `TextMessagePayload(text=message.content or "")` line (currently line ~810):

```python
env = Envelope(
    id=uuid.uuid4().hex,
    correlation_id=uuid.uuid4().hex,
    to=self.target,
    kind="TextMessage",
    payload=TextMessagePayload(text=message.content or ""),
    metadata=metadata,
    urgency=urgency,
    created_at=datetime.now(UTC),
)
```

Change `payload=TextMessagePayload(text=message.content or "")` to `payload=TextMessagePayload(text=text)` so the published payload uses the sigil-stripped text.

Add the import at the top of the file (group with other `from agent_core_discord.*` imports):

```python
from agent_core_discord.sigil import parse_sigil
```

**Note for the implementer:** do NOT remove `import re` from the imports. `re` is still used elsewhere in this file (e.g., `_FILENAME_ALLOWED = re.compile(...)` around line 102). Confirm with `grep -n "re\." packages/agent-core-discord/src/agent_core_discord/endpoint.py` before deciding.

- [ ] **Step 2: Rewrite `test_endpoint_urgency.py`**

The existing 6 tests assert on regex behavior. Most need replacing. The Lancaster regression case is the new flagship.

Replace the contents of `packages/agent-core-discord/tests/test_endpoint_urgency.py` (keeping the file's existing setup helpers — `_Recording`, `_start`, `_msg`):

```python
"""DiscordEndpoint applies sigil-prefix urgency rule on inbound TextMessage envelopes.

Sigil rules: "!" -> red, "?" -> yellow, anything else -> green. See
docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md.
"""

from __future__ import annotations

import pytest
from agent_core_discord.endpoint import DiscordEndpoint

from agent_core.bus.envelope import EndpointInfo, Envelope
from tests.conftest import _FakeChannel, _FakeDiscordClient, _FakeMessage, _FakeUser


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...
    def endpoints(self) -> list[EndpointInfo]:
        return []


async def _start(
    monkeypatch, access_path=None
) -> tuple[DiscordEndpoint, _Recording, _FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = _FakeDiscordClient()
    ep = DiscordEndpoint(
        name="d",
        target="agent",
        token_env="X_TOK",
        access_config_path=access_path,
        _client_factory=lambda **kw: fake,
    )
    await ep.start(handle)
    return ep, handle, fake


def _msg(content: str) -> _FakeMessage:
    msg = _FakeMessage(id="m1", channel_id="200", content=content)
    msg.author = _FakeUser(id="100", name="user", display_name="User")
    msg.guild = type("G", (), {"id": "guild-1"})()
    msg.channel = _FakeChannel(id="200")
    msg.attachments = []
    return msg


@pytest.mark.asyncio
async def test_inbound_default_urgency_is_green(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("hello world")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
        assert handle.published[0].payload.text == "hello world"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_red_sigil_promotes_to_red_and_strips_prefix(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("!server is on fire")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
        # The sigil and the optional space after it are stripped from the payload.
        assert handle.published[0].payload.text == "server is on fire"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_yellow_sigil_promotes_to_yellow_and_strips_prefix(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("?status check")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "yellow"
        assert handle.published[0].payload.text == "status check"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_lancaster_regression_now_in_natural_text_is_not_red(monkeypatch):
    """Issue #38 regression: 'right now we are looking at...' must not fire red.

    The original regex (urgent|now|stop) matched on the substring 'now'. The
    sigil-only rule has no such failure mode — 'right now' is plain text,
    no leading sigil, urgency stays green.
    """
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("ok, more on the ideas for the lancaster trip. right now we are looking at...")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
        # Payload text is unchanged for green messages — no sigil to strip.
        assert handle.published[0].payload.text.startswith("ok, more on the ideas")
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_urgent_keyword_in_plain_text_is_not_red(monkeypatch):
    """Plain 'URGENT' without a sigil is now green — no regex on free text."""
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("URGENT please look at this")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "green"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_red_sigil_with_leading_whitespace(monkeypatch):
    ep, handle, fake = await _start(monkeypatch)
    fake.add_channel(_FakeChannel(id="200"))
    msg = _msg("  !page on-call now")
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        assert handle.published[0].urgency == "red"
        assert handle.published[0].payload.text == "page on-call now"
    finally:
        await ep.stop()
```

- [ ] **Step 3: Run the targeted endpoint tests**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_urgency.py -v`
Expected: PASS for all 6 tests.

- [ ] **Step 4: Run the in-scope test suite**

Run: `uv run pytest packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests`
Expected: PASS for nearly everything. May FAIL on tests in `test_access.py` if any reference `urgency_red_regex`, but `test_access.py` shouldn't have those (verified — current `test_access.py` only tests dm_policy / allow_from / channels / ack_reaction). If a test in this file references `urgency_red_regex`, that's a clean failure to address in Task 3. **Otherwise, expect all green.**

If the suite is green, proceed to Step 5. If not, the implementer should report DONE_WITH_CONCERNS listing the failing tests so the orchestrator can decide.

- [ ] **Step 5: Self-review**

`git diff` — confirm:
- `endpoint.py` has the regex block replaced with one `parse_sigil` call; payload uses `text` variable.
- `import re` is preserved (still used elsewhere in the file).
- `from agent_core_discord.sigil import parse_sigil` is added in the import block.
- `test_endpoint_urgency.py` no longer has the regex tests; new tests cover the sigil cases including the Lancaster regression.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py \
        packages/agent-core-discord/tests/test_endpoint_urgency.py
git commit -m "feat(discord): wire sigil parser into inbound TextMessage path (#38)

DiscordEndpoint now uses parse_sigil() for urgency promotion instead
of the urgency_red_regex pattern. The sigil character is stripped from
the published TextMessagePayload.text so agents see the message
without the prefix.

Test rewrites preserve fire/timing/auth coverage, drop the regex-era
keyword tests (URGENT/now/stop in free text are now green), and add
the Lancaster regression test that fixes the 2026-05-06 false positive.

AccessConfig.urgency_red_regex is still on the dataclass at this point
but unused; Task 3 deletes it."
```

---

## Task 3: Delete `urgency_red_regex` from AccessConfig + migration robustness

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/access.py` (delete field at line ~34 + JSON load at line ~75)
- Modify: `packages/agent-core-discord/tests/test_access.py` (add migration robustness test)

### Steps

- [ ] **Step 1: Delete the field from `AccessConfig`**

In `packages/agent-core-discord/src/agent_core_discord/access.py`, find the field declaration (currently around line 30-34):

```python
@dataclass
class AccessConfig:
    """Validated access policy for a single Discord bot."""

    dm_policy: DmPolicy = "open"
    allow_from: list[str] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    ack_reaction: str = "👀"
    # Regex applied to inbound message content. A match promotes the
    # published TextMessage envelope's urgency to "red". Empty string
    # disables the rule entirely. Operator-overridable via access JSON
    # field `urgencyRedRegex`.
    urgency_red_regex: str = r"(?i)\b(urgent|now|stop)\b"
```

Delete the comment block + the `urgency_red_regex` line. Result:

```python
@dataclass
class AccessConfig:
    """Validated access policy for a single Discord bot."""

    dm_policy: DmPolicy = "open"
    allow_from: list[str] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    ack_reaction: str = "👀"
```

- [ ] **Step 2: Delete the JSON load assignment**

In the same file, find `load_access_config` (around lines 47-76). Locate the `urgency_red_regex` line in the return:

```python
    return AccessConfig(
        dm_policy=dm_policy,  # type: ignore[arg-type]
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
        urgency_red_regex=raw.get("urgencyRedRegex", r"(?i)\b(urgent|now|stop)\b"),
    )
```

Delete the `urgency_red_regex=raw.get(...)` line:

```python
    return AccessConfig(
        dm_policy=dm_policy,  # type: ignore[arg-type]
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
    )
```

The `urgencyRedRegex` JSON key is now silently ignored on load — same fate as any other unrecognized key.

- [ ] **Step 3: Add migration robustness test**

In `packages/agent-core-discord/tests/test_access.py`, append a test that an access JSON with `urgencyRedRegex` set still loads cleanly:

```python
def test_load_access_config_silently_ignores_legacy_urgency_red_regex(tmp_path):
    """Migration: existing access JSON files with urgencyRedRegex set must
    still load cleanly under the post-#38 AccessConfig (which doesn't have
    the field). The key is silently ignored — no warning, no error.
    """
    p = tmp_path / "access.json"
    p.write_text(
        json.dumps(
            {
                "dmPolicy": "open",
                "ackReaction": "👀",
                "urgencyRedRegex": r"(?i)\b(urgent|now|stop)\b",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_access_config(p)
    assert cfg.dm_policy == "open"
    assert cfg.ack_reaction == "👀"
    assert not hasattr(cfg, "urgency_red_regex")
```

- [ ] **Step 4: Run access tests**

Run: `uv run pytest packages/agent-core-discord/tests/test_access.py -v`
Expected: PASS for all (existing tests + the new migration test).

- [ ] **Step 5: Run the in-scope suite**

Run: `uv run pytest packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests`
Expected: PASS for everything.

- [ ] **Step 6: Self-review**

`git diff` — confirm:
- `access.py` no longer has any reference to `urgency_red_regex` (field deleted, comment block deleted, JSON key removed from `load_access_config`).
- `test_access.py` has the new `test_load_access_config_silently_ignores_legacy_urgency_red_regex` test.
- No other files modified.
- `grep -n "urgency_red_regex\|urgencyRedRegex" packages/agent-core-discord/` returns zero matches in source files (test_access.py will still mention the JSON key inside the test fixture — that's expected).

- [ ] **Step 7: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/access.py \
        packages/agent-core-discord/tests/test_access.py
git commit -m "fix(discord/access): delete urgency_red_regex field (#38)

The field is no longer wired into the endpoint (replaced by
parse_sigil in the previous commit). Existing access JSON files with
urgencyRedRegex set silently ignore the key on next daemon start —
same fate as any other unrecognized JSON field. New migration test
asserts the load path stays clean under that scenario.

Closes the false-positive surface that triggered #38 (the regex code
path is now entirely absent from the binary)."
```

---

## Task 4: Final verification + PR

**Files:** none modified.

### Steps

- [ ] **Step 1: Full in-scope test suite**

Run: `uv run pytest packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests`
Expected: All tests pass. New count = pre-branch baseline + ~24 new sigil unit tests + ~6 new endpoint urgency tests + 1 new migration test = ~31 new passing.

- [ ] **Step 2: Lint**

Run: `uv run ruff check packages/agent-core-discord/src packages/agent-core-discord/tests`
Expected: clean. (Don't run `ruff check .` — the 19 pre-existing errors in `memory-compiler/` are unrelated.)

- [ ] **Step 3: Type-check (if mypy is configured)**

Run: `uv run mypy packages/agent-core-discord/src/agent_core_discord/sigil.py packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/src/agent_core_discord/access.py`
Expected: clean.

If mypy isn't configured for `agent-core-discord` (the root `pyproject.toml`'s `[tool.mypy]` lists `packages/core/src` and `packages/agent-core-channel/src` only), skip this step and surface the gap in the PR body as a note.

- [ ] **Step 4: Verify branch state**

Run: `git log --oneline main..HEAD`
Expected: 3 commits on `fix/issue-38-discord-urgency-sigil` (one per Task 1-3).

- [ ] **Step 5: Search for stale references**

Use the Grep tool: `urgency_red_regex` and `urgencyRedRegex` across the whole repo. The only remaining matches should be in:
- `docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md` (the spec — historical reference).
- `docs/superpowers/plans/2026-05-08-issue-38-discord-urgency-sigil.md` (this plan).
- Any pre-existing docs that reference the old regex (look at `docs/cutover/*` and `docs/HANDOFF-*`).

If non-doc source matches exist (e.g., a forgotten import or test still referencing the field), fix them in a follow-up commit and re-run the test suite.

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin fix/issue-38-discord-urgency-sigil
gh pr create --title "fix: Discord urgency sigil-prefix replacement (#38)" --body "$(cat <<'EOF'
## Summary

Closes #38. Replaces the `urgency_red_regex` regex with sigil-prefix urgency detection.

- **Two sigils:** `!` -> red, `?` -> yellow, plain message -> green.
- **Stripped from payload:** the sigil character (and at most one trailing space) is removed before publishing the envelope, so agents see `server is on fire` instead of `!server is on fire`.
- **Regex deleted, not soft-faded:** the `urgency_red_regex` field is removed from `AccessConfig` entirely. Existing access JSON files with `urgencyRedRegex` set silently ignore the key — no warning, no migration script needed.
- **Lancaster regression closed:** the original false positive (`right now we are looking at...` -> red because of "now") now produces green by construction. Test added.

Out of scope (deferred to future issue): sender-map default urgency, channel-of-origin defaults, embedding-similarity, the `~` explicit-green sigil, multi-locale, cross-platform parity. Spec at `docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md`.

## Test plan

- [x] `uv run pytest packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests` — green
- [x] `uv run ruff check packages/agent-core-discord/src packages/agent-core-discord/tests` — clean
- [ ] Validate on testbot: send `!ping`, `?ping`, `ping`, `right now we are looking at...`. Confirm urgency is red / yellow / green / green respectively, and that the payload text doesn't include the sigil.
- [ ] Then restart Pepper to pick up the new contract.

## New tests

- **Sigil parser unit tests** (`tests/test_sigil.py`) — parametrized over ~24 cases covering position, whitespace, multi-char, and edge inputs.
- **Endpoint urgency tests** (`tests/test_endpoint_urgency.py`) — rewritten around the sigil; preserves fire/payload coverage, adds Lancaster regression and "URGENT in plain text is no longer red" tests.
- **Migration robustness** (`tests/test_access.py`) — asserts a legacy JSON config with `urgencyRedRegex` set loads cleanly under the post-#38 `AccessConfig`.
EOF
)"
```

Expected: PR opens cleanly. Return the PR URL.

---

## Self-review checklist (run by orchestrator after plan-write)

- [x] Spec coverage: Task 1 covers parser + unit tests; Task 2 covers endpoint wiring + endpoint tests + Lancaster regression; Task 3 covers AccessConfig cleanup + migration test; Task 4 covers verification + PR.
- [x] Placeholder scan: no TBD/TODO/"add appropriate"/etc. All commands and code blocks concrete.
- [x] Type consistency: `parse_sigil` signature `(content: str) -> tuple[Urgency, str]` used consistently across Tasks 1-2; `Urgency = Literal["red", "yellow", "green"]` defined in `sigil.py` and reused everywhere.
- [x] Conventional commits: `feat(discord/sigil):`, `feat(discord):`, `fix(discord/access):`. Per-repo style verified.
- [x] Branch + PR convention: `fix/issue-38-discord-urgency-sigil`; `Closes #38` will auto-close via the PR title `(#38)`.
- [x] Test command pinned: `packages/core/tests packages/agent-core-channel/tests packages/agent-core-discord/tests` (drops webcam to sidestep the conftest collision).
