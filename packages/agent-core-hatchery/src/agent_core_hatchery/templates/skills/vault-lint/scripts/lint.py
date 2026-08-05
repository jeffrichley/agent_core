#!/usr/bin/env python3
"""vault-lint — health check for an agent-core being's vault.

Invoked by the vault_lint scheduler job (Wed + Sun 3:30 AM) and
manually by the being. Walks the vault, emits a markdown report
to Memory/daily/lint/<ISO-date>.md.

Stub implementation — the full check set ships in v1.5+.
"""

import datetime
import sys
from pathlib import Path

LOAD_BEARING = ("IDENTITY.md", "SOUL.md", "USER.md", "MEMORY.md", "OPERATIONS.md")


def main(vault_root: Path) -> int:
    memory = vault_root / "Memory"
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    for lb in LOAD_BEARING:
        p = memory / lb
        if not p.is_file():
            errors.append(f"Missing load-bearing file: {p}")
        elif p.stat().st_size == 0:
            errors.append(f"Empty load-bearing file: {p}")

    handoff = next(memory.glob("*/handoff.md"), None)
    if handoff is None or handoff.stat().st_size == 0:
        warnings.append("No handoff.md found or it is empty (expected on day 0)")

    today = datetime.date.today().isoformat()
    report_dir = memory / "daily" / "lint"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{today}.md"

    out: list[str] = [f"# Vault lint report — {today}\n"]
    out.append("\n## Errors (must address)\n")
    if errors:
        out.extend(f"- {e}\n" for e in errors)
    else:
        out.append("(none)\n")
    out.append("\n## Warnings (probably address)\n")
    if warnings:
        out.extend(f"- {w}\n" for w in warnings)
    else:
        out.append("(none)\n")
    out.append("\n## Info (FYI)\n")
    if infos:
        out.extend(f"- {i}\n" for i in infos)
    else:
        out.append("(none)\n")
    report.write_text("".join(out), encoding="utf-8")

    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: lint.py <vault_root>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
