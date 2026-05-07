"""Unit tests for monitor configuration helpers."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

import monitor  # noqa: E402


class ConfigHelperTests(unittest.TestCase):
    """Tests for config loading and wishlist helper functions."""

    def test_load_config_accepts_valid_config(self) -> None:
        """A config with a non-empty wishlists list is returned."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
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

        self.assertEqual(cfg["wishlists"][0]["name"], "Books")

    def test_load_config_rejects_empty_wishlist_list(self) -> None:
        """An empty wishlists list is invalid."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"wishlists": []}), encoding="utf-8")

            with self.assertRaises(SystemExit):
                monitor.load_config(str(path))

    def test_get_recipients_prefers_non_empty_wishlist_recipients(self) -> None:
        """Per-wishlist recipients override the global recipient list."""
        with mock.patch.object(
            monitor, "get_global_recipients", return_value=["global@example.com"]
        ):
            recipients = monitor.get_recipients_for_wishlist(
                {"recipients": ["  local@example.com  ", "", 42]}
            )

        self.assertEqual(recipients, ["local@example.com"])

    def test_get_recipients_falls_back_to_global_recipients(self) -> None:
        """Missing or empty wishlist recipients fall back to global recipients."""
        with mock.patch.object(
            monitor, "get_global_recipients", return_value=["global@example.com"]
        ):
            recipients = monitor.get_recipients_for_wishlist({"recipients": []})

        self.assertEqual(recipients, ["global@example.com"])

    def test_wishlist_url_normalizes_known_platform_identifiers(self) -> None:
        """Known platform identifiers become user-facing wishlist URLs."""
        self.assertEqual(
            monitor._wishlist_url("amazon", "abc"),
            "https://www.amazon.com/hz/wishlist/ls/abc",
        )
        self.assertEqual(
            monitor._wishlist_url("throne", "person"),
            "https://throne.com/person",
        )

    def test_wishlist_url_leaves_absolute_urls_unchanged(self) -> None:
        """Already absolute identifiers are returned as-is."""
        url = "https://example.com/list"

        self.assertEqual(monitor._wishlist_url("amazon", url), url)


if __name__ == "__main__":
    unittest.main()
