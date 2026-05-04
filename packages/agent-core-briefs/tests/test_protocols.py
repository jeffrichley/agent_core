"""Protocol definitions are runtime-checkable and have the correct shape."""

from __future__ import annotations

from datetime import datetime

import pytest
from agent_core_briefs.protocol import (
    DeliveryResult,
    Destination,
    Fetcher,
    PlaybookRef,
    SectionSpec,
    validate_destination_signature,
)


class TestFetcherProtocol:
    def test_fetcher_is_runtime_checkable(self):
        class _Good:
            type_id = "test.good"
            namespace = "test"

            async def fetch(self, config: dict, when: datetime) -> dict:
                return {}

        assert isinstance(_Good(), Fetcher)

    def test_missing_type_id_fails_runtime_check(self):
        class _Bad:
            namespace = "test"

            async def fetch(self, config, when):
                return {}

        assert not isinstance(_Bad(), Fetcher)

    def test_missing_fetch_fails_runtime_check(self):
        class _Bad:
            type_id = "x"
            namespace = "x"

        assert not isinstance(_Bad(), Fetcher)


class TestDestinationProtocol:
    def test_destination_is_runtime_checkable(self):
        class _Good:
            type_id = "test.good"

            async def deliver(self, sections, playbook, scope, when, config, bus_handle):
                return DeliveryResult(success=True, ref="test-1")

        assert isinstance(_Good(), Destination)

    def test_missing_deliver_fails_runtime_check(self):
        class _Bad:
            type_id = "x"

        assert not isinstance(_Bad(), Destination)


class TestValidateDestinationSignature:
    def test_validate_destination_signature_accepts_correct_shape(self):
        class _Good:
            type_id = "good"

            async def deliver(self, sections, playbook, scope, when, config, bus_handle):
                pass

        # Should not raise.
        validate_destination_signature(_Good())

    def test_validate_destination_signature_rejects_missing_bus_handle(self):
        """Old-style ``deliver`` without ``bus_handle`` passes
        ``runtime_checkable`` but fails the explicit signature check.

        This is the gap that motivated the helper: ``runtime_checkable``
        only validates attribute presence, so a destination written
        against the pre-T11 protocol shape would silently pass
        ``isinstance`` and then explode at first publish with a
        ``TypeError`` about positional argument count. The helper turns
        that into a loud, named failure at registration time.
        """

        class _OldStyle:
            type_id = "old"

            async def deliver(self, sections, playbook, scope, when, config):
                pass

        # Confirms the runtime_checkable gap (informational — this is
        # exactly why the helper exists).
        assert isinstance(_OldStyle(), Destination)
        # And confirms the explicit check catches the missing parameter.
        with pytest.raises(TypeError, match="bus_handle"):
            validate_destination_signature(_OldStyle())


class TestDeliveryResult:
    def test_success_carries_ref(self):
        r = DeliveryResult(success=True, ref="discord-msg-123")
        assert r.success is True
        assert r.ref == "discord-msg-123"
        assert r.error is None

    def test_failure_carries_error(self):
        r = DeliveryResult(success=False, error="timeout")
        assert r.success is False
        assert r.error == "timeout"
        assert r.ref is None


class TestSectionSpec:
    def test_static_color_resolves_to_palette_name(self):
        spec = SectionSpec(
            section_id="greeting",
            title="🌅 Morning",
            color="MORNING_GREETING",
            required=True,
            fields=[],
        )
        assert spec.color == "MORNING_GREETING"
        assert spec.required is True

    def test_dynamic_color_carries_expr(self):
        spec = SectionSpec(
            section_id="email",
            title="📬 Inbox",
            color={
                "dynamic": True,
                "expr": "len(email.urgent) > 0",
                "if_true": "EMAIL_URGENT",
                "if_false": "EMAIL_OK",
            },
            required=True,
            fields=[],
        )
        assert isinstance(spec.color, dict)
        assert spec.color["dynamic"] is True


class TestPlaybookRef:
    def test_playbook_ref_round_trips_paths(self, tmp_path):
        ref = PlaybookRef(
            brief_type="morning_brief",
            path=tmp_path / "morning.md",
            sections_required=["greeting", "calendar"],
            sections_optional=["recap"],
            sections_conditional_active=["weekly_digest"],
        )
        assert ref.brief_type == "morning_brief"
        assert ref.sections_required == ["greeting", "calendar"]
