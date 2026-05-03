"""IdentityInjector — inject agent identity files into session context.

Thin subclass of FileInjector with identity-appropriate defaults. Use this
when loading personality, preferences, or any other identity files that
define who an agent is.

If you need handoff/continuity files that participate in a sidecar status
protocol (writer race avoidance), use ``HandoffInjector`` instead.

Configuration:
    In agent_core.yaml::

        pipelines:
          SessionStart:
            - type: builtin.identity_injector
              params:
                base_path: "C:\\Users\\jeffr\\.pepper\\Memory"
                files:
                  - "SOUL.md"
                  - "pepper/preferences.md"

See Also:
    agent_core.hooks.tools.file_injector.FileInjector: The base class with all logic.
    agent_core.hooks.tools.handoff_injector.HandoffInjector: Sidecar-aware variant for handoff.md.
"""

from __future__ import annotations

from agent_core.hooks.tools.file_injector import FileInjector


class IdentityInjector(FileInjector):
    """Reads identity files and injects their contents into session context.

    Thin FileInjector subclass — no extra logic, just identity-appropriate defaults.

    Attributes:
        DEFAULT_HEADING: "Identity"
        DEFAULT_MISSING_BEHAVIOR: "skip"
    """

    DEFAULT_HEADING = "Identity"
    DEFAULT_MISSING_BEHAVIOR = "skip"
