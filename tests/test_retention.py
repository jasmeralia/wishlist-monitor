"""Unit tests for observation history retention policy."""

import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import retention


@pytest.fixture()
def clean_observation_env() -> Iterator[None]:
    """Ensure OBSERVATION_* env vars don't leak between tests."""
    keys = ("OBSERVATION_PRUNE_ENABLED", "OBSERVATION_RETENTION_DAYS")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _fake_prune(calls: list[int]):
    """Build a stand-in for storage.prune_observations that records its calls."""

    def _prune(max_age_days: int) -> int:
        calls.append(max_age_days)
        return 0

    return _prune


def test_prune_item_observations_defaults_to_120_day_retention(
    monkeypatch: pytest.MonkeyPatch,
    clean_observation_env: None,
) -> None:
    """With no env override, pruning defaults to a 120-day retention window."""
    calls: list[int] = []
    monkeypatch.setattr(retention.storage, "prune_observations", _fake_prune(calls))

    retention.prune_item_observations()

    assert calls == [120]


def test_prune_item_observations_respects_custom_retention_days(
    monkeypatch: pytest.MonkeyPatch,
    clean_observation_env: None,
) -> None:
    """OBSERVATION_RETENTION_DAYS overrides the default retention window."""
    os.environ["OBSERVATION_RETENTION_DAYS"] = "30"
    calls: list[int] = []
    monkeypatch.setattr(retention.storage, "prune_observations", _fake_prune(calls))

    retention.prune_item_observations()

    assert calls == [30]


def test_prune_item_observations_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    clean_observation_env: None,
) -> None:
    """OBSERVATION_PRUNE_ENABLED=false skips pruning entirely."""
    os.environ["OBSERVATION_PRUNE_ENABLED"] = "false"
    calls: list[int] = []
    monkeypatch.setattr(retention.storage, "prune_observations", _fake_prune(calls))

    retention.prune_item_observations()

    assert not calls


@pytest.fixture()
def clean_retention_env() -> Iterator[None]:
    """Restore file-retention environment variables after each test."""
    keys = (
        "DEBUG_DUMP_PRUNE_ENABLED",
        "DEBUG_DUMP_MAX_AGE_DAYS",
        "DEBUG_DUMP_MAX_FILES",
        "DEBUG_DIR",
        "LOG_PRUNE_ENABLED",
        "LOG_MAX_AGE_DAYS",
        "LOG_MAX_FILES",
        "LOG_FILE",
    )
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.parametrize("value", ("1", "true", " YES ", "on"))
def test_env_bool_recognizes_true_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Supported truthy environment spellings are accepted case-insensitively."""
    monkeypatch.setenv("FLAG", value)

    assert retention.env_bool("FLAG", False)


def test_env_bool_uses_default_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing booleans use their default while unknown text is false."""
    monkeypatch.delenv("FLAG", raising=False)
    assert retention.env_bool("FLAG", True)
    monkeypatch.setenv("FLAG", "maybe")
    assert not retention.env_bool("FLAG", True)


def test_env_int_handles_missing_invalid_and_bounded_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integer settings use defaults on errors and enforce their lower bound."""
    monkeypatch.delenv("COUNT", raising=False)
    assert retention.env_int("COUNT", 7, 2) == 7
    monkeypatch.setenv("COUNT", "invalid")
    assert retention.env_int("COUNT", 7, 2) == 7
    monkeypatch.setenv("COUNT", "-10")
    assert retention.env_int("COUNT", 7, 2) == 2
    monkeypatch.setenv("COUNT", "9")
    assert retention.env_int("COUNT", 7, 2) == 9


def _touch_with_age(path: Path, age_days: int) -> None:
    """Create a file and set its modification time to a controlled age."""
    path.write_text(path.name, encoding="utf-8")
    modified = time.time() - age_days * 86400
    os.utime(path, (modified, modified))


def test_prune_debug_dumps_applies_age_and_count_limits(
    tmp_path: Path, clean_retention_env: None
) -> None:
    """Debug dump pruning removes old files then trims oldest excess files."""
    old = tmp_path / "amazon_old.html"
    first = tmp_path / "amazon_first.html"
    second = tmp_path / "throne_second.html"
    newest = tmp_path / "amazon_newest.html"
    ignored = tmp_path / "other.html"
    for path, age in ((old, 20), (first, 3), (second, 2), (newest, 1), (ignored, 30)):
        _touch_with_age(path, age)
    os.environ["DEBUG_DIR"] = str(tmp_path)
    os.environ["DEBUG_DUMP_MAX_AGE_DAYS"] = "7"
    os.environ["DEBUG_DUMP_MAX_FILES"] = "2"

    retention.prune_debug_dumps()

    assert not old.exists()
    assert not first.exists()
    assert second.exists()
    assert newest.exists()
    assert ignored.exists()


def test_prune_debug_dumps_handles_disabled_missing_and_non_directory(
    tmp_path: Path, clean_retention_env: None
) -> None:
    """Dump pruning safely skips disabled, absent, and non-directory locations."""
    os.environ["DEBUG_DUMP_PRUNE_ENABLED"] = "false"
    os.environ["DEBUG_DIR"] = str(tmp_path / "disabled")
    retention.prune_debug_dumps()

    os.environ["DEBUG_DUMP_PRUNE_ENABLED"] = "true"
    retention.prune_debug_dumps()

    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x", encoding="utf-8")
    os.environ["DEBUG_DIR"] = str(file_path)
    retention.prune_debug_dumps()

    assert file_path.exists()


def test_prune_log_files_excludes_current_and_applies_limits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    clean_retention_env: None,
) -> None:
    """Log pruning excludes the active log while trimming old and excess files."""
    configured = tmp_path / "wishlist.log"
    current = tmp_path / "wishlist_current.log"
    old = tmp_path / "wishlist_old.log"
    excess = tmp_path / "wishlist_excess.log.1"
    newest = tmp_path / "wishlist_new.log"
    for path, age in ((current, 30), (old, 20), (excess, 2), (newest, 1)):
        _touch_with_age(path, age)
    os.environ["LOG_FILE"] = str(configured)
    os.environ["LOG_MAX_AGE_DAYS"] = "7"
    os.environ["LOG_MAX_FILES"] = "1"
    monkeypatch.setattr(
        retention.run_context, "get_log_file_path", lambda: str(current)
    )

    retention.prune_log_files()

    assert current.exists()
    assert not old.exists()
    assert not excess.exists()
    assert newest.exists()


def test_prune_log_files_skips_disabled_or_missing_directory(
    tmp_path: Path, clean_retention_env: None
) -> None:
    """Log pruning safely handles disabled pruning and missing log directories."""
    os.environ["LOG_PRUNE_ENABLED"] = "false"
    retention.prune_log_files()
    os.environ["LOG_PRUNE_ENABLED"] = "true"
    os.environ["LOG_FILE"] = str(tmp_path / "missing" / "monitor")

    retention.prune_log_files()


class _BrokenPath:
    """Minimal path stand-in for exercising defensive filesystem branches."""

    def __init__(self, mtime: float = 0.0, *, stat_error: bool = False) -> None:
        self.mtime = mtime
        self.stat_error = stat_error

    def stat(self) -> SimpleNamespace:
        """Return a fake stat result or raise the configured error."""
        if self.stat_error:
            raise OSError("stat failed")
        return SimpleNamespace(st_mtime=self.mtime)

    def unlink(self) -> None:
        """Always fail deletion to cover defensive cleanup handling."""
        raise OSError("unlink failed")

    def __str__(self) -> str:
        """Return a stable display name for logging."""
        return "broken-path"


def test_prune_files_tolerates_stat_and_unlink_failures() -> None:
    """Filesystem races during stat and deletion are logged without escaping."""
    now = time.time()
    paths = [
        _BrokenPath(stat_error=True),
        _BrokenPath(now - 10 * 86400),
        _BrokenPath(now),
        _BrokenPath(now + 1),
    ]

    retention._prune_files("test", paths, max_age_days=7, max_files=1)

    assert retention._mtime(_BrokenPath(stat_error=True)) == 0.0


def test_prune_item_observations_logs_deletions(
    monkeypatch: pytest.MonkeyPatch,
    clean_observation_env: None,
) -> None:
    """Observation pruning logs when stale rows were actually removed."""
    monkeypatch.setattr(retention.storage, "prune_observations", lambda _days: 3)
    info = mock.Mock()
    monkeypatch.setattr(retention.logger, "info", info)

    retention.prune_item_observations()

    info.assert_called_once()


def test_prune_diagnostics_invokes_all_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The diagnostics orchestrator invokes dump, log, and observation pruning."""
    calls: list[str] = []
    monkeypatch.setattr(retention, "prune_debug_dumps", lambda: calls.append("dumps"))
    monkeypatch.setattr(retention, "prune_log_files", lambda: calls.append("logs"))
    monkeypatch.setattr(
        retention, "prune_item_observations", lambda: calls.append("observations")
    )

    retention.prune_diagnostics()

    assert calls == ["dumps", "logs", "observations"]
