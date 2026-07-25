# Spec: 'Add an endpoint' reference doc (issue #507)

## Goal

Verify and correct `docs/guides/add-an-endpoint.md` — the adopter-facing reference for adding a new bus endpoint. The guide already exists and satisfies the issue's done-when criteria (doc under `docs/`, linked in the nav, walks from zero to a registered endpoint). The one remaining correctness gap is that the guide's background-work tip tells adopters to "spawn a background task" without showing `handle.spawn()`, which is the correct tracked API per the `Endpoint` protocol docstring in `packages/core/src/agent_core/bus/protocol.py`. This spec narrows the Worker's job to that single fix plus a build verification.

See issue #507, sub-ticket of #398, epic #262, track A spec at `docs/superpowers/specs/2026-07-16-theme-f-track-a-pypi-launch-design.md` (§A2-5b).

## Acceptance criteria

- `docs/guides/add-an-endpoint.md` exists under `docs/guides/` (already true; Worker must not delete or move it).
- The guide appears in the `mkdocs.yml` nav under `Guides` as `Add an endpoint: guides/add-an-endpoint.md` (already true; no nav change required).
- The `!!! tip "Return promptly from deliver()"` admonition block in the guide is replaced with an updated version that shows the `handle.spawn()` background-task pattern with a concrete code snippet.
- The updated tip cites `handle.spawn(coro, name=...)` as the correct API, explains that it wraps `asyncio.create_task()` with task tracking and failure routing, and includes a working Python code block demonstrating the ack-then-spawn pattern.
- Running `uv run mkdocs build --strict` exits 0 (no broken links, no missing references).
- No other sections of the guide change.

## Approach

No GoF pattern applies. This is a documentation accuracy fix applying the "make the right thing easy" principle: the guide is the primary surface adopters read, so it must show the correct `handle.spawn()` API rather than the vaguer "spawn a background task" prose.

**Why `handle.spawn()` and not `asyncio.create_task()` directly.** `BusHandle.spawn()` (`packages/core/src/agent_core/bus/handle.py:80-96`) wraps `asyncio.create_task()` with:
- Task registration in the handle's internal set (enables `_drain_tasks()` at stop).
- A done callback that routes non-cancellation exceptions to the endpoint's failure hook rather than letting them vanish silently.

The `Endpoint` protocol docstring in `packages/core/src/agent_core/bus/protocol.py` (lines 49 and 69) explicitly says `bus.spawn(coro, name=...)` is required. The guide's current tip omits this and is the only accuracy gap against the live protocol.

**What stays unchanged.** The four-step structure (implement → wire config → register plugin → run), the error table, the `StubEndpoint` test section, and the "Next steps" block all accurately reflect the current codebase and must not be altered.

## Sub-requests (topologically sorted)

1. **Update the background-task tip in `docs/guides/add-an-endpoint.md`.**

   Locate the admonition block in Step 1 (currently after the error table):

   ```markdown
   !!! tip "Return promptly from deliver()"
       The bus awaits `deliver()` before dispatching to any other endpoint. If you need to do model calls, network I/O, or anything slow, spawn a background task, ack immediately, and publish a follow-up envelope when the work completes.
   ```

   Replace it with:

   ```markdown
   !!! tip "Return promptly from deliver()"
       The bus awaits `deliver()` before dispatching to any other endpoint. For slow work — model calls, network I/O, heavy computation — ack immediately and hand off to a tracked background task via `handle.spawn()`. `spawn()` wraps `asyncio.create_task()` with task registration and failure routing so exceptions are not silently lost.

       ```python
       async def deliver(self, envelope: Envelope) -> None:
           assert self._handle is not None
           # Ack first so the bus can move on; do the slow work in the background.
           await self._handle.ack(envelope.id)
           self._handle.spawn(
               self._process(envelope),
               name=f"greeter-process-{envelope.id[:8]}",
           )

       async def _process(self, envelope: Envelope) -> None:
           """Slow work goes here — model calls, HTTP requests, etc."""
           if envelope.kind == "TextMessage":
               text = envelope.payload.text  # type: ignore[union-attr]
               print(f"{self._prefix} (processed): {text}")
       ```

       Use `asyncio.create_task()` only if you are inside a method that does not have access to the `BusHandle` (e.g. a utility helper). For endpoint `deliver()` implementations, always prefer `handle.spawn()`.
   ```

2. **Verify the docs build.**

   ```bash
   uv run mkdocs build --strict
   ```

   Expected: exits 0 with no warnings. If any warning appears, fix it before committing. The most common pitfall is a stale internal link in `docs/guides/index.md` or `docs/concepts/endpoints.md` — both already link to this guide, so no change is expected.

3. **Commit.**

   ```bash
   git add docs/guides/add-an-endpoint.md
   git commit -m "docs(guides): show handle.spawn() background-task pattern in add-an-endpoint guide"
   ```

## File-level changes

| File | Action | What changes |
|---|---|---|
| `docs/guides/add-an-endpoint.md` | Modify | Replace the `!!! tip "Return promptly from deliver()"` admonition with an updated version that shows the `handle.spawn()` ack-then-spawn pattern with a concrete code snippet |

No other files change. The nav (`mkdocs.yml`), the guides index (`docs/guides/index.md`), and all four guide steps outside the tip block are already correct and must not be altered.

## Alternatives considered

1. **Declare the issue already done and close without change.** The done-when criteria are all met (doc exists, nav linked, zero-to-registered walkthrough present). However, the protocol docstring in `protocol.py` explicitly requires `bus.spawn()` for background work, and the guide currently gives vague advice that leads adopters toward the untracked `asyncio.create_task()`. One sentence of accurate guidance prevents a class of silent task-failure bugs in adopter code. Not closing without the fix.

2. **Rewrite the full guide from scratch.** Out of scope. The existing guide is accurate and complete except for the one tip. A full rewrite introduces regresssion risk for no gain. Ruled out.

## Open questions

None. The `BusHandle.spawn()` method was verified in `packages/core/src/agent_core/bus/handle.py:80-96`. The `Endpoint` protocol docstring was verified in `packages/core/src/agent_core/bus/protocol.py:49,69`. The nav entry was verified in `mkdocs.yml:61`. The guides index entry was verified in `docs/guides/index.md`.

## Out of scope

- Updating `docs/concepts/endpoints.md` — that page shows `asyncio.create_task()` in its example (a separate doc not covered by this ticket).
- Adding `handle.spawn()` documentation to `docs/reference/index.md`.
- Any change to the four-step guide structure, the error table, the StubEndpoint test section, or the Next steps block.
- Changes to `mkdocs.yml` or `docs/guides/index.md` — both already reference the guide correctly.
- Per-package READMEs or bus config key documentation — those are other items within A2-5 and tracked separately.
