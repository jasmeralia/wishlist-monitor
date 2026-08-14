"""Unit tests for wishlist processing and monitor run orchestration."""

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

import monitor
from core.models import FetchResult, Item
from core.storage import ReaddedItemDiagnostic


class _StopDaemon(Exception):
    """Sentinel exception used to terminate daemon tests after a bounded loop."""


def _wishlist(**overrides: object) -> dict[str, object]:
    """Build a valid wishlist configuration with selected overrides."""
    result: dict[str, object] = {
        "platform": "fake",
        "name": "Test List",
        "identifier": "list-id",
        "recipients": ["to@example.com"],
    }
    result.update(overrides)
    return result


@pytest.mark.parametrize(
    "wishlist",
    (
        {"platform": "", "name": "List", "identifier": "id"},
        {"platform": "fake", "name": "", "identifier": "id"},
        {"platform": "fake", "name": "List", "identifier": ""},
    ),
)
def test_process_wishlist_skips_invalid_entries(wishlist: dict[str, str]) -> None:
    """Wishlist entries missing any required identity field are skipped."""
    monitor.process_wishlist(wishlist)


def test_process_wishlist_skips_disabled_and_unknown_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled wishlists and platforms without fetchers are not processed."""
    previous = mock.Mock()
    monkeypatch.setattr(monitor.storage, "get_previous_items", previous)

    monitor.process_wishlist(_wishlist(enabled=False))
    monitor.process_wishlist(_wishlist(platform="unknown"))

    previous.assert_not_called()


def test_process_wishlist_skips_empty_initial_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty complete fetch with no previous state is not persisted."""
    monkeypatch.setitem(
        monitor.FETCHERS,
        "fake",
        lambda _identifier, _name: FetchResult([], [], complete=True),
    )
    monkeypatch.setattr(monitor.storage, "get_previous_items", lambda *_args: {})
    save = mock.Mock()
    monkeypatch.setattr(monitor.storage, "save_items_and_events", save)

    monitor.process_wishlist(_wishlist())

    save.assert_not_called()


def test_process_wishlist_persists_changes_and_sends_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Added, removed, and repriced items are persisted and emailed with diagnostics."""
    removed = Item("removed", "Removed", 200)
    repriced_before = Item("repriced", "Repriced", 100)
    added = Item("added", "Added", 300)
    repriced_after = Item("repriced", "Repriced", 150)
    previous = {"removed": removed, "repriced": repriced_before}
    current = [added, repriced_after]
    fetcher = mock.Mock(return_value=FetchResult(current, ["capture.html"]))
    monkeypatch.setitem(monitor.FETCHERS, "fake", fetcher)
    monkeypatch.setattr(monitor.storage, "get_previous_items", lambda *_args: previous)
    diagnostic = ReaddedItemDiagnostic(
        "added", "Added", "timestamp", "old-run", "old-cycle", "run", "cycle"
    )
    diagnostics = mock.Mock(return_value=[diagnostic])
    save = mock.Mock()
    report = mock.Mock(return_value="<html>report</html>")
    send = mock.Mock()
    monkeypatch.setattr(monitor.storage, "find_readded_item_diagnostics", diagnostics)
    monkeypatch.setattr(monitor.storage, "save_items_and_events", save)
    monkeypatch.setattr(monitor, "build_html_report", report)
    monkeypatch.setattr(monitor, "send_email", send)

    monitor.process_wishlist(_wishlist())

    fetcher.assert_called_once_with("list-id", "Test List")
    save_args = save.call_args.args
    assert save_args[:3] == ("fake", "list-id", current)
    assert [item.item_id for item in save_args[3]] == ["added"]
    assert [item.item_id for item in save_args[4]] == ["removed"]
    assert [(item.item_id, before, after) for item, before, after in save_args[5]] == [
        ("repriced", 100, 150)
    ]
    report.assert_called_once()
    assert report.call_args.kwargs["wishlist_url"] is None
    assert report.call_args.kwargs["readded_diagnostics"] == [diagnostic]
    send.assert_called_once_with(
        "[Wishlist Monitor] Changes detected on Fake for Test List",
        "<html>report</html>",
        None,
        ["to@example.com"],
    )


def test_process_wishlist_persists_unchanged_items_without_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged successful fetches are stored as observations without notifications."""
    item = Item("same", "Same", 100)
    monkeypatch.setitem(
        monitor.FETCHERS,
        "fake",
        lambda _identifier, _name: FetchResult([item], []),
    )
    monkeypatch.setattr(
        monitor.storage, "get_previous_items", lambda *_args: {"same": item}
    )
    monkeypatch.setattr(
        monitor.storage, "find_readded_item_diagnostics", lambda *_args, **_kwargs: []
    )
    save = mock.Mock()
    send = mock.Mock()
    monkeypatch.setattr(monitor.storage, "save_items_and_events", save)
    monkeypatch.setattr(monitor, "send_email", send)

    monitor.process_wishlist(_wishlist())

    save.assert_called_once()
    send.assert_not_called()


def test_process_wishlist_logs_missing_recipients_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detected change without resolved recipients builds but does not send a report."""
    item = Item("new", "New", 100)
    monkeypatch.setitem(
        monitor.FETCHERS,
        "fake",
        lambda _identifier, _name: FetchResult([item], []),
    )
    monkeypatch.setattr(monitor.storage, "get_previous_items", lambda *_args: {})
    monkeypatch.setattr(
        monitor.storage, "find_readded_item_diagnostics", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(monitor.storage, "save_items_and_events", mock.Mock())
    monkeypatch.setattr(monitor, "build_html_report", lambda *_args, **_kwargs: "html")
    monkeypatch.setattr(monitor, "get_global_recipients", list)
    send = mock.Mock()
    monkeypatch.setattr(monitor, "send_email", send)

    monitor.process_wishlist(_wishlist(recipients=[]))

    send.assert_not_called()


def test_jitter_sleep_minutes_uses_bounded_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling sleep applies random jitter to a minimum one-minute base."""
    sleeper = mock.Mock()
    monkeypatch.setattr(monitor.random, "uniform", lambda _low, _high: 0.1)
    monkeypatch.setattr(monitor.time, "sleep", sleeper)

    monitor.jitter_sleep_minutes(0)

    sleeper.assert_called_once_with(66.0)


def test_wishlist_debug_helpers_handle_invalid_and_error_entries() -> None:
    """Debug identifiers represent valid, empty, and malformed wishlist mappings safely."""
    assert monitor._wishlist_debug_id({"platform": "Amazon", "name": "Books"}) == (
        "amazon:Books"
    )
    assert monitor._wishlist_debug_id({}) == "<invalid>"

    class _ExplodingDict(dict[str, object]):
        """Dictionary whose getter raises to exercise defensive debug formatting."""

        def get(self, _key: str, _default: object = None) -> object:
            """Raise an error for every attempted lookup."""
            raise RuntimeError("bad mapping")

    monitor._debug_log_wishlist_order("test", [_ExplodingDict()])


def test_run_once_processes_all_entries_and_contains_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-shot run initializes maintenance and continues after one wishlist fails."""
    wishlists = [_wishlist(name="First"), _wishlist(name="Second")]
    ensure = mock.Mock()
    prune = mock.Mock()
    process = mock.Mock(side_effect=[RuntimeError("boom"), None])
    monkeypatch.setattr(monitor.run_context, "start_cycle", lambda: "cycle")
    monkeypatch.setattr(monitor.storage, "ensure_db", ensure)
    monkeypatch.setattr(monitor, "prune_diagnostics", prune)
    monkeypatch.setattr(monitor, "load_config", lambda: {"wishlists": wishlists})
    monkeypatch.setattr(monitor.random, "shuffle", lambda _items: None)
    monkeypatch.setattr(monitor, "process_wishlist", process)

    assert monitor.run_once() == 0
    assert process.call_count == 2
    ensure.assert_called_once_with()
    prune.assert_called_once_with()


def test_run_daemon_processes_due_entries_and_skips_until_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two bounded daemon iterations process once then honor the wishlist interval."""
    wishlists = [
        {"platform": "fake", "name": "", "identifier": "id"},
        _wishlist(poll_minutes="invalid"),
    ]
    process = mock.Mock()
    sleep = mock.Mock(side_effect=[None, _StopDaemon()])
    prune = mock.Mock()
    monkeypatch.setattr(monitor.storage, "ensure_db", mock.Mock())
    monkeypatch.setattr(monitor, "load_config", lambda: {"wishlists": wishlists})
    monkeypatch.setattr(monitor, "process_wishlist", process)
    monkeypatch.setattr(monitor, "prune_diagnostics", prune)
    monkeypatch.setattr(monitor, "jitter_sleep_minutes", sleep)
    monkeypatch.setattr(monitor.run_context, "start_cycle", lambda: "cycle")
    monkeypatch.setattr(monitor.random, "shuffle", lambda _items: None)
    monkeypatch.setattr(monitor.random, "seed", lambda _seed: None)
    monkeypatch.setattr(monitor.time, "time", lambda: 10000.0)
    monkeypatch.setattr(monitor.time, "time_ns", lambda: 1000000)

    with pytest.raises(_StopDaemon):
        monitor.run_daemon()

    process.assert_called_once_with(wishlists[1])
    prune.assert_called_once_with()


def test_run_daemon_contains_processing_and_loop_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daemon item errors and outer-loop errors are contained until the bounded sleep."""
    valid = _wishlist()
    process = mock.Mock(side_effect=RuntimeError("item failed"))
    load = mock.Mock(
        side_effect=[{"wishlists": [valid]}, RuntimeError("config failed")]
    )
    sleep = mock.Mock(side_effect=[None, _StopDaemon()])
    monkeypatch.setattr(monitor.storage, "ensure_db", mock.Mock())
    monkeypatch.setattr(monitor, "load_config", load)
    monkeypatch.setattr(monitor, "process_wishlist", process)
    monkeypatch.setattr(monitor, "prune_diagnostics", mock.Mock())
    monkeypatch.setattr(monitor, "jitter_sleep_minutes", sleep)
    monkeypatch.setattr(monitor.random, "shuffle", lambda _items: None)
    monkeypatch.setattr(monitor.time, "time", lambda: 1000.0)

    with pytest.raises(_StopDaemon):
        monitor.run_daemon()

    process.assert_called_once_with(valid)
