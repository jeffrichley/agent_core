"""Tests for the email CLI commands."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agent_core.cli import app

runner = CliRunner()


def make_mock_message(message_id="msg-1", from_="sender@example.com",
                       subject="Test Subject", preview="Hello...",
                       labels=None, timestamp="2026-04-14T12:00:00Z",
                       attachments=None):
    """Create a mock MessageItem."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.from_ = from_
    msg.subject = subject
    msg.preview = preview
    msg.labels = labels or ["received"]
    msg.timestamp = timestamp
    msg.attachments = attachments or []
    return msg


def make_mock_list_response(messages, count=None):
    """Create a mock ListMessagesResponse."""
    resp = MagicMock()
    resp.messages = messages
    resp.count = count or len(messages)
    return resp


class TestEmailUnread:
    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_unread_with_messages(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.list.return_value = make_mock_list_response(
            [make_mock_message(labels=["unread"]), make_mock_message(labels=["unread"])],
            count=2,
        )

        result = runner.invoke(app, ["email", "unread"])
        assert result.exit_code == 0
        assert "2" in result.stdout
        assert "unread" in result.stdout.lower()

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_unread_no_messages(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.list.return_value = make_mock_list_response([], count=0)

        result = runner.invoke(app, ["email", "unread"])
        assert result.exit_code == 0
        assert "no unread" in result.stdout.lower()
