"""HandoffInjector — load handoff.md safely under a sidecar status file.

Thin FileInjector subclass that reads a handoff sidecar status file
(``handoff-status.json`` next to ``handoff.md`` by default) before loading
the handoff body. This avoids racing the daemon writer / summarizer:

- ``pending`` — inject a notice; do not load ``handoff.md`` body.
- ``ready`` — load ``handoff.md`` as usual.
- ``failed`` — inject the error; optionally append the on-disk file as
  possibly stale.
- (no status file) — same behavior as FileInjector.

This tool is purpose-built for the handoff sidecar protocol. Each file it
loads must be named ``handoff.md`` (basename) — anything else raises
``ValueError``. For other identity / vault files use ``IdentityInjector``
or ``FileInjector``.

Configuration:
    In agent_core.yaml::

        pipelines:
          SessionStart:
            - type: builtin.handoff_injector
              params:
                base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
                files:
                  - "pepper/handoff.md"
                heading: "Continuity"
                missing_file_behavior: "warn"   # surface a missing handoff
                                                # rather than silently skip
                # Optional — override sidecar location (default: beside handoff.md)
                # handoff_status_file: "pepper/handoff-status.json"
                # skip_handoff_status_gate: true   # restore naive file-only read

See Also:
    agent_core.hooks.tools.file_injector.FileInjector: The base class.
    agent_core.hooks.handoff_status: Sidecar schema and helpers.
"""

from __future__ import annotations

from pathlib import Path

from agent_core.hooks.handoff_status import path_for_handoff, read_status
from agent_core.hooks.tools.file_injector import FileInjector


_HANDOFF_BASENAME = "handoff.md"


class HandoffInjector(FileInjector):
    """Reads handoff.md under sidecar status file gating."""

    DEFAULT_HEADING = "Continuity"
    DEFAULT_MISSING_BEHAVIOR = "skip"

    def _status_path(self, base_path: Path, handoff_rel: str, params: dict) -> Path:
        # Absolute ``handoff_status_file`` paths are honored as-is (Path division
        # discards the LHS); relative paths join under ``base_path``.
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

            if (
                st == "pending"
                and current_sid
                and sid == current_sid
            ):
                return (
                    f"## {file_name}\n\n"
                    "**Handoff status: pending** — this session's handoff is still being "
                    "finalized (or a background writer has not marked it ready yet). "
                    "Do not treat any existing `handoff.md` on disk as authoritative until "
                    "state becomes `ready` or you receive a `HandoffReady` bus notification.\n\n"
                    f"- Last lifecycle touch: session `{sid}`, updated `{updated}`\n"
                )

            if st == "failed":
                err = status.get("error") or "unknown error"
                parts = [
                    f"## {file_name}\n\n",
                    f"**Handoff status: failed** — `{err}`\n\n",
                ]
                if handoff_path.exists():
                    body = handoff_path.read_text(encoding="utf-8-sig")
                    parts.append(
                        "*The file below may be stale or partial (last on-disk copy).*\n\n"
                    )
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
        if Path(file_rel).name != _HANDOFF_BASENAME:
            raise ValueError(
                f"HandoffInjector only loads files named '{_HANDOFF_BASENAME}'; "
                f"got '{file_rel}'. Use IdentityInjector or FileInjector for "
                "non-handoff files."
            )
        if params.get("skip_handoff_status_gate"):
            return super()._build_section_for_file(
                base_path, file_rel, missing_behavior, hook_input, params
            )
        return self._inject_handoff_with_status(
            base_path, file_rel, missing_behavior, params, hook_input
        )
