"""IdentityInjector — injects agent identity files into session context.

Thin subclass of FileInjector with identity-appropriate defaults. Use this
when loading personality, preferences, and continuity files that define
who an agent is.

When the file list includes ``handoff.md``, this injector reads
``handoff-status.json`` (same directory as ``handoff.md``, or
``handoff_status_file`` relative to ``base_path``) so SessionStart context
does not load a stale handoff while SessionEnd / HandoffWriter still has work
in flight. States:

- ``pending`` — inject a legible notice; do not load ``handoff.md`` as authoritative
  (same session: in-flight finalize; other session: prior continuity still summarizing).
- ``ready`` — load ``handoff.md`` as usual.
- ``failed`` — inject the error; optionally append on-disk file as possibly stale.
- (no status file) — same behavior as FileInjector for ``handoff.md``.

The only difference from FileInjector is the default heading ("Identity"
instead of "Injected Files") and the default missing file behavior ("skip").

All logic is inherited from FileInjector. This subclass exists so that:
1. Agent configs read 'IdentityInjector' instead of 'FileInjector' — clearer intent.
2. Identity-appropriate defaults don't need to be spelled out in every config.

Configuration:
    In agent_core.yaml:

        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.identity_injector.IdentityInjector
              params:
                base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
                files:
                  - "SOUL.md"
                  - "pepper/preferences.md"
                  - "pepper/handoff.md"
                # Optional — override sidecar location (default: beside handoff.md)
                # handoff_status_file: "pepper/handoff-status.json"
                # skip_handoff_status_gate: true   # restore naive file-only read

See Also:
    agent_core.hooks.tools.file_injector.FileInjector: The base class with all logic.
    agent_core.hooks.handoff_status: Sidecar schema and helpers.
"""

from __future__ import annotations

from pathlib import Path

from agent_core.hooks.handoff_status import path_for_handoff, read_status
from agent_core.hooks.tools.file_injector import FileInjector


class IdentityInjector(FileInjector):
    """Injects agent identity files into session context.

    Thin subclass of FileInjector with identity-appropriate defaults.

    Attributes:
        DEFAULT_HEADING: "Identity"
        DEFAULT_MISSING_BEHAVIOR: "skip"
    """

    DEFAULT_HEADING = "Identity"
    DEFAULT_MISSING_BEHAVIOR = "skip"

    def _status_path(self, base_path: Path, handoff_rel: str, params: dict) -> Path:
        override = params.get("handoff_status_file")
        if override:
            return base_path / Path(override)
        return path_for_handoff(base_path / handoff_rel)

    def _inject_handoff_with_status(
        self,
        base_path: Path,
        file_rel: str,
        missing_behavior: str,
        params: dict,
        hook_input: dict,
    ) -> str | None:
        handoff_path = base_path / file_rel
        file_name = Path(file_rel).name
        status_path = self._status_path(base_path, file_rel, params)
        status = read_status(status_path)
        current_sid = hook_input.get("session_id")

        if status is not None:
            st = status.get("state")
            updated = status.get("updated_at", "unknown")
            sid = status.get("session_id", "unknown")
            same_session = bool(current_sid and sid == current_sid)

            if st == "pending":
                if same_session:
                    return (
                        f"## {file_name}\n\n"
                        "**Handoff status: pending** — this session's handoff is still being "
                        "finalized (or the handoff daemon has not marked it ready yet). "
                        "Do not treat any existing `handoff.md` on disk as authoritative until "
                        "state becomes `ready` or you receive a `HandoffReady` bus notification.\n\n"
                        f"- Last lifecycle touch: session `{sid}`, updated `{updated}`\n"
                    )
                # Prior session's continuity still in flight (Cutover #02 scenario b).
                return (
                    f"## {file_name}\n\n"
                    "**Continuity not ready yet** — the previous session's continuity is still "
                    "summarizing. You will receive a **continuity ready** notification (via the "
                    "Cutover #08 surface — e.g. `HandoffReady` on the bus routed to your inbox) "
                    "when it is available.\n\n"
                    "Until then, work from **current MEMORY.md** and **recent daily summaries**; "
                    "do **not** confabulate state from prior sessions or treat `handoff.md` on "
                    "disk as authoritative.\n\n"
                    f"- Status sidecar: session `{sid}` (not this boot), state `pending`, "
                    f"updated `{updated}`\n"
                )

            if st == "failed":
                err = status.get("error") or "unknown error"
                parts = [
                    f"## {file_name}\n\n",
                    f"**Handoff status: failed** — `{err}`\n\n",
                    "The summarizer or handoff daemon could not produce continuity for the last "
                    "cycle. Prefer **MEMORY.md** and **recent daily summaries** as ground truth. "
                    "If `handoff.md` exists below, treat it as the **most recent on-disk attempt** "
                    "(it may be partial, empty, or from an earlier successful run — not a silent "
                    "success).\n\n",
                ]
                if handoff_path.exists():
                    body = handoff_path.read_text(encoding="utf-8-sig")
                    parts.append("*Last on-disk `handoff.md` (possibly stale or partial):*\n\n")
                    parts.append(body)
                return "".join(parts)

            if st == "ready":
                if not handoff_path.exists():
                    if missing_behavior == "error":
                        raise FileNotFoundError(f"Required file not found: {handoff_path}")
                    if missing_behavior == "warn":
                        return (
                            f"## {file_name}\n\n"
                            "(status is `ready` but handoff.md is missing on disk)\n"
                        )
                    return None
                content = handoff_path.read_text(encoding="utf-8-sig")
                return f"## {file_name}\n\n{content}"

            # Unknown state — fall back to reading the file if present.

        return super()._build_section_for_file(
            base_path, file_rel, missing_behavior, hook_input, params
        )

    def _build_section_for_file(
        self,
        base_path: Path,
        file_rel: str,
        missing_behavior: str,
        hook_input: dict,
        params: dict,
    ) -> str | None:
        if Path(file_rel).name == "handoff.md" and not params.get("skip_handoff_status_gate"):
            return self._inject_handoff_with_status(
                base_path, file_rel, missing_behavior, params, hook_input
            )
        return super()._build_section_for_file(
            base_path, file_rel, missing_behavior, hook_input, params
        )
