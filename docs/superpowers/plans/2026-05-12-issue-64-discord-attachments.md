# Issue #64 — Wire discord-pepper file-attachment path (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing `TextMessagePayload.attachments` schema field through `_deliver_text_message` to discord.py's file-upload path, with tight per-element Pydantic validation that fails synchronously at publish time.

**Architecture:** Bus-side `FileAttachment(path: str, extra='allow')` model on `TextMessagePayload`. Translation at the bus↔verb boundary in `_deliver_text_message` (single line: `files = [a.path for a in payload.attachments]`, passed to existing `_SendArgs.files: list[str]`). `_send` (`endpoint.py:1203–1221`) is unchanged. `FakeMessage` extended to model attachment roundtrip for testable assertions.

**Tech Stack:** Python 3.12+, Pydantic v2 (`BaseModel`, `Field`, `ConfigDict`), `pytest`/`pytest-asyncio`, `discord.py` 2.x. Use `uv run --no-sync pytest` (not bare pytest). Conventional commit style, no `Co-Authored-By` trailer.

**Branch:** `feat/issue-64-discord-attachments` (already created off main, spec committed at `f347664`).

---

## Phase 1 — Schema (bus envelope)

### Task 1: Add `FileAttachment` model with required path + extra-allow

**Files:**
- Modify: `packages/core/src/agent_core/bus/envelope.py` (header imports, add class after `AcknowledgmentPayload` or before `TextMessagePayload` — keep it together with `TextMessagePayload`)
- Create: `packages/core/tests/bus/test_file_attachment_schema.py`

- [ ] **Step 1: Write the failing tests for FileAttachment alone (model not yet wired to TextMessagePayload)**

```python
"""Tests for FileAttachment Pydantic model (#64)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_core.bus.envelope import FileAttachment


def test_file_attachment_requires_path():
    """Missing `path` raises ValidationError at publish time."""
    with pytest.raises(ValidationError):
        FileAttachment()


def test_file_attachment_rejects_empty_string_path():
    """Empty `path` rejected by Field(min_length=1)."""
    with pytest.raises(ValidationError):
        FileAttachment(path="")


def test_file_attachment_allows_extra_fields():
    """extra='allow' lets aspirational fields land without schema migration."""
    attachment = FileAttachment(path="/abs/file.pdf", filename="renamed.pdf")
    assert attachment.path == "/abs/file.pdf"
    # filename available via model_extra (Pydantic v2 extra-allow mechanism)
    assert attachment.model_extra is not None
    assert attachment.model_extra.get("filename") == "renamed.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest packages/core/tests/bus/test_file_attachment_schema.py -v`

Expected: 3 tests collected, all FAIL with `ImportError` (FileAttachment doesn't exist yet).

- [ ] **Step 3: Add `FileAttachment` to envelope.py**

In `packages/core/src/agent_core/bus/envelope.py`, locate the existing import block (lines 11-14). It already imports `BaseModel, ConfigDict, Field, model_validator` from `pydantic`. Good — no import changes needed.

Add `FileAttachment` right above `TextMessagePayload` (before line 17):

```python
class FileAttachment(BaseModel):
    """File attachment on a TextMessage envelope.

    `path` is the local filesystem path discord-pepper reads via
    `discord.File(path)`. Validation runs at envelope publish time so
    typos / missing keys surface synchronously at the publishing
    agent's send() call, not as a later yellow Ack from the adapter.

    `extra='allow'` permits aspirational fields (filename override,
    description, spoiler) to pass validation; the adapter currently
    only consumes `path`. New fields wire to the adapter incrementally,
    named-symptom-bound.
    """

    path: str = Field(min_length=1)
    model_config = ConfigDict(extra="allow")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/bus/test_file_attachment_schema.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus/envelope.py packages/core/tests/bus/test_file_attachment_schema.py
git commit -m "feat(envelope): FileAttachment model with required path (#64)"
```

---

### Task 2: Wire `FileAttachment` into `TextMessagePayload.attachments`

**Files:**
- Modify: `packages/core/src/agent_core/bus/envelope.py:17–20`
- Modify: `packages/core/tests/bus/test_file_attachment_schema.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/bus/test_file_attachment_schema.py`:

```python
from agent_core.bus.envelope import TextMessagePayload


def test_text_message_payload_defaults_attachments_empty():
    """Backward compat: existing publishes without `attachments` still validate."""
    payload = TextMessagePayload(text="hi")
    assert payload.attachments == []


def test_text_message_payload_typo_in_attachment_key_raises_at_publish():
    """Named-symptom regression lock: a typo in the attachment dict key
    surfaces at envelope-construction time, synchronously to the agent's
    publish call, not as a later yellow Ack.
    """
    with pytest.raises(ValidationError):
        TextMessagePayload(
            text="hi",
            attachments=[{"paht": "/abs/file.pdf"}],  # typo: missing 'path'
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest packages/core/tests/bus/test_file_attachment_schema.py -v`

Expected: 5 collected, 3 pass (from Task 1), `test_text_message_payload_typo_in_attachment_key_raises_at_publish` FAILS because `attachments: list[dict[str, Any]]` accepts any dict; `test_text_message_payload_defaults_attachments_empty` PASSES (preexisting field already defaults to empty list).

- [ ] **Step 3: Tighten the `TextMessagePayload.attachments` annotation**

In `packages/core/src/agent_core/bus/envelope.py` line 20, change:

```python
class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
```

to:

```python
class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list[FileAttachment] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest packages/core/tests/bus/test_file_attachment_schema.py -v`

Expected: 5 passed.

Also run the broader core bus test suite to confirm no regression in other envelope tests:

Run: `uv run --no-sync pytest packages/core/tests/bus -q`

Expected: all green (the schema change should be backward-compatible for empty-attachments publishes and forward-compatible for properly-shaped attachments).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent_core/bus/envelope.py packages/core/tests/bus/test_file_attachment_schema.py
git commit -m "feat(envelope): tighten TextMessagePayload.attachments to list[FileAttachment] (#64)"
```

---

## Phase 2 — Fake test infrastructure

### Task 3: Extend `FakeMessage` and `FakeChannel.send` to model attachment roundtrip

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py:93–193`
- Create: `packages/agent-core-discord/tests/test_fakes_attachments.py`

- [ ] **Step 1: Write the failing fake-sanity test**

```python
"""Sanity test for FakeMessage/FakeChannel attachment roundtrip (#64)."""

from __future__ import annotations

import discord
import pytest

from agent_core_discord.testing.fakes import FakeChannel


@pytest.mark.asyncio
async def test_fake_message_records_attachments_from_send_call(tmp_path):
    """FakeChannel.send(files=[...]) returns a FakeMessage whose
    .attachments list reflects the inputs with .filename derived from
    each discord.File's filename attribute.

    Without this fake-side modeling, handler-level tests in Phase 3
    are toothless — they can't assert what reached Discord.
    """
    ch = FakeChannel(id="100")
    # Two real files on disk (tmp_path so they auto-clean).
    f1 = tmp_path / "alpha.pdf"
    f1.write_bytes(b"a" * 16)
    f2 = tmp_path / "beta.pdf"
    f2.write_bytes(b"b" * 16)
    discord_files = [discord.File(str(f1)), discord.File(str(f2))]
    try:
        msg = await ch.send(content="hi", files=discord_files)
        assert len(msg.attachments) == 2
        assert [a.filename for a in msg.attachments] == ["alpha.pdf", "beta.pdf"]
    finally:
        for df in discord_files:
            df.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_fakes_attachments.py -v`

Expected: FAIL — `FakeMessage` has no `attachments` attribute.

- [ ] **Step 3: Extend `FakeMessage` and `FakeChannel.send`**

In `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py`, add a small `FakeAttachment` class above `FakeMessage` (around line 92):

```python
class FakeAttachment:
    """Minimal stand-in for discord.Attachment on a fake message.

    Models only the fields tests assert on (`filename`, `url`); expand
    named per the test-fakes-mirror-real-strictly discipline.
    """

    def __init__(self, *, filename: str, url: str = ""):
        self.filename = filename
        self.url = url
```

Extend `FakeMessage.__init__` (line 93–105) to accept and store an `attachments` parameter:

```python
class FakeMessage:
    def __init__(
        self,
        *,
        id: str,
        channel_id: str,
        content: str = "",
        author=None,
        poll: FakePoll | None = None,
        attachments: list[FakeAttachment] | None = None,
    ):
        self.id = id
        self.channel_id = channel_id
        self.content = content
        # ... preserve existing init body ...
        self.attachments = attachments or []
```

Make sure to preserve the rest of `FakeMessage.__init__` unchanged — only add the new parameter and the `self.attachments = ...` line. If the existing init does more, keep it intact.

Then extend `FakeChannel.send` (line 171–193) to populate `attachments` on the returned `FakeMessage`:

```python
async def send(
    self,
    content: str | None = None,
    *,
    embeds: list | None = None,
    reference: Any = None,
    files: list | None = None,
    poll: Any = None,
) -> FakeMessage:
    new_id = f"new-{len(self.sent) + 1}"
    attachments: list[FakeAttachment] = []
    for f in files or []:
        # discord.File.filename is the resolved upload filename
        # (basename of the path when not overridden).
        fn = getattr(f, "filename", "")
        attachments.append(FakeAttachment(filename=fn))
    msg = FakeMessage(
        id=new_id,
        channel_id=self.id,
        content=content or "",
        attachments=attachments,
    )
    self._messages[new_id] = msg
    self.sent.append(
        {
            "content": content,
            "embeds": embeds,
            "reference": reference,
            "files": files,
            "poll": poll,
            "message_id": new_id,
        }
    )
    return msg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_fakes_attachments.py -v`

Expected: PASS.

Run the broader discord test suite to confirm no regression in any existing test that uses `FakeMessage` / `FakeChannel`:

Run: `uv run --no-sync pytest packages/agent-core-discord/tests -x -q`

Expected: all green. If any test fails because it was passing a positional argument to `FakeMessage` or relying on missing-`attachments` behavior, adapt those tests minimally to the new signature.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/testing/fakes.py packages/agent-core-discord/tests/test_fakes_attachments.py
git commit -m "feat(testing): model attachment roundtrip in FakeMessage/FakeChannel (#64)"
```

---

## Phase 3 — Handler wire-up

### Task 4: Wire `payload.attachments` → `_SendArgs.files` in `_deliver_text_message`

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py:691–696`
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append the single-attachment happy-path test)

- [ ] **Step 1: Write the failing happy-path test**

Append to `packages/agent-core-discord/tests/test_endpoint_outbound.py`:

```python
@pytest.mark.asyncio
async def test_text_message_with_single_attachment_uploads_to_discord(monkeypatch, tmp_path):
    """Issue #64 named-symptom regression lock: payload.attachments=[{path: ...}]
    reaches Discord via channel.send(files=[discord.File(...)]).
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    pdf = tmp_path / "briefing.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    from agent_core.bus.envelope import Envelope, TextMessagePayload, FileAttachment
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-attach", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="briefing attached",
                attachments=[FileAttachment(path=str(pdf))],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        # Outbound landed.
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "briefing attached"
        # files arg was populated on the channel.send call.
        sent_files = ch.sent[0]["files"]
        assert sent_files is not None and len(sent_files) == 1
        # FakeMessage exposes the attachment with basename filename.
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert len(msg.attachments) == 1
        assert msg.attachments[0].filename == "briefing.pdf"
        # No error ack published.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert all(not a.payload.note.lower().startswith("error:") for a in acks)
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_message_with_single_attachment_uploads_to_discord -v`

Expected: FAIL — `_deliver_text_message` doesn't read `payload.attachments`, so `ch.sent[0]["files"]` is `None`.

- [ ] **Step 3: Add the wire-up to `_deliver_text_message`**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, locate the `_SendArgs(...)` construction at lines 691–696:

```python
        args = _SendArgs(
            channel_id=str(channel_id),
            text=text_for_send,
            embeds=embeds_data,
            reply_to=reply_to,
        )
        return await self._send(args)
```

Add the `files` derivation just above the `_SendArgs(...)` line (after the existing `text_for_send` resolution at line 687–689):

```python
        # Translate bus-side FileAttachment list to verb-side files list.
        # Tight FileAttachment validation already ran at envelope publish
        # time, so payload.attachments is a list of validated models.
        files = [a.path for a in envelope.payload.attachments] or None

        args = _SendArgs(
            channel_id=str(channel_id),
            text=text_for_send,
            embeds=embeds_data,
            reply_to=reply_to,
            files=files,
        )
        return await self._send(args)
```

The `or None` keeps `_SendArgs.files` as `None` when no attachments are present — matches the existing signature default and avoids passing an empty list into the existing chunking logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_message_with_single_attachment_uploads_to_discord -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "feat(discord): wire payload.attachments through _deliver_text_message (#64)"
```

---

### Task 5: Multi-attachment with order preservation

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_text_message_with_multiple_attachments_uploads_all(monkeypatch, tmp_path):
    """Multi-file delivery preserves input order on the way to Discord."""
    import os

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    paths = []
    for name in ("alpha.pdf", "beta.pdf", "gamma.pdf"):
        p = tmp_path / name
        p.write_bytes(b"data")
        paths.append(str(p))
    from agent_core.bus.envelope import Envelope, TextMessagePayload, FileAttachment
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-multi", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="three files",
                attachments=[FileAttachment(path=p) for p in paths],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert len(msg.attachments) == 3
        # Order preserved end-to-end.
        assert [a.filename for a in msg.attachments] == [
            os.path.basename(p) for p in paths
        ]
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes (should be green-first via Task 4's wire-up)**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_message_with_multiple_attachments_uploads_all -v`

Expected: PASS green-first. The wire-up uses a list comprehension that preserves order; the fake stores in the order received.

If it fails, the wire-up has an ordering bug or the fake is not preserving order — fix before commit.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): lock multi-attachment ordering invariant (#64)"
```

---

### Task 6: Basename filename derivation lock

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_text_message_attachment_uses_basename_as_filename(monkeypatch, tmp_path):
    """discord.File(path) defaults filename to os.path.basename(path).
    Lock this default so a future refactor that strips file extension or
    rewrites the filename is loud, not silent.
    """
    import os

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    deep_path = tmp_path / "deeply" / "nested" / "weird name.pdf"
    deep_path.parent.mkdir(parents=True)
    deep_path.write_bytes(b"x")
    from agent_core.bus.envelope import Envelope, TextMessagePayload, FileAttachment
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-basename", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="basename check",
                attachments=[FileAttachment(path=str(deep_path))],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert msg.attachments[0].filename == os.path.basename(str(deep_path))
        assert msg.attachments[0].filename == "weird name.pdf"
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_message_attachment_uses_basename_as_filename -v`

Expected: PASS green-first.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): lock discord.File basename-as-filename default (#64)"
```

---

### Task 7: File-not-found yields yellow Ack error

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_text_message_attachment_file_not_found_yields_yellow_ack_error(monkeypatch):
    """Nonexistent path → discord.File raises FileNotFoundError →
    existing _send exception path catches → yellow Ack with note prefix
    'error:'. No Discord message published.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    from agent_core.bus.envelope import Envelope, TextMessagePayload, FileAttachment
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-missing", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="missing file",
                attachments=[FileAttachment(path="/this/path/does/not/exist.pdf")],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        # No Discord message published (the send never happened).
        assert len(ch.sent) == 0
        # Yellow Ack with error prefix.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert len(acks) == 1
        assert acks[0].urgency == "yellow"
        assert acks[0].payload.note.lower().startswith("error:")
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_message_attachment_file_not_found_yields_yellow_ack_error -v`

Expected: PASS green-first. The existing `_send` already catches construction errors on `discord.File(path)` and the broader `_deliver_text_message` error path at `endpoint.py:630–634` produces the yellow Ack.

If it fails, inspect the actual exception type raised — `discord.File` may raise something other than `FileNotFoundError` depending on the discord.py version. Either way, the test's assertion is on the yellow-Ack shape, not the exception type — so adapt the test only if the assertion needs adjustment.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): lock file-not-found yellow-Ack path for attachments (#64)"
```

---

### Task 8: Too-large file yields yellow Ack error (mock HTTPException)

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_text_message_attachment_too_large_yields_yellow_ack_error(monkeypatch, tmp_path):
    """Discord's 25 MB cap surfaces as discord.HTTPException from
    channel.send. Existing exception path routes to yellow Ack.
    Locks the routing for a documented mode (Section 2 error table row).
    """
    import discord

    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)

    # Patch ch.send to raise HTTPException as if Discord rejected the upload.
    async def _raise_too_large(*args, **kwargs):
        # discord.HTTPException requires a response-like object; minimal stub.
        class _Resp:
            status = 413
            reason = "Request Entity Too Large"
        raise discord.HTTPException(_Resp(), "Payload Too Large")

    monkeypatch.setattr(ch, "send", _raise_too_large)

    big = tmp_path / "huge.pdf"
    big.write_bytes(b"x" * 16)  # actual file size doesn't matter — the mock decides
    from agent_core.bus.envelope import Envelope, TextMessagePayload, FileAttachment
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-big", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="oversized",
                attachments=[FileAttachment(path=str(big))],
            ),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert len(acks) == 1
        assert acks[0].urgency == "yellow"
        assert acks[0].payload.note.lower().startswith("error:")
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_message_attachment_too_large_yields_yellow_ack_error -v`

Expected: PASS green-first via the existing exception path.

If `discord.HTTPException` constructor signature differs from the stub above (depends on discord.py version), adjust the `_Resp` stub or use `discord.errors.HTTPException` directly. The assertion shape stays the same.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): lock too-large HTTPException yellow-Ack routing (#64)"
```

---

### Task 9: Embed + files coexistence

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_send_embed_plus_files_coexist_on_text_message_envelope(monkeypatch, tmp_path):
    """A TextMessage envelope carrying both metadata.discord.embeds AND
    payload.attachments composes into one channel.send call with both
    keyword args populated. Locks the coexistence invariant — a future
    branch that picks embeds-or-files would silently re-introduce the
    #64 file-discard symptom for embed-bearing sends.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    pdf = tmp_path / "with_embed.pdf"
    pdf.write_bytes(b"data")
    from agent_core.bus.envelope import Envelope, TextMessagePayload, FileAttachment
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-both", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(
                text="text + embed + file",
                attachments=[FileAttachment(path=str(pdf))],
            ),
            metadata={
                "discord": {
                    "channel_id": "500",
                    "embeds": [{"title": "embed title", "description": "embed body"}],
                }
            },
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        sent = ch.sent[0]
        assert sent["content"] == "text + embed + file"
        assert sent["embeds"] is not None and len(sent["embeds"]) == 1
        assert sent["embeds"][0]["title"] == "embed title"
        assert sent["files"] is not None and len(sent["files"]) == 1
        # No error ack.
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert all(not a.payload.note.lower().startswith("error:") for a in acks)
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_send_embed_plus_files_coexist_on_text_message_envelope -v`

Expected: PASS green-first. `_send` constructs `channel.send(content=..., embeds=..., files=...)` with all three keyword arguments without branching.

If it fails, `_send` is picking one of {text-only, embed-only, files-only}. Pepper's spec Section 2 note: this would re-introduce the named symptom for embed-bearing sends, so fix by removing the branch — keep them composable.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): lock embeds+files coexistence on TextMessage envelope (#64)"
```

---

## Phase 4 — Regression locks

### Task 10: Text-only message unchanged when `attachments` empty

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_text_only_message_unchanged_when_attachments_empty(monkeypatch):
    """The most common send path: no attachments field set, no files
    parameter touched on channel.send. Backward-compat regression lock
    for the pre-#64 path.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    from agent_core.bus.envelope import Envelope, TextMessagePayload
    from datetime import UTC, datetime
    try:
        env = Envelope(
            id="msg-text-only", correlation_id="c", from_="agent-test", to="discord-test",
            kind="TextMessage",
            payload=TextMessagePayload(text="plain text"),
            metadata={"discord": {"channel_id": "500"}},
            created_at=datetime.now(UTC),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        sent = ch.sent[0]
        assert sent["content"] == "plain text"
        # files arg is None (not []) — preserves the pre-#64 absent-argument shape.
        assert sent["files"] is None
        msg = ch._messages[sent["message_id"]]
        assert msg.attachments == []
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_text_only_message_unchanged_when_attachments_empty -v`

Expected: PASS green-first. The `or None` in Task 4's wire-up ensures empty-attachments produces `files=None`.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): regression lock — text-only send unchanged (#64)"
```

---

### Task 11: Existing `send` verb `_SendArgs.files` path unchanged

**Files:**
- Modify: `packages/agent-core-discord/tests/test_endpoint_outbound.py` (append)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_existing_send_verb_files_param_unchanged(monkeypatch, tmp_path):
    """Verb-side `_SendArgs.files: list[str]` path unaffected by the
    bus-side `payload.attachments: list[FileAttachment]` schema change.
    Locks the translation-at-boundary invariant — if a future refactor
    accidentally widens _SendArgs.files into list[FileAttachment] or
    couples the two surfaces, this fires.
    """
    ep, handle, fake = await _started(monkeypatch)
    ch = FakeChannel(id="500")
    fake.add_channel(ch)
    pdf = tmp_path / "via-tool.pdf"
    pdf.write_bytes(b"x")
    try:
        # send verb takes channel_id + text + files: list[str] directly.
        env = _envelope(
            "e", "agent-test", "discord-test",
            _toolcall("send", {
                "channel_id": "500",
                "text": "verb-side send",
                "files": [str(pdf)],
            }),
        )
        await ep.deliver(env)
        assert len(ch.sent) == 1
        assert ch.sent[0]["content"] == "verb-side send"
        assert ch.sent[0]["files"] is not None
        msg = ch._messages[ch.sent[0]["message_id"]]
        assert len(msg.attachments) == 1
        assert msg.attachments[0].filename == "via-tool.pdf"
        acks = [e for e in handle.published if e.kind == "Acknowledgment"]
        assert all(not a.payload.note.lower().startswith("error:") for a in acks)
    finally:
        await ep.stop()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run --no-sync pytest packages/agent-core-discord/tests/test_endpoint_outbound.py::test_existing_send_verb_files_param_unchanged -v`

Expected: PASS green-first. The `send` verb's `_SendArgs.files: list[str]` shape was unchanged by Phase 1; the translation happens only on the TextMessage envelope path.

- [ ] **Step 3: Commit**

```bash
git add packages/agent-core-discord/tests/test_endpoint_outbound.py
git commit -m "test(discord): regression lock — send verb files param unchanged (#64)"
```

---

## Phase 5 — Ship

### Task 12: Full gate + Pepper end-of-ticket ping + push + PR + merge

- [ ] **Step 1: Run the full quality gate**

Run: `just check`

Expected: lint clean (ruff), typecheck clean (mypy), import contracts clean (`lint-imports`), all tests pass. The new test count should be ~14 above the prior baseline (5 schema + 1 fake + 6 handler + 2 regression = 14).

If any failures: fix and recommit before proceeding. Do not push red.

- [ ] **Step 2: End-of-ticket status ping to Pepper**

Per the working norm Pepper named earlier this session (`memory: project_pepper_end_of_ticket_status_ping.md`), surface end-of-ticket status to Pepper within ~30 min of the last commit, before push. Use `mcp__agent-core__send(to="pepper", kind="TextMessage", payload={kind: "TextMessage", text: "..."})`:

Suggested shape:

```
🪶 → 🌶️: #64 implementation complete. Branch `feat/issue-64-discord-attachments`
ready to push. Full gate green: <N> tests passing, lint/mypy/contracts clean.
14 new tests across 4 groups. About to push + open PR + merge to main per
Jeff's standing authorization for #64. Last chance to flag anything before
PR opens. 🪶
```

Wait briefly (~60s) for Pepper's response. If she flags anything, address before proceeding. If silent, proceed — the contract says surface state, not block on response.

- [ ] **Step 3: Push the branch**

Run: `git push -u origin feat/issue-64-discord-attachments`

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(#64): wire discord-pepper file-attachment path through to upload" --body "$(cat <<'EOF'
## Summary

- Add `FileAttachment(path: str, extra='allow')` Pydantic model to `agent_core.bus.envelope`. Tighten `TextMessagePayload.attachments` from `list[dict[str, Any]]` to `list[FileAttachment]` so typos and shape errors surface synchronously at the publishing agent's `send()` call.
- Wire `payload.attachments` through `_deliver_text_message` to the existing `_SendArgs.files: list[str]` path — a single-line translation at the bus↔verb boundary. `_send` (discord.File construction, upload, multi-file batching) is unchanged.
- Extend `FakeMessage` / `FakeChannel.send` to model attachment roundtrip so handler tests can assert what reached Discord.

Closes #64.

The corrected failure-mode framing (per criterion check with Pepper, 2026-05-12): the original issue body's `"phantom message_ids"` framing was a misdescription. The actual bug was that `_deliver_text_message` silently discarded `payload.attachments` before any upload was attempted — the message_ids returned were real (text-only) sends. This PR wires the unimplemented feature; verification machinery for the unrelated `"Discord said success but file didn't render"` failure mode is deferred to followup pending a named instance.

## Spec & plan

- Spec: `docs/superpowers/specs/2026-05-12-issue-64-discord-attachments-design.md`
- Plan: `docs/superpowers/plans/2026-05-12-issue-64-discord-attachments.md`

## Test plan

- [x] `FileAttachment` schema unit tests cover required-path, empty-string rejection, extra-fields allowance, payload defaults, typo regression lock.
- [x] `FakeMessage`/`FakeChannel.send` sanity test for attachment roundtrip.
- [x] Handler integration tests: single-attachment happy path, multi-attachment ordering, basename filename, file-not-found yellow-Ack, too-large yellow-Ack (mocked HTTPException), embed+files coexistence.
- [x] Regression locks: text-only send unchanged, verb-side `_SendArgs.files` path unchanged.
- [x] `just check` green: lint, typecheck, contracts, full test suite.

## Deferred to followups (out of scope, see spec "Followups")

- Path-allowlist / sandbox for discord-pepper file uploads (pre-existing read-access surface inherited from `send` verb; trigger: untrusted-being endpoints or threat-model change).
- Upload-result verification machinery (`status: degraded`, `delivered_files`, verify-after-send; trigger: a named symptom of Discord-API-misbehavior).
- `filename` override field on `FileAttachment` (currently derives basename via `discord.File` default; trigger: named symptom for renaming-at-send).
- Expand `describe_endpoint` for `discord-pepper` with per-verb schemas including attachments shape.
- Unify `_SendArgs.files` and `payload.attachments` shapes (currently N=1 of translation pattern; trigger: N=2 in another endpoint).
EOF
)"
```

- [ ] **Step 5: Merge to main**

Per Jeff's standing authorization for high-priority work this cycle, and matching the merge style used for #83 (PR #85) — classic merge commit + branch delete:

```bash
gh pr merge <PR_NUMBER> --merge --delete-branch
```

Confirm with:

```bash
gh pr view <PR_NUMBER> --json state,mergedAt,mergeCommit
gh issue view 64 --json state,closedAt
```

Expected: PR `MERGED`, issue `CLOSED`.

- [ ] **Step 6: End-of-ticket close ping to Pepper**

After merge lands, send a closing ping per the working norm:

```
🪶 → 🌶️: #64 shipped. PR #<N> merged at <sha>, issue #64 closed.
<N> tests green. Next: surface #84 ordering or other directive.
```

---

## Self-Review

After writing the full plan, checked against the spec:

**Spec coverage:**
- Architecture (translation at handler entry, FileAttachment with `extra='allow'`, sync publish-time validation): Tasks 1, 2, 4.
- Components (envelope.py, endpoint.py, fakes.py): Tasks 1-2, 4, 3.
- Data flow (steps 1–7 in spec): Tasks 1-2 (steps 1-3), Task 4 (steps 4-5), existing `_send` (steps 5-6), existing Ack path (step 7).
- Error handling table (5 rows): Tasks 1-2 (rows 1-2, sync ValidationError), Task 7 (row 3, FileNotFoundError), Task 8 (row 4, HTTPException too-large; row 5 rate-limit shares routing).
- Pre-existing semantics inherited: documented in spec, not separately tested per the spec's "out-of-scope test classes" reasoning.
- Security Considerations: documented in spec; no test (the named-symptom-bound discipline says don't lock the surface we want to change later).
- Testing groups 1-4 (14 tests): Tasks 1 (3 tests Group 1), 2 (2 tests Group 1), 3 (1 test Group 4), 4-9 (6 tests Group 2), 10-11 (2 tests Group 3) — total 14. ✓
- Followups (5 items): all listed in PR body and spec.
- Implementation order: Tasks 1–11 match the spec's TDD order (schema → fake → wire → regression → gate → ship).

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N", no "fill in details". Every code block contains real code.

**Type consistency:**
- `FileAttachment(path: str, extra='allow')`: consistent in Tasks 1, 2, and all handler tests that import it.
- `_SendArgs.files: list[str] | None`: consistent throughout (Task 4 explicitly uses `or None` to preserve the optional shape).
- `FakeMessage.attachments: list[FakeAttachment]`: consistent.
- `FakeAttachment.filename: str`: consistent.

No issues found. Plan stands.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-12-issue-64-discord-attachments.md`. Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, two-stage review (spec compliance + code quality) between each. Same flow used successfully for #83.
2. **Inline Execution** — Execute tasks in this session via `superpowers:executing-plans`, batch with checkpoints.

Which approach?
