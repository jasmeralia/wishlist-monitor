"""Unit tests for SQLite storage behavior."""

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import storage
from core.models import Item


def _item(item_id: str, price_cents: int = 100) -> Item:
    """Build a minimal test item."""
    return Item(
        item_id=item_id,
        name=f"Item {item_id}",
        price_cents=price_cents,
        product_url=f"https://example.test/{item_id}",
        image_url=f"https://example.test/{item_id}.jpg",
    )


@pytest.fixture()
def isolated_db(tmp_path: Path) -> Iterator[None]:
    """Point storage at a temporary SQLite database for one test."""
    old_db_path = storage.DB_PATH
    storage.DB_PATH = str(tmp_path / "state.sqlite3")
    storage.ensure_db()
    try:
        yield
    finally:
        storage.DB_PATH = old_db_path


def _events() -> list[tuple[str, str, str | None, str | None]]:
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


def test_save_items_upserts_current_items_and_records_events(
    isolated_db: None,
) -> None:
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
    events = _events()
    assert set(stored) == {"old", "new"}
    assert stored["old"].price_cents == 150
    assert ("added", "new", "change-run", "change-cycle") in events
    assert ("price_change", "old", "change-run", "change-cycle") in events
    assert ("removed", "removed", "change-run", "change-cycle") in events


def test_readded_item_diagnostics_report_latest_prior_removal(
    isolated_db: None,
) -> None:
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

    assert len(diagnostics) == 1
    assert diagnostics[0].item_id == "again"
    assert diagnostics[0].removed_run_id == "remove-run"
    assert diagnostics[0].removed_cycle_id == "remove-cycle"
    assert diagnostics[0].current_run_id == "current-run"
    assert diagnostics[0].current_cycle_id == "current-cycle"
