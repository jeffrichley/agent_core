"""IdentityInjector — injects agent identity files into session context.

Thin subclass of FileInjector with identity-appropriate defaults. Use this
when loading personality, preferences, and continuity files that define
who an agent is.

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

See Also:
    agent_core.hooks.tools.file_injector.FileInjector: The base class with all logic.
"""

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
