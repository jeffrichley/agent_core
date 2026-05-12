# Issue #64 — discord-pepper file-attachment path wire-up (Design)

> **Status:** Drafted 2026-05-12. Pending spec-review approval from Pepper (principal-on-this-stream).
>
> **Issue:** [#64](https://github.com/jeffrichley/agent_core/issues/64) — `discord-pepper: file-attachment path silently fails (ack reports sent with phantom message_ids; PDF not actually attached)`.
>
> **Scope:** Wire the existing `TextMessagePayload.attachments` schema field through `_deliver_text_message` to discord.py's file-upload path. The field is defined on the bus envelope but the discord-pepper adapter silently discards it. This design wires it, with element-level Pydantic validation (`FileAttachment` model) that fails synchronously at publish time so typos surface at the agent's `send()` call rather than as a later yellow Ack.

## Problem

`TextMessagePayload.attachments` exists in `packages/core/src/agent_core/bus/envelope.py:20` as `list[dict[str, Any]]` (default empty list). The bus validates this field on publish, the adapter receives it on the envelope — but `_deliver_text_message` in `packages/agent-core-discord/src/agent_core_discord/endpoint.py:638–697` never reads `payload.attachments`. It extracts `text`, `metadata.discord.channel_id`, `metadata.discord.reply_to`, `metadata.discord.embeds`, and constructs `_SendArgs(...)` **without `files=`**. The attachments list is silently discarded before any upload step is attempted.

The corrected failure-mode framing (per criterion check with Pepper, 2026-05-12): the original issue body described *"phantom message_ids"* and *"Discord API claimed success while the file silently failed."* That framing was a misdescription. The reality is **the adapter never tried to upload.** The `Acknowledgment` returned `status: sent` with real (not phantom) `message_ids` because the text-only `channel.send(content=...)` call did succeed — and that was the only call made. The attachment payload was discarded at the bus↔verb seam before any Discord API call.

**Concrete lived instances**, 2026-05-08:
- WHOI trip briefing PDF (881 KB) sent to `#job-niwc`. Ack `status: sent`, real `message_id`. Jeff confirmed: text visible, **no PDF attached** (because no upload was attempted).
- WAR PDF (14 KB) sent to `#pepper-chat` twice. Same shape both times.

The fix is unimplemented-feature-completion, not error-recovery. Verification machinery (verify-after-send, `status: degraded`, `delivered_files` field) was originally proposed in the issue body for a Discord-API-misbehavior scenario that has no named instance. Deferred to followup pending such an instance.

## Out of scope

- **Upload-result verification machinery.** `status: degraded`, `delivered_files: list[str]` on Ack payload, verify-after-send Discord re-fetch. Discord.py's `channel.send(file=...)` raises on HTTP 5xx, rate-limit, and file-too-big failures; the existing error path in `_deliver_text_message` catches and yellow-acks appropriately. The verification proposals were solving for *"Discord said success but didn't actually deliver"* — a scenario with no current named instance. Followup tracked.
- **Path-allowlist or chroot-style sandbox for discord-pepper file uploads.** Pre-existing read-access surface inherited from the `send` verb. Pepper's lived bug doesn't touch this. Followup pending (a) untrusted-being endpoints, or (b) threat-model change.
- **`filename` override field on `FileAttachment`.** `discord.File(path)` defaults the upload filename to `os.path.basename(path)`. No named symptom for renaming-at-send. Add when a named case for path-anonymization or display-name-override arrives.
- **Unifying `_SendArgs.files: list[str]` and `payload.attachments: list[FileAttachment]` shapes.** Translation lives at the handler boundary in this design. If N=2 of this translation pattern surfaces, consider unifying. Currently N=1.
- **`describe_endpoint` per-verb schema expansion.** `mcp__agent-core__describe_endpoint(name="discord-pepper")` returns a short description today; expanding it to cover per-verb schemas including the attachments shape is a separate documentation effort. Followup tracked.
- **Aspirational `FileAttachment` fields** (`filename`, `description`, `spoiler`, `voice_message`, etc. from discord.py). `extra='allow'` on the Pydantic model permits them to pass validation today; the adapter only consumes `path`. Wiring additional fields is incremental, named-symptom-bound.

## Design

### Architecture

Two-shape translation at the bus↔verb boundary:

- **Bus side** (`agent_core.bus.envelope`): tight Pydantic `FileAttachment` model. `path` required, `min_length=1`. `extra='allow'` so aspirational fields (per spec convention; see #83's loose-on-producer discipline) don't force coordinated bus migrations. Validation runs synchronously at publish time, so typos and shape errors fail at the publishing agent's `send()` call with a Pydantic stack trace pointing at the bad call.
- **Verb side** (`agent-core-discord` adapter, `_SendArgs.files: list[str]`): unchanged. The existing `_send` verb and all current callers (the `send` ToolInvocation tool, existing agents using `_SendArgs.files`) keep their string-list shape and behavior.
- **Translation point**: a single line in `_deliver_text_message` extracts `files = [a.path for a in payload.attachments]` and passes through to `_SendArgs(files=files)`.

No new modules, no new verbs, no new namespaces. The translation lives at the natural seam where bus contract meets verb contract.

### Components

**1.** `packages/core/src/agent_core/bus/envelope.py` — add `FileAttachment`, tighten the `attachments` annotation on `TextMessagePayload`:

```python
class FileAttachment(BaseModel):
    path: str = Field(min_length=1)
    model_config = ConfigDict(extra="allow")


class TextMessagePayload(BaseModel):
    kind: Literal["TextMessage"] = "TextMessage"
    text: str
    attachments: list[FileAttachment] = Field(default_factory=list)
```

The `default_factory=list` preserves backward compat: existing publishes without `attachments` continue to validate.

**2.** `packages/agent-core-discord/src/agent_core_discord/endpoint.py` — wire-up in `_deliver_text_message` (line ~638–697):

After the existing extract of `text`, `channel_id`, `reply_to`, `embeds`, add:

```python
files = [a.path for a in payload.attachments]
```

And pass `files=files` into the existing `_SendArgs(...)` construction. `_send` (line ~1203–1221) already handles `discord.File(path)` construction and multi-file batching — no changes there.

**3.** `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` — extend `FakeChannel.send` (line ~171–193) to model the attachment round-trip. Currently it accepts `files: list | None` as a parameter but doesn't reflect it on the returned `FakeMessage`. After change:

- `FakeMessage` exposes an `attachments` list.
- Each element exposes at minimum `.filename` (derived from the input path's basename, matching `discord.File`'s default behavior on the real client).
- Per the `test_fakes_mirror_real_strictly` working norm: model only the shape tests assert on; expand named.

Three files touched. No new files in production code; one new test file (Group 1 schema tests, see Testing).

### Data flow

Happy path:

```
1. Agent calls mcp__agent-core__send(
     to="discord-pepper", kind="TextMessage",
     payload={
       "kind": "TextMessage",
       "text": "Briefing attached.",
       "attachments": [{"path": "/abs/path/briefing.pdf"}]
     },
     metadata={"discord": {"channel_id": "..."}}
   )

2. Bus: Pydantic constructs TextMessagePayload.
   For each dict in attachments, FileAttachment validates path (required, min_length=1).
   Typo / missing path / empty string raises ValidationError synchronously at the
   publishing agent's send() call. Bus refuses to enqueue invalid envelopes.

3. Bus delivers validated envelope to discord-pepper.

4. _deliver_text_message handler:
     text/channel_id/reply_to/embeds: extracted as today.
     NEW: files = [a.path for a in payload.attachments].
     _SendArgs(channel_id, text, reply_to, embeds, files=files).
     await self._send(args).

5. _send (existing): [discord.File(p) for p in args.files]; rejects HTTP URLs upfront.
   channel.send(content=..., embeds=..., files=...). Files attach to first chunk only
   when text is multi-chunked.

6. Discord returns Message objects; _send returns {"status": "sent", "message_ids": [...]}.

7. _deliver_text_message publishes green Acknowledgment with note=json.dumps(result).
```

Topic-override invariant: when the agent sets `metadata.discord.channel_id` explicitly (as #83 covers), routing uses that channel. The attachment list is delivered to whatever channel resolution produces — channel resolution and attachment delivery are orthogonal.

### Error handling

All failure modes route through existing paths. No new code, no new ack shape, no new urgency tier.

| Failure | Where it surfaces | Severity |
|---|---|---|
| Malformed dict (typo like `{paht: ...}`, missing `path`) | Sync `ValidationError` at agent's `send()` call. Stack trace at agent. | Synchronous to agent |
| Empty/whitespace path | Same — `Field(min_length=1)` rejects at validation time | Synchronous to agent |
| Path doesn't exist on disk | `discord.File(path)` raises `FileNotFoundError` → existing `_send` exception path → `_ToolError` → yellow Ack with `note="error: ..."` | Yellow Ack |
| File too large (Discord 25 MB) | `discord.HTTPException` via discord.py → same exception path | Yellow Ack |
| Rate-limit / Discord 5xx | discord.py raises → same exception path | Yellow Ack |

**Explicitly NOT modeled:** *"Discord API claimed success but file didn't actually render."* No named instance. Verification machinery deferred to followup.

#### Pre-existing semantics inherited

The wire-up inherits these properties from `_send`'s existing behavior — naming them so a future audit doesn't assume #64 introduced them:

- **Relative paths** resolve against the daemon process's cwd. No absolute-path constraint enforced.
- **Symlinks** are followed (Python's standard file-open behavior).
- **Multi-file partial failure**: discord.py treats `channel.send(files=[...])` as all-or-nothing on the batch. If any `discord.File(path)` raises before `send()` is called, no files attach for that message.
- **First-chunk-only**: when text exceeds Discord's per-message length and `_send` chunks the message, files attach to the first chunk only. Subsequent chunks are text-only.

These are stable, documented, and not changed by this work.

### Security considerations

`payload.attachments[].path` is forwarded to `discord.File(path)`, which opens the file in the daemon process's filesystem context. **Any agent that can publish a `TextMessage` envelope to `discord-pepper` can cause the daemon to upload any file the daemon process has read access to** — including `~/.ssh/id_ed25519`, `~/.gbrain/config.json` (Supabase pooler URL with credentials), `~/.pepper/.env`, the SQLite scheduler DB, `/etc/passwd`, etc.

This surface is **pre-existing on the `send` ToolInvocation path** (`_SendArgs.files: list[str]` already accepts arbitrary local paths from agents). #64's wire-up inherits the property; it does not introduce or widen it. The current threat model (trusted-being endpoints only) absorbs the risk.

**Mitigation deferred to followup** (see Followups #1): a path-allowlist or chroot-style sandbox becomes warranted when (a) untrusted-being endpoints are added, or (b) the threat model otherwise changes. Until then, the trust boundary is the agent-bus admission control.

## Testing

Fourteen tests across four groups. Each test names a specific behavior tied to either a named symptom (Pepper's lived experience) or a named documented mode (this design's contract).

### Group 1: Schema validation (5 tests, new file)

File: `packages/core/tests/bus/test_file_attachment_schema.py`

- `test_file_attachment_requires_path` — `FileAttachment()` raises `ValidationError`.
- `test_file_attachment_rejects_empty_string_path` — `FileAttachment(path="")` raises (catches `Field(min_length=1)`).
- `test_file_attachment_allows_extra_fields` — `FileAttachment(path="/x", filename="y")` validates; locks the `extra='allow'` aspirational-field tolerance.
- `test_text_message_payload_defaults_attachments_empty` — `TextMessagePayload(text="hi")` validates with empty `attachments` list; backward compat.
- `test_text_message_payload_typo_in_attachment_key_raises_at_publish` — `{"text": "hi", "attachments": [{"paht": "/x"}]}` raises `ValidationError`. Named-symptom regression lock.

### Group 2: Handler wire-up (6 tests, append)

File: `packages/agent-core-discord/tests/test_endpoint_outbound.py`

- `test_text_message_with_single_attachment_uploads_to_discord` — happy path: PDF path → `channel.send` receives `files` containing the expected file.
- `test_text_message_with_multiple_attachments_uploads_all` — two-to-three paths land; **order preserved** (`assert [a.filename for a in fake_msg.attachments] == [os.path.basename(p) for p in input_paths]`).
- `test_text_message_attachment_uses_basename_as_filename` — `FakeMessage.attachments[0].filename == os.path.basename(path)`. Locks the `discord.File` default behavior at the test boundary.
- `test_text_message_attachment_file_not_found_yields_yellow_ack_error` — nonexistent path → yellow Ack `note.startswith("error:")`; no Discord message published.
- `test_text_message_attachment_too_large_yields_yellow_ack_error` — mock `channel.send` to raise `discord.HTTPException` (413 or similar) → yellow Ack via existing exception path. Locks routing for the documented mode (not pinned to a lived symptom; the bar is lower for tests-on-documented-modes).
- `test_send_embed_plus_files_coexist_on_text_message_envelope` — payload with `text` + `metadata.discord.embeds` + `payload.attachments` compose into a single `channel.send` with all three keyword arguments populated. Locks the coexistence invariant.

### Group 3: Regression locks (2 tests, same file)

- `test_text_only_message_unchanged_when_attachments_empty` — the most common send path keeps working with no attachments field touched. Backward compat.
- `test_existing_send_verb_files_param_unchanged` — verb-side `_SendArgs.files: list[str]` path unaffected by the bus-side schema change. Locks the translation-at-boundary invariant — if a future refactor leaks the new shape into `_SendArgs`, this test fires.

### Group 4: Fake extension sanity (1 test)

File: `packages/agent-core-discord/tests/test_fakes.py` (if exists) or fold into Group 2.

- `test_fake_message_records_attachments_from_send_call` — `FakeChannel.send(files=[...])` returns a `FakeMessage` whose `attachments` list reflects the inputs with `.filename` derived from basename. Without this, the Group 2 assertions are toothless.

### Out-of-scope test classes (explicitly considered, decided against)

- **File-on-first-chunk-only-when-text-multi-chunked.** Pre-existing `_send` behavior, unchanged by this work. Covered by the *Pre-existing semantics inherited* subsection.
- **Path-is-a-directory / broken-symlink.** Same exception shape as `FileNotFoundError`; runtime error path is locked by test 4. Adding redundant tests for the same path is YAGNI.
- **Cross-platform / special-char path handling.** Python's path machinery handles. No named symptom.
- **Concurrent multi-file race conditions.** Out of scope; same shape as #83's cache-concurrency discussion.

The restraint here is part of the discipline: the temptation to *"since we're touching tests, let's add ten more"* is the cheap-while-touching alarm from #83's Q2 (cache extraction). Fourteen tests tied to documented behaviors is the right scope.

## Followups (out of scope for #64)

Tracked for separate tickets, each named-trigger-bound:

1. **Path-allowlist / sandbox for discord-pepper file uploads.** Pre-existing read-access surface inherited from the `send` verb. **Trigger:** addition of untrusted-being endpoint, or threat-model change.
2. **Upload-result verification machinery.** `status: degraded`, `delivered_files: list[str]` on Ack payload, verify-after-send Discord re-fetch. **Trigger:** a named symptom of *"Discord API claimed success but file didn't render."* No current instances.
3. **`filename` override field on `FileAttachment`.** Currently derives basename via `discord.File`'s default. **Trigger:** a named symptom for renaming-at-send (e.g., anonymizing a source path before posting, display-name override).
4. **Expand `mcp__agent-core__describe_endpoint` for `discord-pepper`** to include per-verb schemas — payload shapes, attachment formats, required vs optional fields. The short description returns today; expansion is documentation work that doesn't block #64.
5. **Unifying `_SendArgs.files` and `payload.attachments` shape.** Translation lives at the handler boundary today. **Trigger:** N=2 of this translation pattern in another endpoint.

## Implementation order

TDD, bite-sized per `writing-plans` discipline:

1. **Schema first.** Add `FileAttachment` + wire to `TextMessagePayload.attachments` in `agent_core.bus.envelope`. Write Group 1 tests (5 schema validation tests). Red → green via the new model.
2. **Fake extension.** Extend `FakeMessage.attachments` to model the round-trip. Write Group 4 sanity test. Red → green.
3. **Handler wire-up.** Read `payload.attachments` in `_deliver_text_message`, translate to `_SendArgs.files`. Write Group 2 tests (6 handler-integration tests). Red → green.
4. **Regression locks.** Group 3 tests (2 tests). Should pass green-first; if they fail, the wire-up has a backward-compat bug.
5. **Full gate.** `just check` green: lint, mypy, contracts, all tests.
6. **End-of-ticket status ping to Pepper** (per the working norm) before push.
7. **PR + merge.**

## Open questions for spec review

None. Every design decision resolved through Q1 (named-vs-speculative scope), Q2 (schema tightness), Q3 (translation routing), and Section 1–4 refinements.
