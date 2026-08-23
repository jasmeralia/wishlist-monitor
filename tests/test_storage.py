"""Unit tests for SQLite storage behavior."""

import datetime
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import pytz

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
    with storage._connect() as con:
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


def _observations() -> list[
    tuple[str, int | None, int | None, int, str | None, str | None]
]:
    """Return item observation rows relevant to storage assertions."""
    with storage._connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT item_id, price_cents, available, present, run_id, cycle_id
            FROM item_observations
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    return [(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]


def test_save_items_upserts_current_items_and_records_events(
    isolated_db: None,
) -> None:
    """Saving changes upserts current rows and records events and observations."""
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
    observations = _observations()
    assert set(stored) == {"old", "new"}
    assert stored["old"].price_cents == 150
    assert ("added", "new", "change-run", "change-cycle") in events
    assert ("price_change", "old", "change-run", "change-cycle") in events
    assert ("removed", "removed", "change-run", "change-cycle") in events
    assert observations == [
        ("old", 100, 1, 1, "seed-run", "seed-cycle"),
        ("removed", 200, 1, 1, "seed-run", "seed-cycle"),
        ("old", 150, 1, 1, "change-run", "change-cycle"),
        ("new", 300, 1, 1, "change-run", "change-cycle"),
        ("removed", None, None, 0, "change-run", "change-cycle"),
    ]


def test_observations_capture_unchanged_and_unavailable_states(
    isolated_db: None,
) -> None:
    """Every saved poll records price and availability independent of events."""
    available = _item("tracked", 100)
    storage.save_items_and_events(
        "amazon",
        "wishlist",
        [available],
        added=[available],
        removed=[],
        price_changes=[],
        run_id="first-run",
        cycle_id="first-cycle",
    )
    storage.save_items_and_events(
        "amazon",
        "wishlist",
        [available],
        added=[],
        removed=[],
        price_changes=[],
        run_id="second-run",
        cycle_id="second-cycle",
    )

    unavailable = _item("tracked", -1)
    unavailable.available = False
    storage.save_items_and_events(
        "amazon",
        "wishlist",
        [unavailable],
        added=[],
        removed=[],
        price_changes=[],
        run_id="third-run",
        cycle_id="third-cycle",
    )

    assert _observations() == [
        ("tracked", 100, 1, 1, "first-run", "first-cycle"),
        ("tracked", 100, 1, 1, "second-run", "second-cycle"),
        ("tracked", -1, 0, 1, "third-run", "third-cycle"),
    ]


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


def _insert_observation(item_id: str, observed_at: str) -> None:
    with storage._connect() as con:
        con.execute(
            """
            INSERT INTO item_observations (
                observed_at, platform, wishlist_id, item_id, name, present
            )
            VALUES (?, 'amazon', 'wishlist', ?, ?, 1)
            """,
            (observed_at, item_id, f"Item {item_id}"),
        )
        con.commit()


def test_prune_observations_deletes_only_rows_past_retention(
    isolated_db: None,
) -> None:
    """Rows older than max_age_days are deleted; newer rows are kept."""
    old_ts = (
        datetime.datetime.now(tz=pytz.UTC) - datetime.timedelta(days=200)
    ).isoformat()
    _insert_observation("old", old_ts)
    _insert_observation("recent", storage.now_utc_iso())

    deleted = storage.prune_observations(120)

    assert deleted == 1
    assert [row[0] for row in _observations()] == ["recent"]


def test_prune_observations_disabled_when_max_age_not_positive(
    isolated_db: None,
) -> None:
    """A max_age_days of zero or less deletes nothing."""
    old_ts = (
        datetime.datetime.now(tz=pytz.UTC) - datetime.timedelta(days=500)
    ).isoformat()
    _insert_observation("ancient", old_ts)

    assert storage.prune_observations(0) == 0
    assert [row[0] for row in _observations()] == ["ancient"]


def test_ensure_db_adds_columns_to_legacy_schema(tmp_path: Path) -> None:
    """Database initialization migrates missing columns in legacy tables."""
    old_db_path = storage.DB_PATH
    storage.DB_PATH = str(tmp_path / "legacy.sqlite3")
    try:
        with storage._connect() as con:
            con.execute(
                "CREATE TABLE items (platform TEXT, wishlist_id TEXT, item_id TEXT)"
            )
            con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
            con.commit()

        storage.ensure_db()

        with storage._connect() as con:
            item_columns = {row[1] for row in con.execute("PRAGMA table_info(items)")}
            event_columns = {row[1] for row in con.execute("PRAGMA table_info(events)")}
            observation_columns = {
                row[1] for row in con.execute("PRAGMA table_info(item_observations)")
            }
        assert "binding" in item_columns
        assert "compare_at_price_cents" in item_columns
        assert "compare_at_price_cents" in observation_columns
        assert {"run_id", "cycle_id"} <= event_columns
    finally:
        storage.DB_PATH = old_db_path


def test_compare_at_price_cents_round_trips_through_storage(isolated_db: None) -> None:
    """A saved item's compare-at price is persisted and returned by get_previous_items."""
    on_sale = Item(
        "sale-item",
        "Sale Item",
        price_cents=8700,
        compare_at_price_cents=14500,
    )
    storage.save_items_and_events(
        "honeybirdette",
        "us",
        [on_sale],
        added=[on_sale],
        removed=[],
        price_changes=[],
        run_id="run",
        cycle_id="cycle",
    )

    previous = storage.get_previous_items("honeybirdette", "us")

    assert previous["sale-item"].compare_at_price_cents == 14500


def test_missing_compare_at_price_cents_defaults_to_negative_one(
    isolated_db: None,
) -> None:
    """An item without a compare-at price stores/reloads as -1, not NULL."""
    plain = _item("plain", 4500)
    storage.save_items_and_events(
        "amazon",
        "wishlist",
        [plain],
        added=[plain],
        removed=[],
        price_changes=[],
        run_id="run",
        cycle_id="cycle",
    )

    previous = storage.get_previous_items("amazon", "wishlist")

    assert previous["plain"].compare_at_price_cents == -1


def test_get_previous_item_count_reports_rows(isolated_db: None) -> None:
    """Stored item counts are returned for populated and absent wishlists."""
    item = _item("counted")
    storage.save_items_and_events(
        "amazon", "wishlist", [item], [item], [], [], run_id="run", cycle_id="cycle"
    )

    assert storage.get_previous_item_count("amazon", "wishlist") == 1
    assert storage.get_previous_item_count("amazon", "missing") == 0


def test_readded_diagnostics_ignore_latest_non_removal(isolated_db: None) -> None:
    """Items whose latest prior event was not removal are excluded from diagnostics."""
    item = _item("existing")
    storage.save_items_and_events("amazon", "wishlist", [item], [item], [], [])

    assert storage.find_readded_item_diagnostics("amazon", "wishlist", [item]) == []
