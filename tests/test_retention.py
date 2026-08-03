"""Unit tests for observation history retention policy."""

import os
import tempfile
from collections.abc import Iterator

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
