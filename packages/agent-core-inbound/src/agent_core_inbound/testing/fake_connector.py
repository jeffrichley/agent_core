"""In-memory connector for Router tests + downstream contract tests."""
from agent_core_inbound.protocol import Connector
from agent_core_inbound.types import Allow, ConnectorEvent, Deny, Tier


class FakeConnector:
    """Connector whose verdict is configured per-instance.

    ``verdicts`` keys are ``(event_id, target_being)`` tuples; the
    value is the Allow|Deny to return. Unknown keys default to Deny
    (matches the deny-by-default policy a real connector enforces).
    ``rule_id_for`` returns the rule_id the router will log alongside
    an Allow verdict.
    """

    name = "fake"

    def __init__(self) -> None:
        self.verdicts: dict[tuple[str, str], Allow | Deny] = {}
        self.rule_ids: dict[tuple[str, str], str] = {}
        self.classify_calls: list[tuple[str, str]] = []

    def allow(
        self,
        *,
        event_id: str,
        target_being: str,
        tier: Tier = Tier.GREEN,
        reason: str = "test allow",
        rule_id: str = "test_rule",
    ) -> None:
        self.verdicts[(event_id, target_being)] = Allow(tier=tier, reason=reason)
        self.rule_ids[(event_id, target_being)] = rule_id

    def deny(self, *, event_id: str, target_being: str) -> None:
        self.verdicts[(event_id, target_being)] = Deny()

    def classify(
        self,
        event: ConnectorEvent,
        target_being: str,
    ) -> Allow | Deny:
        key = (event.event_id, target_being)
        self.classify_calls.append(key)
        return self.verdicts.get(key, Deny())

    def rule_id_for(self, *, event_id: str, target_being: str) -> str:
        return self.rule_ids.get((event_id, target_being), "unknown")


# Static check: FakeConnector satisfies the Connector Protocol.
_FAKE: Connector = FakeConnector()
