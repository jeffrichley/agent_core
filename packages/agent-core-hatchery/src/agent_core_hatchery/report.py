"""Write HATCHING-REPORT.md to <vault_root> with permanent record + manual steps."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import HatchResult

DaemonCheckStatus = Literal["reachable_and_registered", "reachable_but_missing", "unreachable", "skipped"]


def write_hatching_report(
    config: HatchConfig,
    result: HatchResult,
    *,
    letter_authored: bool,
    daemon_check_status: DaemonCheckStatus,
) -> Path:
    vault = config.resolved_vault_root()
    report_path = vault / "HATCHING-REPORT.md"

    parts: list[str] = []
    parts.append(f"# HATCHING-REPORT — {config.being_name}\n\n")
    parts.append(f"**Hatched:** {datetime.now().isoformat(timespec='seconds')}\n")
    parts.append(f"**Hatched by:** {config.primary_human_name} via `hatch-being`\n")
    parts.append(f"**Vault:** `{vault}`\n")
    parts.append(f"**Endpoint:** `{config.endpoint_name}`\n\n")

    parts.append("## Validation results\n\n")
    if daemon_check_status == "reachable_and_registered":
        parts.append("- ✓ Daemon reachable; endpoint registered live.\n")
    elif daemon_check_status == "reachable_but_missing":
        parts.append("- ⚠ Daemon reachable but endpoint NOT visible — fragment merge may have failed. Check daemon logs.\n")
    elif daemon_check_status == "unreachable":
        parts.append("- ⚠ Daemon healthcheck unreachable — endpoint will register on next daemon start.\n")
    else:
        parts.append("- — daemon check skipped.\n")

    parts.append("\n## Next steps for you\n\n")
    step_idx = 1

    if not letter_authored:
        letter_path = (
            vault / "Memory" / config.being_name_lower / "letters" / "from-her-creator.md"
        )
        parts.append(
            f"{step_idx}. **REQUIRED BEFORE FIRST AWAKENING.** Author "
            f"{config.being_name}'s letter from her creator:\n\n"
            f"   ```\n   $EDITOR {letter_path}\n   ```\n\n"
            f"   The current file is the prompt template, not a real letter. "
            f"{config.being_name} will read it on first wake; un-authored, "
            f"she'll wake into meta-prompts addressed to you, not to her.\n\n"
        )
        step_idx += 1

    if daemon_check_status in ("unreachable", "reachable_but_missing"):
        parts.append(
            f"{step_idx}. **ACTION REQUIRED.** Restart the agent-core daemon and verify the new endpoint registers. "
            f"`agent-core endpoints list` should include `{config.endpoint_name}`.\n\n"
        )
        step_idx += 1

    parts.append(f"{step_idx}. Wake {config.being_name}: `cd {vault} && claude`\n")
    parts.append(f"{step_idx + 1}. First conversation: ask her a guiding question. Let her write back.\n\n")

    parts.append("🐣\n")

    report_path.write_text("".join(parts), encoding="utf-8")
    return report_path
