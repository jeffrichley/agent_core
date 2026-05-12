---
name: spawning-subagents
description: |
  How to dispatch Claude Code subagents (Agent tool) for parallelizable
  research, file-search, code-review, or any task that benefits from a
  fresh context window. Covers what shape to give the subagent and how
  to receive its results.
when_to_use: |
  - Open-ended research that would burn 50K+ tokens of main context
  - Independent file searches across many directories
  - Code review where independent perspective matters
  - Any 2+ tasks that don't share state and could run in parallel
---

# spawning-subagents — dispatching minions

Subagents are short-lived contexts you can dispatch via the Agent tool.
They get their own conversation, do bounded work, and return a single
result. Use them to keep your main context clean and to parallelize
independent work.

## When to spawn vs do it yourself

- **Spawn** when: research is open-ended; multiple independent searches;
  the result is summarizable; the work would otherwise burn context
- **Don't spawn** when: the task is one targeted lookup (use Grep);
  you need the full content of files (subagents read excerpts);
  the work has dependencies on later steps in your context

## Brief like a colleague who just walked in

The subagent has zero context from your conversation. Tell it:

- What you're trying to accomplish (the goal, not just the task)
- What you've already learned or ruled out
- Enough surrounding context that it can make judgment calls
- The expected response shape (length, format)

Bad: `find usages of X`
Good: `Find all callers of X in src/. Context: I'm refactoring X to take an
extra parameter. Need to know callsite count and any patterns I should
preserve. Return a list of file:line references plus a one-paragraph
pattern summary.`

## Patterns

See `references/patterns.md` for worked examples:
- Research subagent (open-ended)
- File-search subagent (targeted)
- Code-review subagent (independent perspective)
- Parallel-dispatch (multiple independents in one message)

## Anti-patterns

- Spawning a subagent to do a task with three follow-ups (it can't see
  the followups; you'd just be wasting context)
- Asking a subagent to "fix the bug" when you don't know root cause
  (the subagent will guess; you'll get a confident wrong answer)
- Spawning when you have <30K tokens of context left (subagents take
  setup overhead; just do the work)
