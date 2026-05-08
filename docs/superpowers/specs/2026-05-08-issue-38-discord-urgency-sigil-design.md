# Issue #38 — Discord urgency: sigil prefix replacement (Design)

> **Status:** Approved 2026-05-08. Ready for implementation plan.
>
> **Issue:** [#38](https://github.com/jeffrichley/agent_core/issues/38) — Discord adapter urgency detection: replace 3-word regex with layered signals.
>
> **Scope:** MVP only — sigil-prefix replacement (issue body's approach A). Layered design (B/C/D/E/F) deferred.
>
> **Roadmap:** RED tier of `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md`.

## Problem

The Discord adapter promotes inbound `TextMessage` envelopes to `urgency="red"` via a regex on raw message content (`packages/agent-core-discord/src/agent_core_discord/access.py:34`):

```python
urgency_red_regex: str = r"(?i)\b(urgent|now|stop)\b"
```

On 2026-05-06, a casual message — *"ok, more on the ideas for the lancaster trip. right now we are looking at..."* — matched on "now" and Pepper read it as red-priority. Trip planning is not red.

The deeper issue is structural: regex on free-form natural language has unbounded false positives. "now" and "stop" are among the most common English words. Tightening the pattern per-deployment doesn't compose, doesn't generalize across locales, and forces operators to discover failures by being interrupted.

The MVP fix is approach **A** from the issue body: drop the regex entirely; use explicit author-controlled sigil prefixes for urgency promotion.

## Out of scope

- Sender-map default urgency (issue body's approach B).
- Channel-of-origin defaults (issue body's approach C).
- Embedding-similarity to canonical samples (D).
- Tiny distilled classifier (E).
- LLM-as-classifier (F).
- Layered / stacked composition of the above (G).
- The `~` explicit-green sigil. Reserved for when sender / channel maps land and the override semantic becomes meaningful; teaching it now would be muscle memory for a no-op character.
- Multi-locale urgency keywords.
- Cross-platform parity (Slack, email). This is Discord-specific; other adapters can adopt the pattern when they implement urgency.
- Backward-compat opt-in for the old regex. The regex code path is removed entirely; existing access JSON files with `"urgencyRedRegex"` set will silently ignore the key on next daemon start.

## Design

### Sigil set

Two sigils only:

| Prefix | Urgency |
|---|---|
| `!` | red |
| `?` | yellow |
| (none) | green |

The sigil character is *stripped* from the published `TextMessagePayload.text`. The receiving agent sees the message without the sigil but with the elevated urgency on the envelope.

### Parser rules

1. **Position.** The sigil must be the first non-whitespace character of `message.content`. `!hello`, `  !hello`, `\n!hello` all parse as red. `hello !world` does not — sigil at position 0 (after leading whitespace) only.

2. **Stripping behavior.** When a sigil matches, the parser strips: leading whitespace + the sigil character + at most one following space. Examples:
   - `"!server down"` → `urgency="red"`, `text="server down"`
   - `"! server down"` → `urgency="red"`, `text="server down"`
   - `"!  server down"` → `urgency="red"`, `text=" server down"` (only one space is eaten; the second is preserved)
   - `"  !urgent fix"` → `urgency="red"`, `text="urgent fix"`

3. **Multi-char "sigils".** Only ONE sigil character is consumed. `!!hello` → `urgency="red"`, `text="!hello"`. Stacking sigils for "extra red" is not a thing — one is the highest tier.

4. **Position-0 only.** `hello !world` → no sigil match, `urgency="green"`, `text="hello !world"`. Mid-message punctuation is never reinterpreted as a sigil.

5. **Empty text after strip.** `"!"` alone → `urgency="red"`, `text=""`. The empty-text envelope still publishes; the agent sees a red wake with empty payload. Rare but well-defined.

6. **Both sigils.** Only the first sigil character fires. `!?hello` → `urgency="red"`, `text="?hello"`. The second sigil becomes literal text.

7. **No interaction with mentions, emoji, or attachments.** Discord mention syntax is `<@user_id>` — never starts with `!` or `?`. Attachments live separately in `message.attachments`, handled by existing code. The sigil parser only inspects `message.content`.

### Components

**New module:** `packages/agent-core-discord/src/agent_core_discord/sigil.py`

```python
from typing import Literal

Urgency = Literal["red", "yellow", "green"]

_SIGIL_TO_URGENCY: dict[str, Urgency] = {"!": "red", "?": "yellow"}


def parse_sigil(content: str) -> tuple[Urgency, str]:
    """Parse a sigil-prefixed message into (urgency, stripped_text).

    See docs/superpowers/specs/2026-05-08-issue-38-discord-urgency-sigil-design.md
    for the full parsing rules.
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

Single-purpose, no dependencies, importable from tests directly.

**Endpoint integration:** `packages/agent-core-discord/src/agent_core_discord/endpoint.py:776-790` — replace the regex block:

```python
# Before:
urgency: Any = "green"
regex = self._access.urgency_red_regex
if regex:
    try:
        if re.search(regex, message.content or ""):
            urgency = "red"
    except re.error:
        log.warning(...)

# After:
urgency, text = parse_sigil(message.content or "")
```

Then `TextMessagePayload(text=text)` instead of `TextMessagePayload(text=message.content or "")` at the envelope construction. Drop the `import re` if no other regex remains in the file.

**AccessConfig cleanup:** `packages/agent-core-discord/src/agent_core_discord/access.py` — delete the `urgency_red_regex` field (line 34) and the `urgency_red_regex=raw.get("urgencyRedRegex", ...)` assignment in `load_access_config` (line 75). Existing access JSON files with `"urgencyRedRegex"` set silently ignore the key on next daemon start; no warning, no migration script. Same as any other unrecognized JSON key.

## Edge cases

| Case | Behavior |
|---|---|
| Empty message (`""`) | `urgency="green"`, `text=""`. No sigil match. |
| Whitespace-only (`"  "`) | `urgency="green"`, `text="  "`. No sigil after lstrip. |
| Sigil with mention (`"!<@123> help"`) | `urgency="red"`, `text="<@123> help"`. Sigil stripped, mention preserved as Discord-internal syntax. |
| Sigil with emoji (`"!:fire: help"`) | `urgency="red"`, `text=":fire: help"`. Sigil stripped; emoji shortcode left intact. |
| Markdown after sigil (`"!**bold**"`) | `urgency="red"`, `text="**bold**"`. Sigil stripped; markdown preserved. |
| Existing access JSON has `urgencyRedRegex` | Key silently ignored on load. No warning. Operator's intent (regex behavior) is *not* honored — sigil is the only signal now. |

## Testing

### Unit tests for the parser (new)

`packages/agent-core-discord/tests/test_sigil.py` — parametrized over the seven parsing rules:

- `parse_sigil("!hello")` → `("red", "hello")`
- `parse_sigil("?hello")` → `("yellow", "hello")`
- `parse_sigil("hello")` → `("green", "hello")`
- `parse_sigil("  !hello")` → `("red", "hello")` (leading whitespace tolerated)
- `parse_sigil("\n!hello")` → `("red", "hello")` (newline tolerated as whitespace)
- `parse_sigil("! hello")` → `("red", "hello")` (single space eaten)
- `parse_sigil("!  hello")` → `("red", " hello")` (only one space eaten)
- `parse_sigil("!!hello")` → `("red", "!hello")` (only one sigil consumed)
- `parse_sigil("!?hello")` → `("red", "?hello")` (first sigil wins, second becomes text)
- `parse_sigil("hello !world")` → `("green", "hello !world")` (mid-message sigil not interpreted)
- `parse_sigil("")` → `("green", "")` (empty)
- `parse_sigil("   ")` → `("green", "   ")` (whitespace-only, content preserved verbatim)
- `parse_sigil("!")` → `("red", "")` (sigil with no body)

### Endpoint integration tests (modify)

`packages/agent-core-discord/tests/test_endpoint_urgency.py` currently has 6 tests asserting on regex behavior. Rewrite around the sigil:

- **Regression test for the original bug** — message `"ok, more on the ideas for the lancaster trip. right now we are looking at..."` → `urgency="green"`. The exact false-positive that triggered #38 must now produce green. Keep this test as the regression marker.
- `!server down` → published envelope has `urgency="red"`, `payload.text="server down"`. Verifies stripping at the endpoint level, not just the unit.
- `?status check` → `urgency="yellow"`, `payload.text="status check"`.
- Plain `"hello"` → `urgency="green"`, `payload.text="hello"`.
- The "urgent" / "stop" / "now" plain-text cases that used to fire red should be deleted or rewritten as plain green (they're no-ops under the new design).
- Drop the "custom regex override" test entirely — the field is gone.

### Migration robustness test (cheap, optional)

`packages/agent-core-discord/tests/test_access.py` (or wherever `load_access_config` is tested) — assert that an access JSON file containing `"urgencyRedRegex": "..."` loads cleanly and produces an `AccessConfig` without that field. Confirms no `TypeError` / `AttributeError` on old configs. ~5 lines.

## Components touched

**Source:**
- `packages/agent-core-discord/src/agent_core_discord/sigil.py` — new.
- `packages/agent-core-discord/src/agent_core_discord/access.py` — delete the regex field + JSON load logic.
- `packages/agent-core-discord/src/agent_core_discord/endpoint.py` — replace the regex code path with `parse_sigil()` call; update payload construction to use stripped text.

**Tests:**
- `packages/agent-core-discord/tests/test_sigil.py` — new.
- `packages/agent-core-discord/tests/test_endpoint_urgency.py` — rewrite around sigil; preserve the false-positive regression case.
- `packages/agent-core-discord/tests/test_access.py` — optional migration test.

**Docs:** Grep for `urgencyRedRegex` and `urgency_red_regex` before commit to catch any stale references; update if found.

## References

- 2026-05-06 false positive (issue body): Jeff's Lancaster trip message → red because of "now". Triggered this issue.
- Issue #33 — wake-builder uses urgency to compute aggregate signals; better classification → better aggregate (already-shipped contract).
- Recent shipping precedent: PR #65 closed #33 with the same brainstorming → spec → plan → subagent-driven workflow.
