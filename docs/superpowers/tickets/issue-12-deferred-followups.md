# Deferred follow-ups — issue #12 (auto-ack / MCP mailbox)

Tracked here until picked up; see `ops-claude-code-mcp-auto-ack-trust.md` for shipped trust notes.

| Item | Rationale |
|------|-----------|
| **End-to-end test** via `Bus._dispatch` + persistence | Highest confidence path; needs harness around SQLite + deliver ordering. |
| **O(n) outbound eviction** → ordered structure | Only matters if `max_tracked_outbounds` is raised far above defaults; profile first. |
| **Shutdown race audit** (`stop()` vs concurrent `deliver()`) | Add only if real shutdown flakes appear; document bus stop ordering instead. |
| **Machine-readable delivery outcome** | If bridges need semantic success/failure beyond `error:` / urgency, add a versioned field under `metadata` (not ad-hoc JSON parsing of `note`). |
