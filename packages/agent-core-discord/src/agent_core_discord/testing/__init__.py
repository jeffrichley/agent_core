"""Public test fakes for ``agent_core_discord``.

Importable stand-ins that mirror real ``discord.py`` types strictly enough
that tests catch shape drift between fake and real (e.g., ``Poll.question``
is a flat ``str``, not a nested object — see ``fakes.FakePoll``).

Downstream packages that integrate against ``agent_core_discord`` may
import the same fakes for their own tests.
"""

from agent_core_discord.testing.fakes import (
    FakeBusHandle,
    FakeChannel,
    FakeDiscordClient,
    FakeGuild,
    FakeMessage,
    FakePoll,
    FakePollAnswer,
    FakeScheduledEvent,
    FakeUser,
)

__all__ = [
    "FakeBusHandle",
    "FakeChannel",
    "FakeDiscordClient",
    "FakeGuild",
    "FakeMessage",
    "FakePoll",
    "FakePollAnswer",
    "FakeScheduledEvent",
    "FakeUser",
]
