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


class TestEmailCheck:
    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_check_default(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        msgs = [
            make_mock_message("msg-1", "alice@example.com", "Hello", labels=["unread"]),
            make_mock_message("msg-2", "bob@example.com", "Meeting", labels=["received"]),
        ]
        client.inboxes.messages.list.return_value = make_mock_list_response(msgs)

        result = runner.invoke(app, ["email", "check"])
        assert result.exit_code == 0
        assert "alice@example.com" in result.stdout
        assert "bob@example.com" in result.stdout
        assert "Hello" in result.stdout

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_check_with_limit(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.list.return_value = make_mock_list_response([])

        result = runner.invoke(app, ["email", "check", "--limit", "20"])
        assert result.exit_code == 0
        call_kwargs = client.inboxes.messages.list.call_args
        assert call_kwargs[1].get("limit") == 20 or call_kwargs.kwargs.get("limit") == 20

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_check_unread_only(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.list.return_value = make_mock_list_response([])

        result = runner.invoke(app, ["email", "check", "--unread"])
        assert result.exit_code == 0
        call_kwargs = client.inboxes.messages.list.call_args
        assert "unread" in str(call_kwargs)

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_check_empty_inbox(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.list.return_value = make_mock_list_response([])

        result = runner.invoke(app, ["email", "check"])
        assert result.exit_code == 0
        assert "no messages" in result.stdout.lower()


def make_mock_full_message(message_id="msg-1", from_="sender@example.com",
                            to=None, subject="Test Subject", text="Hello body",
                            labels=None, timestamp="2026-04-14T12:00:00Z",
                            attachments=None, cc=None):
    """Create a mock full Message (from .get())."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.from_ = from_
    msg.to = to or ["pepper_ai@agentmail.to"]
    msg.cc = cc
    msg.subject = subject
    msg.text = text
    msg.html = None
    msg.labels = labels or ["received"]
    msg.timestamp = timestamp
    msg.attachments = attachments or []
    return msg


class TestEmailRead:
    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_read_message(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.get.return_value = make_mock_full_message(
            from_="alice@example.com",
            subject="Important",
            text="This is the full body.",
        )

        result = runner.invoke(app, ["email", "read", "msg-1"])
        assert result.exit_code == 0
        assert "alice@example.com" in result.stdout
        assert "Important" in result.stdout
        assert "This is the full body." in result.stdout

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_read_with_attachments(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        att1 = MagicMock()
        att1.filename = "file1.pdf"
        att2 = MagicMock()
        att2.filename = "image.png"
        client.inboxes.messages.get.return_value = make_mock_full_message(
            attachments=[att1, att2],
        )

        result = runner.invoke(app, ["email", "read", "msg-1"])
        assert result.exit_code == 0
        assert "file1.pdf" in result.stdout
        assert "image.png" in result.stdout

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_read_not_found(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.inboxes.messages.get.side_effect = Exception("Not found")

        result = runner.invoke(app, ["email", "read", "bad-id"])
        assert result.exit_code == 1


class TestEmailSend:
    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_send_basic(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        resp = MagicMock()
        resp.message_id = "sent-1"
        resp.thread_id = "thread-1"
        client.inboxes.messages.send.return_value = resp

        result = runner.invoke(app, ["email", "send", "jeff@gmail.com", "Hello", "Body text"])
        assert result.exit_code == 0
        assert "sent" in result.stdout.lower()
        client.inboxes.messages.send.assert_called_once()

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_send_with_cc(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client
        resp = MagicMock()
        resp.message_id = "sent-2"
        client.inboxes.messages.send.return_value = resp

        result = runner.invoke(app, ["email", "send", "jeff@gmail.com", "Hello", "Body", "--cc", "other@example.com"])
        assert result.exit_code == 0
        call_kwargs = client.inboxes.messages.send.call_args[1]
        assert call_kwargs.get("cc") == "other@example.com"

    @patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to")
    @patch("agent_core.email.cli.get_client")
    def test_send_dry_run(self, mock_client_fn, mock_inbox):
        client = MagicMock()
        mock_client_fn.return_value = client

        result = runner.invoke(app, ["email", "send", "jeff@gmail.com", "Hello", "Body", "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.stdout.lower()
        client.inboxes.messages.send.assert_not_called()

    def test_send_body_file(self, tmp_path):
        body_file = tmp_path / "body.txt"
        body_file.write_text("Body from file", encoding="utf-8")

        with patch("agent_core.email.cli.get_inbox_id", return_value="test@agentmail.to"), \
             patch("agent_core.email.cli.get_client") as mock_client_fn:
            client = MagicMock()
            mock_client_fn.return_value = client
            resp = MagicMock()
            resp.message_id = "sent-3"
            client.inboxes.messages.send.return_value = resp

            result = runner.invoke(app, [
                "email", "send", "jeff@gmail.com", "Subject",
                "--body-file", str(body_file),
            ])
            assert result.exit_code == 0
            call_kwargs = client.inboxes.messages.send.call_args[1]
            assert "Body from file" in (call_kwargs.get("text", "") or call_kwargs.get("html", ""))
