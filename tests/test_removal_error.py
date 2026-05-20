"""Regression tests for incomplete wishlist fetch removal guards."""

import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

import monitor  # noqa: E402
from core import storage  # noqa: E402
from core.models import FetchResult, Item  # noqa: E402
from fetchers import amazon  # noqa: E402


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


@pytest.fixture()
def monitor_state(tmp_path: Path) -> Iterator[None]:
    """Point monitor and storage globals at isolated test state."""
    old_db_path = storage.DB_PATH
    old_fetchers = monitor.FETCHERS.copy()
    old_drop_threshold = monitor.REMOVAL_DROP_PERCENT_THRESHOLD
    old_drop_min = monitor.REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD
    storage.DB_PATH = str(tmp_path / "state.sqlite3")
    storage.ensure_db()
    try:
        yield
    finally:
        storage.DB_PATH = old_db_path
        monitor.FETCHERS.clear()
        monitor.FETCHERS.update(old_fetchers)
        monitor.REMOVAL_DROP_PERCENT_THRESHOLD = old_drop_threshold
        monitor.REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD = old_drop_min


def _run_with_fetcher(
    platform: str, fetcher: Callable[[str, str], FetchResult]
) -> None:
    """Run process_wishlist with a test fetcher."""
    monitor.FETCHERS[platform] = fetcher
    monitor.process_wishlist(
        {
            "platform": platform,
            "name": "Test Wishlist",
            "identifier": "wishlist-1",
            "recipients": ["test@example.com"],
        }
    )


def test_incomplete_fetch_skips_diff_and_persistence(monitor_state: None) -> None:
    """An incomplete non-empty fetch must not delete missing prior rows."""
    previous = [_item("a"), _item("b"), _item("c")]
    _seed_items("fake", "wishlist-1", previous)

    _run_with_fetcher(
        "fake",
        lambda _identifier, _name: FetchResult(
            items=[_item("a")],
            dump_paths=["partial.html"],
            complete=False,
            failure_reason="captcha_persisted",
        ),
    )

    remaining = storage.get_previous_items("fake", "wishlist-1")
    assert set(remaining) == {"a", "b", "c"}
    assert _event_count("removed") == 0


def test_legacy_tuple_fetch_result_is_treated_as_complete() -> None:
    """Legacy fetchers can still return (items, dump_paths) tuples."""
    result = monitor.normalize_fetch_result(([_item("a")], ["dump.html"]))

    assert result.complete
    assert [item.item_id for item in result.items] == ["a"]
    assert result.dump_paths == ["dump.html"]


def test_legacy_none_items_are_normalized_to_empty_list() -> None:
    """Legacy failure tuples with None items normalize without crashing."""
    result = monitor.normalize_fetch_result((None, []))

    assert result.complete
    assert result.items == []
    assert result.dump_paths == []


def test_complete_fetch_persists_legitimate_removal(monitor_state: None) -> None:
    """A complete fetch still records removals when guards do not trigger."""
    previous = [_item("a"), _item("b"), _item("c")]
    _seed_items("fake", "wishlist-1", previous)

    with mock.patch.object(monitor, "send_email"):
        _run_with_fetcher(
            "fake",
            lambda _identifier, _name: FetchResult(
                items=[_item("a"), _item("b")],
                dump_paths=[],
                complete=True,
            ),
        )

    remaining = storage.get_previous_items("fake", "wishlist-1")
    assert set(remaining) == {"a", "b"}
    assert _event_count("removed") == 1


def test_relative_drop_guard_skips_suspicious_complete_fetch(
    monitor_state: None,
) -> None:
    """Large proportional drops are blocked even if marked complete."""
    previous = [_item(str(i)) for i in range(51)]
    _seed_items("fake", "wishlist-1", previous)
    monitor.REMOVAL_DROP_PERCENT_THRESHOLD = 10
    monitor.REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD = 10

    _run_with_fetcher(
        "fake",
        lambda _identifier, _name: FetchResult(
            items=[_item(str(i)) for i in range(43)],
            dump_paths=[],
            complete=True,
        ),
    )

    remaining = storage.get_previous_items("fake", "wishlist-1")
    assert len(remaining) == 51
    assert _event_count("removed") == 0


def test_zero_item_fetch_with_prior_state_preserves_rows(
    monitor_state: None,
) -> None:
    """A complete zero-item fetch keeps prior rows when prior state exists."""
    previous = [_item("a"), _item("b")]
    _seed_items("fake", "wishlist-1", previous)

    _run_with_fetcher(
        "fake",
        lambda _identifier, _name: FetchResult(
            items=[],
            dump_paths=[],
            complete=True,
        ),
    )

    remaining = storage.get_previous_items("fake", "wishlist-1")
    assert set(remaining) == {"a", "b"}
    assert _event_count("removed") == 0


def test_absolute_removal_threshold_preserves_rows(monitor_state: None) -> None:
    """The existing absolute removal threshold still blocks large removals."""
    previous = [_item(str(i)) for i in range(25)]
    _seed_items("fake", "wishlist-1", previous)
    monitor.REMOVAL_DROP_PERCENT_THRESHOLD = 100

    _run_with_fetcher(
        "fake",
        lambda _identifier, _name: FetchResult(
            items=[_item("0")],
            dump_paths=[],
            complete=True,
        ),
    )

    remaining = storage.get_previous_items("fake", "wishlist-1")
    assert len(remaining) == 25
    assert _event_count("removed") == 0


@pytest.fixture()
def amazon_settings(tmp_path: Path) -> Iterator[None]:
    """Configure Amazon fetcher globals for fast isolated tests."""
    old_min_spacing = amazon.AMAZON_MIN_SPACING
    old_page_sleep = amazon.PAGE_SLEEP
    old_captcha_sleep = amazon.CAPTCHA_SLEEP
    old_fail_sleep = amazon.FAIL_SLEEP
    old_retries = amazon.AMAZON_MAX_PAGE_RETRIES
    old_last_fetch = amazon._LAST_AMAZON_FETCH_TS
    old_debug_dir_env = os.environ.get("DEBUG_DIR")
    os.environ["DEBUG_DIR"] = str(tmp_path / "debug_dumps")
    amazon.AMAZON_MIN_SPACING = 0
    amazon.PAGE_SLEEP = 0
    amazon.CAPTCHA_SLEEP = 0
    amazon.FAIL_SLEEP = 0
    amazon.AMAZON_MAX_PAGE_RETRIES = 2
    amazon._LAST_AMAZON_FETCH_TS = 0.0
    try:
        yield
    finally:
        if old_debug_dir_env is None:
            os.environ.pop("DEBUG_DIR", None)
        else:
            os.environ["DEBUG_DIR"] = old_debug_dir_env
        amazon.AMAZON_MIN_SPACING = old_min_spacing
        amazon.PAGE_SLEEP = old_page_sleep
        amazon.CAPTCHA_SLEEP = old_captcha_sleep
        amazon.FAIL_SLEEP = old_fail_sleep
        amazon.AMAZON_MAX_PAGE_RETRIES = old_retries
        amazon._LAST_AMAZON_FETCH_TS = old_last_fetch


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


def test_partial_fetch_after_persistent_captcha_is_incomplete(
    amazon_settings: None,
) -> None:
    """Amazon returns partial items with complete=False after CAPTCHA retries."""
    pages = [
        _page("a", "/page-1"),
        _page("b", "/page-2"),
        "<html><title>Robot Check</title></html>",
        "<html><title>Robot Check</title></html>",
    ]

    with mock.patch.object(amazon, "fetch_page_raw", side_effect=pages):
        result = amazon.fetch_items("wishlist-id", "Wishlist")

    assert not result.complete
    assert "captcha_persisted" in (result.failure_reason or "")
    assert [item.item_id for item in result.items] == ["a", "b"]


def test_fetch_failure_after_retries_is_incomplete(amazon_settings: None) -> None:
    """Amazon request failures produce an incomplete result after retries."""
    first_page = _page("a", "/page-1")
    errors = [
        amazon.AmazonError("503 Service Unavailable"),
        amazon.AmazonError("503 Service Unavailable"),
    ]

    with mock.patch.object(amazon, "fetch_page_raw", side_effect=[first_page, *errors]):
        result = amazon.fetch_items("wishlist-id", "Wishlist")

    assert not result.complete
    assert "fetch_failed_after_retries" in (result.failure_reason or "")
    assert [item.item_id for item in result.items] == ["a"]


def test_normal_pagination_is_complete(amazon_settings: None) -> None:
    """Amazon marks naturally exhausted pagination as complete."""
    pages = [_page("a", "/page-1"), _page("b")]

    with mock.patch.object(amazon, "fetch_page_raw", side_effect=pages):
        result = amazon.fetch_items("wishlist-id", "Wishlist")

    assert result.complete
    assert result.failure_reason is None
    assert [item.item_id for item in result.items] == ["a", "b"]
