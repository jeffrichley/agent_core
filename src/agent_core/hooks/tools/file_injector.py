"""FileInjector — reads a list of files and injects their contents into session context.

This is a generic tool for loading any set of files into a Claude Code session.
Files are read in order and concatenated with ## headings derived from the filename.
All behavior is configurable via YAML params.

This tool serves as the base class for specialized injectors like IdentityInjector.
Subclasses override DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR to set
domain-appropriate defaults without changing any logic.

Configuration:
    In agent_core.yaml, register under any lifecycle event:

        pipelines:
          SessionStart:
            - tool: agent_core.hooks.tools.file_injector.FileInjector
              params:
                base_path: "/path/to/files"
                files: ["readme.md", "config/settings.md"]
                heading: "Project Context"
                missing_file_behavior: "skip"

    Required params:
        base_path (str): Root directory for file resolution.
        files (list[str]): Ordered list of file paths relative to base_path.

    Optional params:
        heading (str): The ToolResult heading. Default: class DEFAULT_HEADING.
        missing_file_behavior (str): "skip", "warn", or "error".
            Default: class DEFAULT_MISSING_BEHAVIOR.

See Also:
    agent_core.hooks.tools.identity_injector.IdentityInjector: Subclass for identity files.
    agent_core.hooks.protocol.HookTool: The protocol this tool implements.
"""

from pathlib import Path

from agent_core.models import ToolResult


class FileInjector:
    """Reads a list of files and injects their contents into session context.

    Subclasses can override DEFAULT_HEADING and DEFAULT_MISSING_BEHAVIOR.
    """

    DEFAULT_HEADING = "Injected Files"
    DEFAULT_MISSING_BEHAVIOR = "skip"

    def execute(self, event: str, hook_input: dict, params: dict) -> ToolResult:
        base_path_str = params.get("base_path")
        if not base_path_str:
            raise ValueError("Required param 'base_path' is missing")

        files = params.get("files")
        if not files:
            raise ValueError("Required param 'files' is missing")

        heading = params.get("heading", self.DEFAULT_HEADING)
        missing_behavior = params.get("missing_file_behavior", self.DEFAULT_MISSING_BEHAVIOR)

        valid_behaviors = ("skip", "warn", "error")
        if missing_behavior not in valid_behaviors:
            raise ValueError(
                f"Invalid missing_file_behavior '{missing_behavior}', "
                f"must be one of: {', '.join(valid_behaviors)}"
            )

        base_path = Path(base_path_str)
        sections: list[str] = []

        for file_rel in files:
            file_path = base_path / file_rel
            file_name = Path(file_rel).name

            if not file_path.exists():
                if missing_behavior == "error":
                    raise FileNotFoundError(f"Required file not found: {file_path}")
                elif missing_behavior == "warn":
                    sections.append(f"## {file_name}\n\n(file not found: {file_rel})")
                continue

            content = file_path.read_text(encoding="utf-8-sig")
            sections.append(f"## {file_name}\n\n{content}")

        return ToolResult(
            heading=heading,
            content="\n\n".join(sections),
        )
