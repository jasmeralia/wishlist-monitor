"""SQLite persistence layer for wishlist items and change events."""

import datetime
import os
import sqlite3
from dataclasses import dataclass

import pytz

from . import run_context
from .logger import get_logger
from .models import Item

logger = get_logger(__name__)

DB_PATH = os.getenv("DB_PATH", "/data/wishlist_state.sqlite3")


@dataclass
class ReaddedItemDiagnostic:
    """Prior removal metadata for an item that was added again in this cycle."""

    item_id: str
    name: str
    removed_ts: str
    removed_run_id: str
    removed_cycle_id: str
    current_run_id: str
    current_cycle_id: str


def _connect() -> sqlite3.Connection:
    """Open (and create parent dirs for) the SQLite database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def now_utc_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.datetime.now(tz=pytz.UTC).isoformat()


def ensure_db() -> None:
    """Create the current-state, event, and observation tables."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                platform TEXT,
                wishlist_id TEXT,
                item_id TEXT,
                name TEXT,
                price_cents INTEGER,
                currency TEXT,
                product_url TEXT,
                image_url TEXT,
                available INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                binding TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (platform, wishlist_id, item_id)
            )
        """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                platform TEXT,
                wishlist_id TEXT,
                event_type TEXT,   -- added|removed|price_change
                item_id TEXT,
                name TEXT,
                from_price_cents INTEGER,
                to_price_cents INTEGER,
                run_id TEXT,
                cycle_id TEXT
            )
        """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS item_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                platform TEXT NOT NULL,
                wishlist_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                name TEXT NOT NULL,
                price_cents INTEGER,
                currency TEXT,
                available INTEGER,
                present INTEGER NOT NULL,
                product_url TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                binding TEXT NOT NULL DEFAULT '',
                run_id TEXT,
                cycle_id TEXT
            )
        """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_observations_item_time
            ON item_observations (
                platform, wishlist_id, item_id, observed_at, id
            )
        """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_observations_cycle
            ON item_observations (platform, wishlist_id, cycle_id)
        """
        )
        _ensure_column(cur, "events", "run_id", "TEXT")
        _ensure_column(cur, "events", "cycle_id", "TEXT")
        _ensure_column(cur, "items", "binding", "TEXT NOT NULL DEFAULT ''")
        con.commit()


def _ensure_column(
    cur: sqlite3.Cursor, table_name: str, column_name: str, column_type: str
) -> None:
    cur.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cur.fetchall()}
    if column_name not in existing:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def get_previous_items(platform: str, wishlist_id: str) -> dict[str, Item]:
    """Return mapping item_id -> Item for existing DB entries."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT item_id, name, price_cents, currency, product_url, image_url, available, binding
            FROM items
            WHERE platform=? AND wishlist_id=?
        """,
            (platform, wishlist_id),
        )
        rows = cur.fetchall()

    out: dict[str, Item] = {}
    for row in rows:
        (
            item_id,
            name,
            price_cents,
            currency,
            product_url,
            image_url,
            available,
            binding,
        ) = row
        out[item_id] = Item(
            item_id=item_id,
            name=name,
            price_cents=price_cents,
            currency=currency,
            product_url=product_url or "",
            image_url=image_url or "",
            available=bool(available),
            binding=binding or "",
        )
    return out


def prune_observations(max_age_days: int) -> int:
    """Delete item_observations rows older than max_age_days; return rows deleted."""
    if max_age_days <= 0:
        return 0

    cutoff = (
        datetime.datetime.now(tz=pytz.UTC) - datetime.timedelta(days=max_age_days)
    ).isoformat()
    with _connect() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM item_observations WHERE observed_at < ?", (cutoff,))
        deleted = cur.rowcount
        con.commit()
    return deleted


def get_previous_item_count(platform: str, wishlist_id: str) -> int:
    """Return the number of stored items for the given platform/wishlist."""
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM items WHERE platform=? AND wishlist_id=?",
            (platform, wishlist_id),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else 0


def find_readded_item_diagnostics(
    platform: str,
    wishlist_id: str,
    added: list[Item],
    current_run_id: str | None = None,
    current_cycle_id: str | None = None,
) -> list[ReaddedItemDiagnostic]:
    """Return added items whose most recent prior event was a removal."""
    event_run_id = current_run_id or run_context.PROCESS_RUN_ID
    event_cycle_id = current_cycle_id or run_context.get_cycle_id()
    diagnostics: list[ReaddedItemDiagnostic] = []

    with _connect() as con:
        cur = con.cursor()
        for item in added:
            cur.execute(
                """
                SELECT event_type, ts, run_id, cycle_id
                FROM events
                WHERE platform=? AND wishlist_id=? AND item_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (platform, wishlist_id, item.item_id),
            )
            row = cur.fetchone()
            if not row or row[0] != "removed":
                continue

            _, removed_ts, removed_run_id, removed_cycle_id = row
            diagnostics.append(
                ReaddedItemDiagnostic(
                    item_id=item.item_id,
                    name=item.name,
                    removed_ts=removed_ts or "",
                    removed_run_id=removed_run_id or "<unknown>",
                    removed_cycle_id=removed_cycle_id or "<unknown>",
                    current_run_id=event_run_id,
                    current_cycle_id=event_cycle_id,
                )
            )

    return diagnostics


def save_items_and_events(
    platform: str,
    wishlist_id: str,
    new_items: list[Item],
    added: list[Item],
    removed: list[Item],
    price_changes: list[tuple[Item, int, int]],
    run_id: str | None = None,
    cycle_id: str | None = None,
) -> None:
    """Persist current items and diff events into SQLite."""
    ts = now_utc_iso()
    event_run_id = run_id or run_context.PROCESS_RUN_ID
    event_cycle_id = cycle_id or run_context.get_cycle_id()
    with _connect() as con:
        cur = con.cursor()

        # Upsert all current items
        for it in new_items:
            cur.execute(
                """
                INSERT INTO items (
                    platform, wishlist_id, item_id, name, price_cents, currency,
                    product_url, image_url, available, first_seen, last_seen, binding
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform, wishlist_id, item_id) DO UPDATE SET
                    name=excluded.name,
                    price_cents=excluded.price_cents,
                    currency=excluded.currency,
                    product_url=excluded.product_url,
                    image_url=excluded.image_url,
                    available=excluded.available,
                    last_seen=excluded.last_seen,
                    binding=excluded.binding
            """,
                (
                    platform,
                    wishlist_id,
                    it.item_id,
                    it.name,
                    it.price_cents,
                    it.currency,
                    it.product_url,
                    it.image_url,
                    1 if it.available else 0,
                    ts,
                    ts,
                    it.binding,
                ),
            )

        # Record every successfully fetched item, even when its state is unchanged.
        cur.executemany(
            """
            INSERT INTO item_observations (
                observed_at, platform, wishlist_id, item_id, name,
                price_cents, currency, available, present, product_url,
                image_url, binding, run_id, cycle_id
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    ts,
                    platform,
                    wishlist_id,
                    it.item_id,
                    it.name,
                    it.price_cents,
                    it.currency,
                    1 if it.available else 0,
                    1,
                    it.product_url,
                    it.image_url,
                    it.binding,
                    event_run_id,
                    event_cycle_id,
                )
                for it in new_items
            ],
        )

        # Absence is distinct from a fetched item whose availability is false.
        cur.executemany(
            """
            INSERT INTO item_observations (
                observed_at, platform, wishlist_id, item_id, name,
                price_cents, currency, available, present, product_url,
                image_url, binding, run_id, cycle_id
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    ts,
                    platform,
                    wishlist_id,
                    it.item_id,
                    it.name,
                    None,
                    it.currency,
                    None,
                    0,
                    it.product_url,
                    it.image_url,
                    it.binding,
                    event_run_id,
                    event_cycle_id,
                )
                for it in removed
            ],
        )

        # Events for added
        for it in added:
            cur.execute(
                """
                INSERT INTO events (
                    ts, platform, wishlist_id, event_type,
                    item_id, name, from_price_cents, to_price_cents, run_id, cycle_id
                )
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    ts,
                    platform,
                    wishlist_id,
                    "added",
                    it.item_id,
                    it.name,
                    None,
                    it.price_cents,
                    event_run_id,
                    event_cycle_id,
                ),
            )

        # Events for price changes
        for it, before, after in price_changes:
            cur.execute(
                """
                INSERT INTO events (
                    ts, platform, wishlist_id, event_type,
                    item_id, name, from_price_cents, to_price_cents, run_id, cycle_id
                )
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    ts,
                    platform,
                    wishlist_id,
                    "price_change",
                    it.item_id,
                    it.name,
                    before,
                    after,
                    event_run_id,
                    event_cycle_id,
                ),
            )

        # Events + deletion for removed
        for it in removed:
            cur.execute(
                """
                INSERT INTO events (
                    ts, platform, wishlist_id, event_type,
                    item_id, name, from_price_cents, to_price_cents, run_id, cycle_id
                )
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    ts,
                    platform,
                    wishlist_id,
                    "removed",
                    it.item_id,
                    it.name,
                    None,
                    None,
                    event_run_id,
                    event_cycle_id,
                ),
            )
            cur.execute(
                "DELETE FROM items WHERE platform=? AND wishlist_id=? AND item_id=?",
                (platform, wishlist_id, it.item_id),
            )

        con.commit()
