"""DiscordEndpoint — bus endpoint that bridges one Discord bot to one agent.

Implementation lands in subsequent tasks. See the design doc and plan for
the task breakdown."""

from __future__ import annotations


class DiscordEndpoint:
    """Placeholder — real implementation in Task 3."""

    def __init__(self, *, name: str, target: str, token_env: str) -> None:
        self.name = name
        self.target = target
        self.token_env = token_env
