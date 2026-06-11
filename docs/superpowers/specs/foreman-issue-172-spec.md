# Spec: document Claude Code subagent + agent-core MCP inheritance contract (issue #172)

## Goal

Resolve issue [#172](https://github.com/jeffrichley/agent_core/issues/172) by
landing a reproducible investigation report and updating `agent-core-channel`'s
user-facing docs to make the subagent-dispatch contract explicit. The ticket
is scoped as **investigate + document + decide**, not fix: the question is
whether dispatched subagents inherit the parent's `mcp__agent-core__*`
connection (and therefore the parent's bus identity + inbox access), and the
answer must be (a) empirically confirmed and (b) written down where Wren /
Pepper / future agent-core users will find it before they dispatch a
subagent that touches the bus.

## Acceptance criteria

- A new investigation report exists at
  `docs/superpowers/tickets/issue-172-subagent-mcp-inheritance.md` containing:
  - The verbatim live-bus test recipe from the issue (steps 1-5: sentinel
    envelope, `list_pending` snapshot, subagent dispatch with
    `consume()`/`send()`, parent-side inbox re-check) so any operator with a
    real Claude Code session connected to a live `agent-core` daemon can
    reproduce it. Recipe MUST use the exact tool names the issue cites:
    `mcp__agent-core__list_pending`, `mcp__agent-core__consume`,
    `mcp__agent-core__send`.
  - The verbatim in-container empirical test the Worker actually ran:
    dispatch a subagent via the `Agent` tool (`subagent_type: general-purpose`,
    which has `Tools: *`) with a prompt instructing it to call
    `ToolSearch` and report every `mcp__*` tool name it can see, then
    return that list as its final message. The Worker's container has the
    `context7` MCP server connected (per project CLAUDE.md), so a
    subagent that inherits MCP will report `mcp__context7__*` tools; a
    subagent that does NOT inherit will report none.
  - The Worker's verbatim observed output from that dispatch, copy-pasted
    into the report under a `## Result` heading with a UTC ISO-8601
    timestamp. No paraphrasing.
  - A `## Disambiguation` section that maps the observed output to
    hypothesis 1 vs hypothesis 2 from the issue, with one sentence of
    interpretation. If the subagent reports any `mcp__context7__*` tool,
    hypothesis 1 (MCP inheritance) is confirmed by generalization (the
    inheritance is at the MCP-client level, not per-server) and the
    `agent-core` case follows. Make the generalization argument explicit
    so the reader can audit it.
  - A `## Canonical answer` one-paragraph statement of the contract
    (e.g., "Claude Code dispatched subagents inherit the parent's full
    set of `mcp__*` server connections by default; for the `agent-core`
    MCP server this means subagent calls to `consume`/`send`/`reply`
    operate as the parent's bus identity and race the parent for inbound
    envelopes").
  - A `## Operator confirmation` checkbox line for a human running the
    live-bus recipe later: `- [ ] Live-bus recipe executed against a
    running agent_core daemon; result matches the in-container finding.`
    Leave unchecked; the report's empirical claim rests on the
    in-container generalization until checked.
- `packages/agent-core-channel/README.md` gains a new `## Subagent
  dispatch and bus identity` section, inserted after the existing
  `## Windows Development Caveat` section, containing:
  - One sentence stating the contract: subagents dispatched via Claude
    Code's `Agent` / Task tool inherit the parent's `mcp__agent-core__*`
    tools and therefore operate as the parent's bus identity (same
    `from:` endpoint, same inbox).
  - One sentence on the operational consequence: any subagent the parent
    dispatches can race the parent for inbound envelopes (`consume`,
    `handle`) and send envelopes that Pepper / other agents will
    attribute to the parent.
  - A `### Safe-dispatch pattern` subsection with a working code example
    showing a `.claude/agents/<name>.md` custom subagent definition
    whose YAML frontmatter `tools:` field enumerates a safe-by-default
    allowlist that EXCLUDES every `mcp__agent-core__*` tool. Use
    `Read, Edit, Write, Bash, Grep, Glob` as the example allowlist —
    these are tools a subagent doing a focused code/research task
    typically needs, and the example MUST contain the comment
    `# mcp__agent-core__* deliberately excluded` on its own line inside
    the frontmatter so the intent is obvious to anyone copy-pasting.
    Name the file `bus-isolated.md` in the example.
  - A `### Parallel-session note` subsection (2-3 sentences) covering
    hypothesis 2 from the issue: multiple concurrent Claude Code sessions
    bound to the same agent name share one bus identity and one inbox,
    and the bus does NOT today provide inbox claim arbitration between
    them, so envelope-consumption ordering between two concurrent same-
    identity sessions is undefined. Explicitly state that fixing this is
    out of scope (tracked separately if it becomes a real operational
    issue) and that operators wanting strict single-consumer semantics
    should run one Claude Code session per agent name.
  - Cross-reference to the investigation report at
    `docs/superpowers/tickets/issue-172-subagent-mcp-inheritance.md`
    using a relative repo-root-style link.
- No production code changes. No tests added or modified. No
  CHANGELOG / news fragment (this is a docs-only change and the
  agent-core-channel package's own CHANGELOG.md does not exist —
  verified by `find packages/agent-core-channel -name 'CHANGELOG.md'`
  during planning).
- `just check` (or whatever the repo's quick gate is) continues to exit
  zero — the change touches only `*.md` and should be a no-op for
  lint/typecheck/tests, but the Worker still runs it to confirm.

## Approach

This ticket is documentation. The risk to manage is **claim discipline**
— do not state the canonical answer as fact without an empirical
observation behind it. The Worker's tool surface inside the foreman
container makes one empirical observation cleanly achievable: dispatch a
subagent via the `Agent` tool, ask it to enumerate every `mcp__*` tool
it can see, and look for `mcp__context7__*` in the response. The
foreman container is documented (in the project's root `CLAUDE.md`
quoted in the system prompt) to have the `context7` MCP server
connected, so context7 is a known-good proxy for "does MCP inherit".

If the in-container subagent reports `mcp__context7__*` tools,
inheritance is at the MCP-client level (not per-server-tool), and the
`agent-core` case follows because the Claude Code MCP client doesn't
discriminate between MCP servers when forking a subagent's tool
namespace. If it reports zero `mcp__*` tools, hypothesis 1 is falsified
and the canonical-answer paragraph in the report MUST be rewritten to
match — the Worker is expected to do this rewrite, NOT to pretend the
expected outcome happened.

**Why a custom subagent definition for the workaround, not just a
prompt-level instruction.** The issue calls this out explicitly: "the
parent has no way to scope a subagent's bus access without trusting the
subagent's prompt-adherence." Claude Code's `.claude/agents/<name>.md`
subagent files DO let a parent specify a `tools:` allowlist in YAML
frontmatter, and the harness enforces the allowlist mechanically —
that's the contract-level fix the operator can apply today without
waiting for an upstream Claude Code change. The README example
demonstrates this concrete mechanism rather than a wishful "tell the
subagent not to touch the bus" pattern.

**Why docs land in two places.** The investigation report
(`docs/superpowers/tickets/...`) is the auditable evidence — it captures
the test, the verbatim output, and the reasoning. The README section is
the user-facing surface — a developer integrating with
`agent-core-channel` is much more likely to read the README than dig
into `docs/superpowers/tickets/`. Splitting them keeps each doc focused
and avoids burying the workaround example inside a long incident
narrative.

**Why no inbox-claim mechanism in this PR.** Explicitly out of scope
per the issue: "Reworking the bus to require per-connection identity
tokens (much bigger change; file separately if hypothesis 1 +
sandboxing-wanted is the conclusion)." The README's parallel-session
note documents the current contract honestly and points at the design
choice an operator can make today (one session per agent name); a real
inbox-claim feature gets its own issue.

**Why no operator-confirmation gate on landing.** The "Test executed in
a controlled Claude Code session; result captured verbatim" acceptance
criterion in the issue is satisfied by the in-container test the Worker
runs — Claude Code IS the controlled Claude Code session, and the
result is captured verbatim in the report. The live-bus recipe is also
recorded so a human can verify the generalization on demand; the
unchecked `Operator confirmation` checkbox in the report makes the
status visible. Blocking merge on live-bus confirmation would block
the docs indefinitely with no marginal evidentiary value over the
in-container observation.

## Sub-requests (topologically sorted)

1. The Worker dispatches a subagent via the `Agent` tool with
   `subagent_type: general-purpose` and a prompt of approximately the
   following shape (the Worker may rephrase but MUST preserve intent
   and the verbatim-list ask):

   > "List every `mcp__*` tool you have available right now. Call
   > `ToolSearch` with query `mcp` and `max_results: 100` to enumerate
   > them, then return a single message containing the comma-separated
   > list of tool names (no commentary, no other prose). If you have
   > zero `mcp__*` tools, return the literal string `NONE`."

   The Worker captures the subagent's final message verbatim — no
   editing, no summarization — for inclusion in step 3's report.

2. The Worker creates the file
   `docs/superpowers/tickets/issue-172-subagent-mcp-inheritance.md`. The
   file's structure is:

   ```markdown
   # Subagent MCP inheritance — investigation report (issue #172)

   ## Background
   One paragraph linking issue #172 and stating the two competing
   hypotheses from the issue body.

   ## Live-bus test recipe (for operator-side reproduction)
   The verbatim five-step recipe from the issue, in numbered list form.
   Use the issue's exact tool names: `mcp__agent-core__list_pending`,
   `mcp__agent-core__consume`, `mcp__agent-core__send`. Use the
   issue's exact sentinel-text convention `BUS-SUBAGENT-TEST-<uuid>`.

   ## In-container empirical test (Worker, <UTC timestamp>)
   ### Method
   One paragraph describing what the Worker did: dispatched a subagent
   via the `Agent` tool with `subagent_type: general-purpose` and the
   prompt asking the subagent to enumerate `mcp__*` tools via
   `ToolSearch`. Note that the foreman container is documented (in
   `/root/.claude/CLAUDE.md` per the system prompt) to have the
   `context7` MCP server connected.

   ### Result
   ```
   <verbatim subagent final-message output captured in step 1>
   ```

   ## Disambiguation
   One paragraph mapping the result to hypothesis 1 or 2, with the
   generalization argument: MCP inheritance is at the MCP-client level
   (the subagent either has all the parent's MCP tools or none),
   not per-server, so observing context7 inheritance implies
   agent-core would inherit too.

   ## Canonical answer
   One paragraph stating the contract that matches the observed
   result. If `mcp__context7__*` tools appeared in the result, state
   hypothesis 1 confirmed and describe the contract as in the
   Acceptance criteria. If the result was `NONE` or empty of
   `mcp__*` tools, state hypothesis 1 falsified and rewrite the
   contract to match (subagents do not inherit, ghost envelopes
   were a parallel session, etc.). Do NOT publish a result
   that contradicts the observation.

   ## Operator confirmation
   - [ ] Live-bus recipe executed against a running agent_core daemon; result matches the in-container finding.

   ## References
   - Issue #172
   - `packages/agent-core-channel/README.md` § Subagent dispatch and bus identity
   ```

3. The Worker updates
   `packages/agent-core-channel/README.md`. Append the new section
   AFTER the existing `## Windows Development Caveat` section (which
   currently ends the file at line 60). The section's exact heading is
   `## Subagent dispatch and bus identity`. Body contents follow the
   Acceptance criteria's enumeration. The custom-subagent code example
   uses a fenced ` ```markdown ` block showing a complete
   `.claude/agents/bus-isolated.md` file with YAML frontmatter:

   ```markdown
   ---
   name: bus-isolated
   description: Use for code/research subagent tasks that must NOT touch the agent-core bus on the parent's behalf.
   tools: Read, Edit, Write, Bash, Grep, Glob
   # mcp__agent-core__* deliberately excluded — see ../packages/agent-core-channel/README.md § Subagent dispatch and bus identity
   ---

   You are a focused subagent dispatched by the parent agent for a single task.
   You do not have access to the agent-core bus. If the task appears to require
   bus access, stop and report the gap to the parent instead of attempting it.
   ```

   The cross-reference link from the README section to the
   investigation report MUST be a repo-root-relative path
   (`../../docs/superpowers/tickets/issue-172-subagent-mcp-inheritance.md`
   from the README's location) and the Worker MUST verify the link
   resolves by `ls`-ing the target file from the README's directory
   before committing.

4. The Worker runs `just check` (or, if `just check` is not available
   for any reason, runs `uv run ruff check .` and `uv run pytest -q`
   or whatever the repo's standard quick gate is — read `justfile` and
   the root `README.md` to confirm). Confirm zero failures and zero new
   failures vs main. Docs-only changes are not expected to affect any
   gate, but running it is the discipline.

5. The Worker stages the two new/modified files and commits with a
   message body that names the issue and summarizes the two-file
   change. Push happens via the foreman daemon — the Worker does not
   `git push`.

## File-level changes

| File | Change |
|---|---|
| `docs/superpowers/tickets/issue-172-subagent-mcp-inheritance.md` | New. Investigation report: live-bus recipe + in-container empirical test + verbatim subagent output + disambiguation + canonical answer + operator-confirmation checkbox. |
| `packages/agent-core-channel/README.md` | New section `## Subagent dispatch and bus identity` appended after `## Windows Development Caveat`. Names the contract, gives the safe-dispatch custom-subagent example, notes the parallel-session caveat, cross-links the investigation report. |

No changes expected anywhere in `packages/core/`, `packages/agent-core-discord/`,
or anywhere else in `packages/`. No test files modified. No CI config touched.

## Alternatives considered

- **Worker runs the live-bus test (start the daemon in the container,
  pre-populate an inbox, dispatch the subagent, check the parent-side
  inbox after).** Rejected: the foreman container does not have the
  `agent-core` MCP server connected (only `context7` per the project's
  root CLAUDE.md), so the subagent dispatched from inside the Worker
  cannot call `mcp__agent-core__*` tools and the test simply cannot
  resolve. Falling back to the context7-proxy test for the inheritance
  question, plus codifying the live-bus recipe for operator-side
  reproduction, gets the same evidentiary outcome the issue cares about.
- **Skip the empirical test entirely and document the answer from
  Claude Code's published subagent-tool-inheritance behavior.** Fails
  the issue's "Test executed in a controlled Claude Code session;
  result captured verbatim" acceptance criterion. The in-container
  test is cheap (one `Agent` dispatch) and produces a verbatim
  artifact, so there is no reason to skip it.
- **Document only in the investigation report and skip the README
  update.** Fails the issue's "Behavior documented in agent-core-
  channel's user-facing docs" criterion. The README IS the user-facing
  doc — there is no other user-facing surface for `agent-core-channel`
  (verified by `ls packages/agent-core-channel/`: only `README.md`,
  `pyproject.toml`, `src/`, `tests/`).
- **Add a per-connection identity token / inbox claim mechanism in
  this PR.** Explicitly out of scope per the issue's Out-of-scope
  list. The README's parallel-session note documents the current
  contract; a real claim mechanism gets its own issue.
- **Block landing on the operator running the live-bus recipe.**
  Blocks docs indefinitely with no marginal evidence over the
  in-container observation. The unchecked checkbox in the report
  makes the confirmation status visible without gating merge.
- **Use the `Explore` or `Plan` subagent_type for the in-container
  test instead of `general-purpose`.** `Explore` and `Plan` enumerate
  their tool sets as "All tools except Agent, ExitPlanMode, Edit,
  Write, NotebookEdit" — which still includes MCP tools, but
  `general-purpose` is documented as "Tools: *" and is the clearest
  signal for "this subagent should inherit everything", which makes
  the test the most direct possible read of the inheritance question.
  Pick `general-purpose`.

## Open questions

- The in-container test confirms MCP inheritance via context7 as a
  proxy. The generalization to `agent-core` is sound because Claude
  Code's MCP inheritance is at the MCP-client level (not per-server),
  but the explicit live-bus confirmation is delegated to a human
  operator via the report's `Operator confirmation` checkbox. The
  Reviewer should sanity-check the generalization argument is
  presented honestly in the report and that the checkbox is left
  unchecked.

## Out of scope

- Fixing Claude Code's subagent tool-inheritance behavior. Upstream
  concern outside this repo; the issue explicitly lists it as out of
  scope.
- Reworking the bus to require per-connection identity tokens, or
  adding an inbox-claim arbitration mechanism between concurrent
  same-identity sessions. Explicitly listed as out-of-scope in the
  issue; file separately if hypothesis 1 + sandboxing-wanted becomes
  the operational consensus.
- Re-attributing the three ghost envelopes from 2026-06-10. The issue
  states they are "already settled" and out of scope.
- Adding any production code change (no `bus_run.py` edits, no MCP
  server edits, no endpoint changes). This is docs-only.
- Adding tests. Docs change; no executable surface to test.
- Updating any other package's README or any other top-level doc
  (e.g., `docs/ROADMAP.md`, `docs/BACKLOG.md`, `AGENTS.md`). The
  issue scopes documentation to "agent-core-channel's user-facing
  docs", which is exactly the one README being touched.
- Updating the project's root `CLAUDE.md` or `README.md` to mention
  the contract. Possible future addition; not in scope here.
