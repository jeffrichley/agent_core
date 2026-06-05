# Spec: Dedupe HandoffReady bus envelopes during `/compact` storm (issue #136)

## Goal

After Claude Code runs `/compact`, the daemon's `HandoffJobsEndpoint`
publishes ~25 `HandoffReady` Event envelopes to one being's mailbox over
~5 minutes — one per parallel summarization sub-session firing
`SessionEnd` → `builtin.handoff_writer` → enqueue → publish. This spec
adds a time-windowed publish-side dedupe in `HandoffJobsEndpoint` so
only the first `HandoffReady` per `(routing_target, kind=HandoffReady,
event=SessionEnd)` pair within a configurable sliding window reaches
the bus. The remaining handoff jobs still run, still write their status
files and handoff markdown — only the bus envelope is suppressed, which
is the user-visible noise the being reports.

See issue [#136](https://github.com/jeffrichley/agent_core/issues/136)
for the live observation in Wren on 2026-05-29.

## Acceptance criteria

- `HandoffJobsEndpoint.__init__` in
  `packages/core/src/agent_core/endpoints/handoff_jobs.py` gains a
  `handoff_publish_dedupe_seconds: float = 60.0` kwarg. `0.0` disables
  dedupe entirely (always publish). Negative values clamp to `0.0`.
- The endpoint maintains a private `_recent_handoff_publishes:
  dict[str, float]` keyed by `routing_target`, storing the
  monotonic-clock timestamp (`asyncio.get_running_loop().time()`) of
  the most recent published `HandoffReady` for that target. Bounded by
  the same `max_tracked_outbounds`-shaped cap used elsewhere in this
  endpoint (default `10_000`); oldest entries evicted on overflow.
- In `_publish_result`, when `kind == "HandoffReady"` AND
  `req.event == "SessionEnd"` AND `handoff_publish_dedupe_seconds > 0`:
  - If `_recent_handoff_publishes[req.routing_target]` exists and
    `now - last_published_at < handoff_publish_dedupe_seconds`, log
    one `INFO` line of the form
    `handoff publish suppressed: routing_target=%s event=SessionEnd
    age_seconds=%.2f window_seconds=%.2f job_id=%s` and return without
    calling `self._handle.publish(env)`.
  - Otherwise publish as today, then record `now` in
    `_recent_handoff_publishes[req.routing_target]`.
- `req.event == "PreCompact"` HandoffReady envelopes are NEVER
  suppressed. They are the first envelope of a `/compact` cycle and
  carry meaningful "I am about to be compacted" signal. The dedupe
  window also does not record PreCompact publishes (so a PreCompact
  followed immediately by a SessionEnd still lets the SessionEnd
  through if it's outside the window; the bookkeeping is SessionEnd-
  only).
- `kind == "HandoffFailed"` envelopes are NEVER suppressed regardless
  of event. Failures are signal, not noise.
- The status file write (`_write_ready`) and the handoff markdown
  file write (`_atomic_write(handoff_path, normalized_handoff)`)
  happen BEFORE the dedupe check. Both occur on every successful job,
  exactly as today. Suppression affects ONLY the bus publish.
- The job intake idempotency check (`_idempotency_index` in
  `_post_job`) is unchanged — same-payload retries still short-circuit
  to the existing job id with status 202.
- `agent_core.endpoints.handoff_jobs.HandoffJobsEndpoint` is exposed
  via `builtin.handoff_jobs` in
  `packages/core/src/agent_core/plugins/builtin_aliases.py`; the YAML
  loader passes `params:` straight through to `__init__` already, so
  no plumbing change is needed for the new kwarg — confirm by reading
  `builtin_aliases.py` lines 34-40 and the existing test config in
  `packages/core/tests/test_handoff_jobs_endpoint.py` line 42-46 that
  drives the param-passthrough path.
- The example YAML at
  `docs/examples/pepper-agent-core.yaml` (the `builtin.handoff_jobs`
  block at lines 42-46) gains a commented-out
  `handoff_publish_dedupe_seconds: 60.0` line under `params:` with a
  one-line comment explaining what it does and pointing at issue #136.
  Code default already matches — the comment is documentation, not a
  behavioral override.
- A `fixed` towncrier news fragment is added under
  `packages/core/changelog.d/` (the dir does not exist yet — create
  it; `packages/core/towncrier.toml` already declares
  `directory = "changelog.d"`). Filename
  `136.fixed.md`. One-line description.
- `just check` (lint + typecheck + tests) exits zero.

## Approach

The storm is two compounding causes (per the issue):

1. `/compact` spawns N parallel summarization sub-sessions; each fires
   `SessionEnd`, each runs `builtin.handoff_writer`, each POSTs a job
   to `/internal/handoff-jobs`. The job intake idempotency layer
   (`HandoffJobsEndpoint._derive_idempotency_key`) doesn't catch these
   because every sub-session has its own `session_id`, its own
   `transcript_path`, and its own `transcript_size` — the key varies
   per the issue (#42) design (which is correct for distinct
   sessions). Each job runs and publishes its own `HandoffReady`
   envelope.
2. Each `HandoffReady` is then push-fanned to ~20+ zombie MCP
   transports on the daemon side, amplifying daemon log spam (O(N×M)
   `pushing notifications/claude/channel` lines).

This spec addresses cause #1 only — the cheapest, biggest UX win
called out by the issue ("(1) gives the biggest immediate win for
this specific symptom"). Cause #2 (zombie transport reaping) is
genuinely orthogonal — it amplifies log noise but does not multiply
the envelopes the being sees, and the fix lives in
`ClaudeCodeMCPEndpoint.SessionRegistry`/`_unregister_session`, a
different file with different concerns. Filed as a follow-up under
"Alternatives considered" / "Open questions"; it is explicitly out of
scope here per anti-overscope discipline.

**Where the dedupe lives.** Three candidate seams:

- `builtin.handoff_writer` (the hook). Stateless across sessions; the
  N summarization sub-sessions are separate processes (or at minimum
  separate hook invocations). No natural place to coordinate.
- `HandoffJobsEndpoint._post_job` (job intake). Tempting, but the
  worker hasn't run yet — we'd have to either drop the job (and not
  write the status file / handoff markdown the user might still want)
  or accept the job and silently skip the publish later. The second
  option is fine but moving the dedupe decision to intake forces us
  to predict outcomes we don't yet have.
- `HandoffJobsEndpoint._publish_result` (publish step). The single
  in-process owner of every `HandoffReady` publish, runs after the
  status file and handoff markdown are already on disk, has access to
  `routing_target` + `event` + `content_sha256`. This is the cleanest
  seam.

**Why time-window keyed on routing_target, not content_sha256.** The
issue body explicitly says: "each with a unique `session_id`,
`content_sha256`, and `job_id`". The summarization sub-sessions read
slightly different transcript tails and the SDK summarizer returns
slightly different markdown each time, so `content_sha256`-based
dedupe would not collapse them. The coarser key
`(routing_target, event=SessionEnd)` captures the actual pattern:
"one being's mailbox getting a burst of SessionEnd-driven handoff
publishes". Cross-being interference is impossible because
`routing_target` is per-mailbox.

**Why event=SessionEnd only.** PreCompact fires exactly once per
`/compact` and is the meaningful "I am about to be compacted" signal
the being wants. The first HandoffReady envelope in the storm is
PreCompact (per the issue: "Metadata field `handoff_event` is
`SessionEnd` on all but the first"). Suppressing PreCompact would
hide legitimate state changes; suppressing only SessionEnd targets
the noise exactly.

**Why publish-side, not delivery-side.** The bus already has a
delivery-side debounce (`ClaudeCodeMCPEndpoint._notify_mail_arrived`
debouncing wake notifications by urgency). That collapses wake
*notifications* but not the envelopes themselves — once an envelope
is in `_pending`, the being sees it on the next `list_pending`/
`consume`. Suppressing at publish keeps the envelope out of `_pending`
entirely, which is what the being wants.

**Eviction shape.** Follow the existing pattern in
`_evict_stale_outbounds` (claude_code_mcp.py, lines 320-329): on each
`_publish_result` call do an inline scan + drop entries older than
some TTL, and cap the dict size. The TTL for the dedupe map is
`max(handoff_publish_dedupe_seconds, 1.0) * 4` — a safety multiple of
the window so we never evict a still-load-bearing entry while the
window is open, but we also don't hoard entries forever. The cap
mirrors `max_tracked_outbounds` (default 10_000).

**Clock source.** `_publish_result` runs on the asyncio event loop
inside the worker task, so `asyncio.get_running_loop().time()` is the
correct clock — matches `_evict_stale_outbounds` and friends in
`claude_code_mcp.py`. Do NOT use wall-clock `time.time()` or
`datetime.now()` for the dedupe key — those drift on NTP corrections
and don't match the eviction code elsewhere in the project.

## Sub-requests (topologically sorted)

1. In
   `packages/core/src/agent_core/endpoints/handoff_jobs.py`, add to
   `HandoffJobsEndpoint.__init__` a new kwarg
   `handoff_publish_dedupe_seconds: float = 60.0`. Clamp negatives to
   `0.0` (`max(0.0, float(handoff_publish_dedupe_seconds))`). Store
   on `self._handoff_publish_dedupe_seconds`. Also add
   `self._recent_handoff_publishes: dict[str, float] = {}` and reuse
   the existing-style cap `self._max_tracked_handoff_publishes = 10_000`.
2. In the same file, add a helper
   `def _evict_stale_handoff_publishes(self, now: float) -> None:`
   that mirrors `_evict_stale_outbounds` from
   `packages/core/src/agent_core/endpoints/claude_code_mcp.py`
   lines 320-329 — drop entries where
   `now - t > max(self._handoff_publish_dedupe_seconds, 1.0) * 4`,
   then evict oldest until size ≤ cap.
3. In the same file, in `_publish_result`, immediately after the
   `if self._handle is None: ...` guard:
   - Capture `now = asyncio.get_running_loop().time()`.
   - Call `self._evict_stale_handoff_publishes(now)`.
   - If `kind == "HandoffReady"` and `req.event == "SessionEnd"` and
     `self._handoff_publish_dedupe_seconds > 0`:
     - Look up `last = self._recent_handoff_publishes.get(req.routing_target)`.
     - If `last is not None` and
       `now - last < self._handoff_publish_dedupe_seconds`:
       - `log.info("handoff publish suppressed: routing_target=%s event=SessionEnd age_seconds=%.2f window_seconds=%.2f job_id=%s", req.routing_target, now - last, self._handoff_publish_dedupe_seconds, job_id)`
       - `return` (without publishing).
   - Build and publish the envelope as today.
   - After a successful publish, if `kind == "HandoffReady"` and
     `req.event == "SessionEnd"`, record
     `self._recent_handoff_publishes[req.routing_target] = now`.
   - Do NOT record on PreCompact or on HandoffFailed.
4. In `packages/core/tests/test_handoff_jobs_endpoint.py`, add the
   following async tests next to the existing
   `test_handoff_jobs_endpoint_writes_status_and_publishes_ready`,
   reusing its YAML/bus fixture shape. The fixture pattern at lines
   20-77 — `vault_root`, `transcript_root`, `cfg.write_text(...)`,
   `build_bus_from_config`, `monkeypatch _extract_handoff` — applies
   to all of them. Each new test posts multiple jobs to the same
   endpoint and asserts the `stub_ep.inbox` (already used at lines
   100-117) contains the expected number of `HandoffReady` events.
   - `test_handoff_publish_dedupe_suppresses_consecutive_session_end`:
     Two SessionEnd jobs for the same `agent_name=pepper` posted
     <1s apart (each with its own unique `session_id` so the
     intake-level idempotency key differs). Endpoint constructed
     with default `handoff_publish_dedupe_seconds=60.0`. Assert
     exactly one `HandoffReady` envelope in `stub_ep.inbox` for
     `pepper`.
   - `test_handoff_publish_dedupe_window_resets`:
     Same setup, but monkeypatch
     `asyncio.get_running_loop().time` (or, simpler, drive a
     custom-clock test by setting `handoff_publish_dedupe_seconds`
     to a very small value like `0.05` and `await
     asyncio.sleep(0.2)` between posts). Assert two `HandoffReady`
     envelopes received.
   - `test_handoff_publish_dedupe_zero_disables`:
     Construct endpoint with
     `handoff_publish_dedupe_seconds: 0.0` (via YAML
     `params:`). Two SessionEnd jobs posted back-to-back. Assert
     two `HandoffReady` envelopes received.
   - `test_handoff_publish_dedupe_precompact_never_suppressed`:
     One PreCompact job, then within-window a second PreCompact
     job (or PreCompact-then-SessionEnd; the spec is that
     PreCompact does not record into the dedupe map AND is never
     suppressed). Two distinct `session_id`s. Assert both
     PreCompact `HandoffReady` envelopes received; for the
     PreCompact-then-SessionEnd variant, assert both received
     (because PreCompact didn't poison the SessionEnd's first-of-
     window slot).
   - `test_handoff_publish_dedupe_independent_per_mailbox`:
     Two SessionEnd jobs back-to-back with different
     `agent_name` (e.g., `pepper` vs `wren`, both wired as stub
     endpoints in the YAML). Assert each mailbox receives one
     `HandoffReady`. Cross-mailbox dedupe would be a bug.
   - `test_handoff_publish_dedupe_failed_not_suppressed`:
     Force `_extract_handoff` to raise so the job hits the
     `HandoffFailed` branch in `_process_job` (lines 295-309).
     Two such failing SessionEnd jobs back-to-back. Assert two
     `HandoffFailed` envelopes received — failures are signal.
5. Update `docs/examples/pepper-agent-core.yaml`: under the
   `builtin.handoff_jobs` block at lines 42-46, add a commented-out
   line in `params:`:
   ```yaml
       # Dedupe window for SessionEnd-driven HandoffReady envelopes
       # (issue #136). 0.0 disables. Code default is 60.0; uncomment
       # only to override.
       # handoff_publish_dedupe_seconds: 60.0
   ```
6. Add a towncrier news fragment at
   `packages/core/changelog.d/136.fixed.md` (create
   `changelog.d/` directory). One line:
   ```
   `HandoffJobsEndpoint` collapses bursts of `SessionEnd`-driven
   `HandoffReady` envelopes into one per routing target per
   `handoff_publish_dedupe_seconds` window (default 60s). Fixes the
   `/compact` storm reported in #136.
   ```
7. Run `just check` and confirm exit zero. Fix anything that
   surfaces (likely lint on the new helper / docstrings).

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/endpoints/handoff_jobs.py` | Add `handoff_publish_dedupe_seconds` kwarg + `_recent_handoff_publishes` dict + `_evict_stale_handoff_publishes` helper; wire dedupe + bookkeeping into `_publish_result` for `kind=HandoffReady, event=SessionEnd`. |
| `packages/core/tests/test_handoff_jobs_endpoint.py` | Six new tests covering suppression, window reset, `0.0` disable, PreCompact bypass, per-mailbox independence, and HandoffFailed bypass. |
| `docs/examples/pepper-agent-core.yaml` | Commented-out `handoff_publish_dedupe_seconds` line + one-line comment pointing at issue #136. |
| `packages/core/changelog.d/136.fixed.md` (new file; new directory) | Towncrier `fixed` fragment describing the dedupe and its default window. |

No other files change. No new runtime dependencies. No DB / persistence schema change (the dedupe map is in-memory only — a daemon restart that clears it is acceptable, mirrors the existing `_recent_outbound_ids` and `_idempotency_index` shape).

## Alternatives considered

- **Add zombie MCP transport reaping in
  `ClaudeCodeMCPEndpoint.SessionRegistry` (issue's option 2).**
  Rejected for this spec — it's a different file, addresses a
  different cause (log fan-out amplification, not envelope
  multiplicity), and the issue explicitly says "(2) is broader and
  worth doing regardless." Better as its own follow-up issue so the
  blast radius for each change stays small and the review surface
  stays scoped. Should be filed as a separate issue referencing
  #136 as the trigger.
- **Skip the SessionEnd hook entirely for summarization-spawned
  sessions (issue's option 3).** Rejected because it depends on
  Claude Code surfacing a way to identify summarization sessions
  (an env var or session-metadata flag) that doesn't exist today.
  The issue itself notes this dependency: "depends on Claude Code
  surfacing a way to identify summarization sessions." File as a
  pre-req on the Claude Code side first.
- **Dedupe by `content_sha256` instead of `(routing_target, event)`
  + time window.** Rejected because the issue explicitly observes
  "each with a unique session_id, `content_sha256`, and `job_id`"
  — content hashes do NOT collide across the storm (each
  summarization sub-session reads a slightly different transcript
  tail). Content-based dedupe would catch zero of the actual storm
  envelopes.
- **Dedupe at the job intake (`_post_job`) by dropping jobs
  altogether.** Rejected because the status file
  (`handoff-status.json`) and handoff markdown file
  (`handoff.md`) are user-visible side effects that the existing
  contract guarantees are written. Suppressing at publish keeps the
  file side effects intact (so downstream `IdentityInjector` still
  works) while removing only the bus noise. The job intake's
  existing `_idempotency_index` already catches exact-payload
  retries — that layer is untouched.
- **Raise the bus's wake debounce (the
  `_notify_debounce_seconds_by_urgency` map in
  `ClaudeCodeMCPEndpoint`).** Rejected — that collapses *wake
  notifications* but the envelopes still land in `_pending` and
  the being still has to drain them on next `consume()`. The
  issue's symptom is "the queue gets ~25 envelopes," which the
  wake debounce cannot fix.
- **Do nothing; recommend `mcp__agent-core__consume()` as a
  one-shot drain workaround.** Rejected because the issue lists
  this as the workaround in use today and the user is filing
  precisely because the noise is friction at the worst moment
  (immediately after `/compact`, when the being is trying to
  resume work from a fresh compacted state).

## Open questions

- **Default window value.** Spec proposes 60.0s. The observed storm
  lasted ~5 minutes with envelopes ~12s apart, so a 60s window
  would coalesce ~5 envelopes per slot — meaningful reduction (25 →
  ~5) but not down to "1-2" (the expected count in the issue). A
  larger window (e.g., 300s) would collapse the entire storm into
  one envelope but risks suppressing a real second handoff if a
  being legitimately exits two sessions close together. The
  reviewer should confirm 60s is the right starting point; the
  YAML kwarg means an operator can tune without code change. If
  the reviewer prefers 300s, just change the default constant and
  the YAML comment.
- **Should the suppressed-publish log line be `INFO` or `DEBUG`?**
  Spec proposes `INFO` so an operator scanning `daemon.log` can
  see "dedupe is doing its job during a `/compact`" without
  turning on debug logging. The reviewer may prefer `DEBUG` if
  daemon-log volume is a concern; the wake-suppression log lines
  in `claude_code_mcp.py` are mostly `INFO`/`DEBUG` mixed, so
  there is no single house rule.
- **Should this also dedupe `PreCompact`-then-`SessionEnd` pairs
  produced by ONE single user `/compact` invocation?** Today's
  spec lets PreCompact (1 envelope) and the first SessionEnd
  (1 envelope) both through. That arguably matches the issue's
  "Expected: 1-2 HandoffReady envelopes" wording. If the
  reviewer feels even 2 envelopes is one too many, switch the
  dedupe map to record on BOTH events (still only suppress
  SessionEnd). Flag — but the current design follows the
  issue's wording.

## Out of scope

- Zombie MCP transport reaping in
  `ClaudeCodeMCPEndpoint.SessionRegistry`. The issue's option 2 —
  file as a separate follow-up issue referencing #136 as the
  trigger. The N×M log-spam amplification it causes is real but
  orthogonal: the daemon log noise multiplies whether or not the
  envelope storm happens. Solving N (this spec) reduces the
  product but does not solve M.
- Surfacing a way to identify summarization-spawned sessions
  (issue's option 3). Blocked on Claude Code exposing a session-
  metadata flag or env var.
- Changing the job intake idempotency key
  (`_derive_idempotency_key` in `handoff_jobs.py`). The existing
  per-session, per-transcript-size key is correct for the
  problem it was designed for (issue #42) — distinct
  summarization sessions are correctly distinct jobs.
- Changing the existing wake debounce
  (`_notify_debounce_seconds_by_urgency`) in
  `ClaudeCodeMCPEndpoint`. Different concern, different file,
  different mechanism.
- Adding a metric / counter for suppressed publishes. Useful but
  YAGNI for now; the log line is enough to observe behaviour in
  the post-deploy `/compact` smoke test.
- Persisting the dedupe map across daemon restarts. The map is
  in-memory only; a restart clears it. Matches the existing
  `_recent_outbound_ids` and `_idempotency_index` shape — both
  are also in-memory only and the project accepts that posture.
- Adjusting `builtin.handoff_writer` itself. The hook stays
  enqueue-only; this spec changes only the daemon-side endpoint
  that already owns publish semantics.
