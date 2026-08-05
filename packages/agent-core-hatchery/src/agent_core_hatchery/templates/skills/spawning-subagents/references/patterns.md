# Subagent patterns — worked examples

## Research subagent

When: open-ended question, multi-source synthesis needed.

Prompt shape:
- Goal (what you're trying to learn and why)
- Constraints (what's been ruled out, what shape the answer needs)
- Output spec (length, format, citations)

Example: "Research X library's approach to Y. Context: we're choosing
between X and Z for our use case. Return: 1-paragraph executive
summary, 5-7 bullet points on tradeoffs, links to canonical sources.
Under 400 words total."

## File-search subagent

When: looking for something across many files, don't know exactly where.

Prompt shape:
- What to find (literal symbol/pattern)
- Where to look (root path, file globs)
- What to return (paths, line numbers, snippets, or pattern summary)

Example: "Find all places in src/ that catch ValidationError. Return as
markdown list of file:line plus a one-line description of what each
handler does."

## Code-review subagent

When: want independent perspective without your own framing biasing the
review.

Prompt shape:
- The diff to review (paste or path)
- Specific concerns to check (or "general review")
- Severity scale + format

Example: "Review this PR for SQL safety. Return findings as a numbered
list with severity (must-fix/should-fix/nit) and one-line rationale."

## Parallel dispatch

When: 2+ independent subagent tasks could run concurrently.

Send them in one message with multiple Agent tool calls. Don't await
sequentially.
