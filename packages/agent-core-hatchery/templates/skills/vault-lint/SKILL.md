---
name: vault-lint
description: |
  Health check for the being's vault. Catches stale files, orphan pages,
  missing cross-references, contradictions, and missing load-bearing
  files. Runs on the vault_lint scheduler job (Wed + Sun 3:30 AM).
when_to_use: |
  - Wednesday and Sunday at 3:30 AM (scheduled)
  - Before a major vault reorganization
  - When the being feels the vault is "drifting" — files don't connect anymore
  - After importing content from another source
---

# vault-lint — health check for the vault

Walk the vault and produce a markdown report of any health issues. Don't
fix anything yet — just report. The being decides what to act on.

## What to check

1. **Load-bearing files exist and are non-empty** — IDENTITY.md, SOUL.md,
   USER.md, MEMORY.md, OPERATIONS.md, and the handoff pair.

2. **Stale files** — files in `daily/raw/`, `drafts/active/`,
   `gather/` not modified in N days (default 14). They may need
   archiving or deletion.

3. **Orphan pages** — markdown files with no inbound `[[wikilink]]`
   from MEMORY.md or any other indexed file. Either link them in or
   archive.

4. **Missing cross-references** — USER.md references a person; check
   if `people/<name>.md` exists. Same for projects, ideas, dreams.

5. **Contradictions** — same fact stated differently in two files
   (best-effort detection; flag suspected pairs for human review).

## Output

Write the report to `Memory/daily/lint/<ISO-date>.md`. Format:

    # Vault lint report — 2026-05-09

    ## Errors (must address)
    - ...

    ## Warnings (probably address)
    - ...

    ## Info (FYI)
    - ...

The scheduler job runs the lint script (`scripts/lint.py`) and pipes
output to the file above.

## See also

- `scripts/lint.py` — the executable lint logic
