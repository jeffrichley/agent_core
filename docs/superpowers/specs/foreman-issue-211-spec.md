# Spec: scrub lone UTF-16 surrogates at Discord inbound boundary (issue #211)

## Goal

Add a small `scrub_surrogates()` utility and apply it to inbound Discord message text immediately after sigil-parsing, before the text enters a `TextMessagePayload`. A lone or unpaired UTF-16 surrogate code unit (U+D800–U+DFFF) in a Discord message currently propagates into the bus envelope and from there into the Claude Code conversation transcript, where it poisons all subsequent API requests and blocks session recovery via `/compact`. See [issue #211](https://github.com/jeffrichley/agent_core/issues/211).

## Acceptance criteria

- A unit test feeds a string containing a lone high surrogate (`"\ud83c"`) through `scrub_surrogates()` and asserts the returned string round-trips through `json.dumps` / `json.loads` and `.encode("utf-8")` without error, and that the lone surrogate has been replaced by U+FFFD (the Unicode replacement character).
- A unit test exercises the subdivision-tag flag-emoji case: constructs the lone high surrogate that results from slicing the UTF-16 encoding of U+1F3F4 (BLACK FLAG) at a buffer boundary (programmatically, not by pasting the emoji), feeds it through `scrub_surrogates()`, and asserts the result is clean.
- An integration test in `test_endpoint_inbound.py` fires an `on_message` event with content containing a lone high surrogate and asserts the published envelope's `payload.text` is valid JSON-serialisable text (no lone surrogate present).
- `new_failures_count == 0` on the existing test suite (`just check` exits zero).

## Approach

**Pattern naming.** No GoF pattern fits cleanly. This is a **Sanitize-at-Entry** idiom: normalize malformed input at the earliest possible system boundary rather than defensively coding every downstream consumer. The relevant engineering principle is SRP: the inbound handler's job is to translate Discord events into well-formed bus envelopes; "well-formed" includes "valid UTF-8 / JSON-safe string content."

**Where to sanitize.** The inbound text enters the system at one narrow seam in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, inside `_make_on_message_handler`, at line 1063:

```python
urgency, text = parse_sigil(message.content or "")
```

The fix is to apply `scrub_surrogates(text)` immediately after this line, before `text` is passed to `TextMessagePayload(text=text)`. This is the single gate through which all Discord message text flows.

**The sanitizer.** Python 3 `str` objects can hold lone surrogate code points (via `surrogatepass` or sliced UTF-16 bytes). The round-trip `s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")` maps each ill-formed code unit to U+FFFD (replacement character `�`): `surrogatepass` on encode writes the WTF-8/CESU-8 byte sequence for the lone surrogate (e.g. `0xED 0xA0 0xBC` for U+D83C), which is invalid UTF-8; `replace` on decode then maps that invalid byte sequence to U+FFFD, reconstructing a clean str. Note: using `errors="replace"` on the *encoding* step instead would substitute ASCII `?` (0x3F), not U+FFFD — only the decode-side `replace` handler emits U+FFFD. This is the standard, zero-dependency approach; a regex over D800–DFFF is a valid alternative but adds complexity without benefit.

**Module placement.** Following the pattern of `chunking.py` (text chunking) and `sigil.py` (sigil parsing), a new file `packages/agent-core-discord/src/agent_core_discord/text_sanitize.py` houses `scrub_surrogates()`. This keeps the utility local to the package that owns the inbound path, avoids premature extraction to core (rule of one), and mirrors the existing fine-grained module decomposition in the Discord package.

**Central envelope-serialization pass.** The issue mentions a belt-and-suspenders pass in `packages/core/src/agent_core/bus/envelope.py`. This is deliberately out of scope here: (a) the Pydantic `Envelope` model's `str` fields would need a custom validator on every string-typed payload field, adding non-trivial complexity to the central model for a problem that has a single identified source; (b) the fix at the Discord boundary prevents the bad bytes from ever entering the bus, which is the stated goal.

## Sub-requests (topologically sorted)

1. **Create `packages/agent-core-discord/src/agent_core_discord/text_sanitize.py`** with the `scrub_surrogates()` function.

2. **Apply `scrub_surrogates()` to inbound text in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`**, in `_make_on_message_handler`, immediately after `urgency, text = parse_sigil(message.content or "")`.

3. **Add unit tests in `packages/agent-core-discord/tests/test_text_sanitize.py`** covering: lone high surrogate, lone low surrogate, the flag-emoji sliced-surrogate case, clean text passthrough, and JSON round-trip.

4. **Add integration test in `packages/agent-core-discord/tests/test_endpoint_inbound.py`** asserting that an inbound Discord message containing a lone surrogate produces a sanitized envelope.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/text_sanitize.py` | **Create.** Single public function `scrub_surrogates(s: str) -> str` that replaces lone surrogates with U+FFFD via `s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")`. |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify.** Import `scrub_surrogates` from `.text_sanitize`. In `_make_on_message_handler`, apply it to `text` immediately after the `parse_sigil` call. No other changes to this file. |
| `packages/agent-core-discord/tests/test_text_sanitize.py` | **Create.** Pure-function unit tests for `scrub_surrogates`: lone high surrogate, lone low surrogate, valid-pair passthrough, the programmatic flag-emoji case, and JSON round-trip assertion. |
| `packages/agent-core-discord/tests/test_endpoint_inbound.py` | **Modify.** Append one async integration test: `test_on_message_sanitizes_lone_surrogate`. Fires `on_message` with content containing `"\ud800"` and asserts the published `payload.text` is JSON-safe. |

## Alternatives considered

- **Sanitize in `parse_sigil()` in `sigil.py`**: Rejected. `sigil.py` is a text-classification utility whose contract is sigil detection; introducing Unicode normalization changes its responsibility and couples it to a concern from the inbound handler. The handler owns the translation step; it should own the sanitization.
- **Add a central validator on `TextMessagePayload.text` in `envelope.py`**: Considered (the issue mentions it as a belt-and-suspenders option). Rejected for this PR: it would require a custom Pydantic validator on every `str` field that might carry surrogate-poisoned text (not just `text` on `TextMessagePayload` but potentially `note` on `AcknowledgmentPayload`, `reason` on `CancellationPayload`, etc.), and the problem has a single known entry point. YAGNI — fix the entry point.
- **Regex over D800–DFFF range**: Functionally equivalent to `encode/decode`. Rejected in favour of the standard stdlib idiom which is simpler and correct.
- **Silent deletion instead of U+FFFD replacement**: Rejected per the issue requirement — "prefer replacement (U+FFFD) over silent deletion so the failure is visible but non-fatal."

## Open questions

None. The entry point (Discord inbound `on_message`), the fix (`encode/decode` with `replace`), the module placement (new `text_sanitize.py` in the Discord package), and the test shape are all unambiguous from the issue and the code.

## Out of scope

- The Claude Code transcript or `/compact` behavior itself — upstream tool; we defend the boundary.
- A central surrogate-scrub pass on `Envelope`'s Pydantic model — deferred by YAGNI (single identified source).
- Sanitizing non-string envelope fields (metadata dicts, Event `data` payloads) — not the confirmed vector.
- Outbound direction (text sent *to* Discord) — surrogates in outbound text would be rejected by Discord's API, not by the Anthropic API; different failure mode.
- Transcript-size / output-cap concerns — separate ticket per the issue.
- Any change to emoji rendering.
