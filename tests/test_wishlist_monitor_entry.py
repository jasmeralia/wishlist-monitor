"""Tests for the ``python -m wishlist_monitor`` entry point."""

from unittest import mock

import wishlist_monitor.__main__ as entry


def test_main_runs_once_when_mode_is_once() -> None:
    """``main()`` should delegate to ``run_once()`` when MODE is once."""
    with (
        mock.patch.object(entry, "MODE", "once"),
        mock.patch.object(entry, "run_once", return_value=0) as run_once,
        mock.patch.object(entry, "run_daemon") as run_daemon,
    ):
        try:
            entry.main()
        except SystemExit as exc:
            assert exc.code == 0
        else:
            raise AssertionError("expected SystemExit(0)")
        run_once.assert_called_once()
        run_daemon.assert_not_called()


def test_main_runs_daemon_when_mode_is_daemon() -> None:
    """``main()`` should call ``run_daemon()`` when MODE is not once."""
    with (
        mock.patch.object(entry, "MODE", "daemon"),
        mock.patch.object(entry, "run_once") as run_once,
        mock.patch.object(entry, "run_daemon") as run_daemon,
    ):
        entry.main()
        run_daemon.assert_called_once()
        run_once.assert_not_called()
