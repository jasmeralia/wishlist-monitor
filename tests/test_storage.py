"""Unit tests for SQLite storage behavior."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import storage  # noqa: E402
from core.models import Item  # noqa: E402


def _item(item_id: str, price_cents: int = 100) -> Item:
    """Build a minimal test item."""
    return Item(
        item_id=item_id,
        name=f"Item {item_id}",
        price_cents=price_cents,
        product_url=f"https://example.test/{item_id}",
        image_url=f"https://example.test/{item_id}.jpg",
    )


class StorageTests(unittest.TestCase):
    """Tests for item and event persistence."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = storage.DB_PATH
        storage.DB_PATH = str(Path(self.tmp.name) / "state.sqlite3")
        storage.ensure_db()

    def tearDown(self) -> None:
        storage.DB_PATH = self.old_db_path

    def _events(self) -> list[tuple[str, str, str | None, str | None]]:
        """Return event rows relevant to storage assertions."""
        with sqlite3.connect(storage.DB_PATH) as con:
            cur = con.cursor()
            cur.execute(
                """
                SELECT event_type, item_id, run_id, cycle_id
                FROM events
                ORDER BY id
                """
            )
            rows = cur.fetchall()
        return [(row[0], row[1], row[2], row[3]) for row in rows]

    def test_save_items_upserts_current_items_and_records_events(self) -> None:
        """Saving changes upserts current rows and records event metadata."""
        existing = [_item("old", 100), _item("removed", 200)]
        storage.save_items_and_events(
            "amazon",
            "wishlist",
            existing,
            added=existing,
            removed=[],
            price_changes=[],
            run_id="seed-run",
            cycle_id="seed-cycle",
        )

        current = [_item("old", 150), _item("new", 300)]
        storage.save_items_and_events(
            "amazon",
            "wishlist",
            current,
            added=[current[1]],
            removed=[existing[1]],
            price_changes=[(current[0], 100, 150)],
            run_id="change-run",
            cycle_id="change-cycle",
        )

        stored = storage.get_previous_items("amazon", "wishlist")
        self.assertEqual(set(stored), {"old", "new"})
        self.assertEqual(stored["old"].price_cents, 150)
        self.assertIn(("added", "new", "change-run", "change-cycle"), self._events())
        self.assertIn(
            ("price_change", "old", "change-run", "change-cycle"), self._events()
        )
        self.assertIn(
            ("removed", "removed", "change-run", "change-cycle"), self._events()
        )

    def test_readded_item_diagnostics_report_latest_prior_removal(self) -> None:
        """Re-added diagnostics include the most recent removal metadata."""
        item = _item("again")
        storage.save_items_and_events(
            "amazon",
            "wishlist",
            [item],
            added=[item],
            removed=[],
            price_changes=[],
            run_id="seed-run",
            cycle_id="seed-cycle",
        )
        storage.save_items_and_events(
            "amazon",
            "wishlist",
            [],
            added=[],
            removed=[item],
            price_changes=[],
            run_id="remove-run",
            cycle_id="remove-cycle",
        )

        diagnostics = storage.find_readded_item_diagnostics(
            "amazon",
            "wishlist",
            [item],
            current_run_id="current-run",
            current_cycle_id="current-cycle",
        )

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].item_id, "again")
        self.assertEqual(diagnostics[0].removed_run_id, "remove-run")
        self.assertEqual(diagnostics[0].removed_cycle_id, "remove-cycle")
        self.assertEqual(diagnostics[0].current_run_id, "current-run")
        self.assertEqual(diagnostics[0].current_cycle_id, "current-cycle")


if __name__ == "__main__":
    unittest.main()
