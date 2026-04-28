"""Tests for the credential data models."""

from agent_core_credentials.models import Credential, CredentialSummary


class TestCredential:
    def test_construct_with_required_fields(self) -> None:
        cred = Credential(service="x", username="u", password="p")
        assert cred.service == "x"
        assert cred.username == "u"
        assert cred.password == "p"
        assert cred.url == ""
        assert cred.notes == ""

    def test_construct_with_all_fields(self) -> None:
        cred = Credential(
            service="x",
            username="u",
            password="p",
            url="https://x",
            notes="hi",
        )
        assert cred.url == "https://x"
        assert cred.notes == "hi"


class TestCredentialSummary:
    def test_construct_with_required_fields(self) -> None:
        s = CredentialSummary(service="x", username="u")
        assert s.service == "x"
        assert s.username == "u"
        assert s.url == ""

    def test_construct_with_url(self) -> None:
        s = CredentialSummary(service="x", username="u", url="https://x")
        assert s.url == "https://x"
