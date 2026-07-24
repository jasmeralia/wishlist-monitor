"""Unit tests for monitor configuration helpers."""

import json
import os
import tempfile
from pathlib import Path

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

import monitor


def test_load_config_accepts_valid_config(tmp_path: Path) -> None:
    """A config with a non-empty wishlists list is returned."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "wishlists": [
                    {
                        "platform": "amazon",
                        "name": "Books",
                        "identifier": "abc",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cfg = monitor.load_config(str(path))

    assert cfg["wishlists"][0]["name"] == "Books"


def test_load_config_rejects_empty_wishlist_list(tmp_path: Path) -> None:
    """An empty wishlists list is invalid."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"wishlists": []}), encoding="utf-8")

    with pytest.raises(SystemExit):
        monitor.load_config(str(path))


def test_get_recipients_prefers_non_empty_wishlist_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-wishlist recipients override the global recipient list."""
    monkeypatch.setattr(
        monitor, "get_global_recipients", lambda: ["global@example.com"]
    )

    recipients = monitor.get_recipients_for_wishlist(
        {"recipients": ["  local@example.com  ", "", 42]}
    )

    assert recipients == ["local@example.com"]


def test_get_recipients_falls_back_to_global_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing or empty wishlist recipients fall back to global recipients."""
    monkeypatch.setattr(
        monitor, "get_global_recipients", lambda: ["global@example.com"]
    )

    recipients = monitor.get_recipients_for_wishlist({"recipients": []})

    assert recipients == ["global@example.com"]


def test_wishlist_url_normalizes_known_platform_identifiers() -> None:
    """Known platform identifiers become user-facing wishlist URLs."""
    assert (
        monitor._wishlist_url("amazon", "abc")
        == "https://www.amazon.com/hz/wishlist/ls/abc"
    )
    assert monitor._wishlist_url("throne", "person") == "https://throne.com/person"


def test_wishlist_url_leaves_absolute_urls_unchanged() -> None:
    """Already absolute identifiers are returned as-is."""
    url = "https://example.com/list"

    assert monitor._wishlist_url("amazon", url) == url
