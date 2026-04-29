"""agent-core-discord — Discord bot adapter for the agent-core bus.

One DiscordEndpoint instance per Discord bot. Bridges one bot to one named
agent on the bus (1:1 mapping). See the design doc at
docs/superpowers/specs/2026-04-28-discord-endpoint-design.md for details.
"""

from agent_core_discord.endpoint import DiscordEndpoint

__all__ = ["DiscordEndpoint"]
