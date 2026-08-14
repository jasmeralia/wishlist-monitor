"""Unit tests for SMTP email delivery."""

import os
import tempfile
from email import message_from_string
from unittest import mock

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import emailer


def test_get_global_recipients_parses_mixed_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global recipients are stripped and split on commas and semicolons."""
    monkeypatch.setattr(
        emailer,
        "_EMAIL_TO_RAW",
        " one@example.com; two@example.com, ,three@example.com ",
    )

    assert emailer.get_global_recipients() == [
        "one@example.com",
        "two@example.com",
        "three@example.com",
    ]


def test_get_global_recipients_handles_empty_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty global-recipient setting produces an empty list."""
    monkeypatch.setattr(emailer, "_EMAIL_TO_RAW", "")

    assert emailer.get_global_recipients() == []


def test_send_email_skips_missing_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email delivery is skipped when no recipients are supplied."""
    smtp = mock.Mock()
    monkeypatch.setattr(emailer.smtplib, "SMTP", smtp)

    emailer.send_email("Subject", "<p>Body</p>", None, [])

    smtp.assert_not_called()


def test_send_email_skips_incomplete_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email delivery is skipped when sender or SMTP host is absent."""
    smtp = mock.Mock()
    monkeypatch.setattr(emailer, "EMAIL_FROM", "")
    monkeypatch.setattr(emailer, "SMTP_HOST", "")
    monkeypatch.setattr(emailer.smtplib, "SMTP", smtp)

    emailer.send_email("Subject", "<p>Body</p>", "Body", ["to@example.com"])

    smtp.assert_not_called()


def test_send_email_uses_starttls_and_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-SSL SMTP starts TLS, authenticates, sends both parts, and quits."""
    server = mock.Mock()
    smtp = mock.Mock(return_value=server)
    monkeypatch.setattr(emailer, "EMAIL_FROM", "from@example.com")
    monkeypatch.setattr(emailer, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(emailer, "SMTP_PORT", 2525)
    monkeypatch.setattr(emailer, "SMTP_USER", "user")
    monkeypatch.setattr(emailer, "SMTP_PASS", "secret")
    monkeypatch.setattr(emailer, "SMTP_USE_SSL", False)
    monkeypatch.setattr(emailer.smtplib, "SMTP", smtp)

    emailer.send_email("Subject", "<p>Body</p>", None, ["to@example.com"])

    smtp.assert_called_once_with("smtp.example.com", 2525)
    server.starttls.assert_called_once_with()
    server.login.assert_called_once_with("user", "secret")
    args = server.sendmail.call_args.args
    assert args[:2] == ("from@example.com", ["to@example.com"])
    message = message_from_string(args[2])
    text_part, html_part = message.get_payload()
    assert (
        "HTML capable email client required"
        in text_part.get_payload(decode=True).decode()
    )
    assert "<p>Body</p>" in html_part.get_payload(decode=True).decode()
    assert "Content-Type: text/html" in args[2]
    server.quit.assert_called_once_with()


def test_send_email_uses_ssl_without_optional_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSL SMTP bypasses STARTTLS and login when no username is configured."""
    server = mock.Mock()
    server.quit.side_effect = OSError("connection already closed")
    smtp_ssl = mock.Mock(return_value=server)
    monkeypatch.setattr(emailer, "EMAIL_FROM", "from@example.com")
    monkeypatch.setattr(emailer, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(emailer, "SMTP_PORT", 465)
    monkeypatch.setattr(emailer, "SMTP_USER", "")
    monkeypatch.setattr(emailer, "SMTP_USE_SSL", True)
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", smtp_ssl)

    emailer.send_email("Subject", "<p>Body</p>", "Plain", ["to@example.com"])

    smtp_ssl.assert_called_once_with("smtp.example.com", 465)
    server.starttls.assert_not_called()
    server.login.assert_not_called()
    server.sendmail.assert_called_once()
    server.quit.assert_called_once_with()
