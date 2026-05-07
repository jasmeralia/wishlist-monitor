"""Regression tests for incomplete wishlist fetch removal guards."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

import monitor
from core import storage
from core.models import FetchResult, Item
from fetchers import amazon


def _item(item_id: str) -> Item:
    """Build a minimal test item."""
    return Item(item_id=item_id, name=f"Item {item_id}", price_cents=100)


def _seed_items(platform: str, wishlist_id: str, items: list[Item]) -> None:
    """Store initial items as prior state."""
    storage.save_items_and_events(
        platform,
        wishlist_id,
        items,
        added=items,
        removed=[],
        price_changes=[],
        run_id="seed-run",
        cycle_id="seed-cycle",
    )


def _event_count(event_type: str) -> int:
    """Return the number of stored events with the given type."""
    with sqlite3.connect(storage.DB_PATH) as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM events WHERE event_type=?", (event_type,))
        row = cur.fetchone()
    return int(row[0])


class MonitorRemovalGuardTests(unittest.TestCase):
    """Tests for monitor-level guards that prevent false removals."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = storage.DB_PATH
        self.old_fetchers = monitor.FETCHERS.copy()
        self.old_drop_threshold = monitor.REMOVAL_DROP_PERCENT_THRESHOLD
        self.old_drop_min = monitor.REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD
        storage.DB_PATH = str(Path(self.tmp.name) / "state.sqlite3")
        storage.ensure_db()

    def tearDown(self) -> None:
        storage.DB_PATH = self.old_db_path
        monitor.FETCHERS.clear()
        monitor.FETCHERS.update(self.old_fetchers)
        monitor.REMOVAL_DROP_PERCENT_THRESHOLD = self.old_drop_threshold
        monitor.REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD = self.old_drop_min

    def _run_with_fetcher(
        self, platform: str, fetcher: Callable[[str, str], FetchResult]
    ) -> None:
        monitor.FETCHERS[platform] = fetcher
        monitor.process_wishlist(
            {
                "platform": platform,
                "name": "Test Wishlist",
                "identifier": "wishlist-1",
                "recipients": ["test@example.com"],
            }
        )

    def test_incomplete_fetch_skips_diff_and_persistence(self) -> None:
        """An incomplete non-empty fetch must not delete missing prior rows."""
        previous = [_item("a"), _item("b"), _item("c")]
        _seed_items("fake", "wishlist-1", previous)

        self._run_with_fetcher(
            "fake",
            lambda _identifier, _name: FetchResult(
                items=[_item("a")],
                dump_paths=["partial.html"],
                complete=False,
                failure_reason="captcha_persisted",
            ),
        )

        remaining = storage.get_previous_items("fake", "wishlist-1")
        self.assertEqual(set(remaining), {"a", "b", "c"})
        self.assertEqual(_event_count("removed"), 0)

    def test_complete_fetch_persists_legitimate_removal(self) -> None:
        """A complete fetch still records removals when guards do not trigger."""
        previous = [_item("a"), _item("b"), _item("c")]
        _seed_items("fake", "wishlist-1", previous)

        with mock.patch.object(monitor, "send_email"):
            self._run_with_fetcher(
                "fake",
                lambda _identifier, _name: FetchResult(
                    items=[_item("a"), _item("b")],
                    dump_paths=[],
                    complete=True,
                ),
            )

        remaining = storage.get_previous_items("fake", "wishlist-1")
        self.assertEqual(set(remaining), {"a", "b"})
        self.assertEqual(_event_count("removed"), 1)

    def test_relative_drop_guard_skips_suspicious_complete_fetch(self) -> None:
        """Large proportional drops are blocked even if marked complete."""
        previous = [_item(str(i)) for i in range(51)]
        _seed_items("fake", "wishlist-1", previous)
        monitor.REMOVAL_DROP_PERCENT_THRESHOLD = 10
        monitor.REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD = 10

        self._run_with_fetcher(
            "fake",
            lambda _identifier, _name: FetchResult(
                items=[_item(str(i)) for i in range(43)],
                dump_paths=[],
                complete=True,
            ),
        )

        remaining = storage.get_previous_items("fake", "wishlist-1")
        self.assertEqual(len(remaining), 51)
        self.assertEqual(_event_count("removed"), 0)


class AmazonFetchResultTests(unittest.TestCase):
    """Tests for Amazon fetch completion status."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_debug_dir = amazon.DEBUG_DIR
        self.old_min_spacing = amazon.AMAZON_MIN_SPACING
        self.old_page_sleep = amazon.PAGE_SLEEP
        self.old_captcha_sleep = amazon.CAPTCHA_SLEEP
        self.old_retries = amazon.AMAZON_MAX_PAGE_RETRIES
        self.old_last_fetch = amazon._LAST_AMAZON_FETCH_TS
        amazon.DEBUG_DIR = Path(self.tmp.name)
        amazon.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        amazon.AMAZON_MIN_SPACING = 0
        amazon.PAGE_SLEEP = 0
        amazon.CAPTCHA_SLEEP = 0
        amazon.AMAZON_MAX_PAGE_RETRIES = 2
        amazon._LAST_AMAZON_FETCH_TS = 0.0

    def tearDown(self) -> None:
        amazon.DEBUG_DIR = self.old_debug_dir
        amazon.AMAZON_MIN_SPACING = self.old_min_spacing
        amazon.PAGE_SLEEP = self.old_page_sleep
        amazon.CAPTCHA_SLEEP = self.old_captcha_sleep
        amazon.AMAZON_MAX_PAGE_RETRIES = self.old_retries
        amazon._LAST_AMAZON_FETCH_TS = self.old_last_fetch

    @staticmethod
    def _page(item_id: str, next_path: str | None = None) -> str:
        """Build a minimal Amazon wishlist page."""
        token = ""
        if next_path is not None:
            token = (
                '<form class="scroll-state">'
                f'<input class="showMoreUrl" value="{next_path}">'
                "</form>"
            )
        return (
            "<html><body>"
            f'<li class="awl-item-wrapper" id="{item_id}">'
            f"<h3>Item {item_id}</h3>"
            "</li>"
            f"{token}"
            "</body></html>"
        )

    def test_partial_fetch_after_persistent_captcha_is_incomplete(self) -> None:
        """Amazon returns partial items with complete=False after CAPTCHA retries."""
        pages = [
            self._page("a", "/page-1"),
            self._page("b", "/page-2"),
            "<html><title>Robot Check</title></html>",
            "<html><title>Robot Check</title></html>",
        ]

        with mock.patch.object(amazon, "fetch_page_raw", side_effect=pages):
            result = amazon.fetch_items("wishlist-id", "Wishlist")

        self.assertFalse(result.complete)
        self.assertIn("captcha_persisted", result.failure_reason or "")
        self.assertEqual([item.item_id for item in result.items], ["a", "b"])

    def test_normal_pagination_is_complete(self) -> None:
        """Amazon marks naturally exhausted pagination as complete."""
        pages = [self._page("a", "/page-1"), self._page("b")]

        with mock.patch.object(amazon, "fetch_page_raw", side_effect=pages):
            result = amazon.fetch_items("wishlist-id", "Wishlist")

        self.assertTrue(result.complete)
        self.assertIsNone(result.failure_reason)
        self.assertEqual([item.item_id for item in result.items], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
