"""Unit tests for process and cycle run identifiers."""

import importlib
import os
import tempfile

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import run_context


def test_start_cycle_creates_independent_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each cycle receives a new identifier that becomes the active value."""
    generated = iter(("cycle-one", "cycle-two"))
    monkeypatch.setattr(run_context, "_new_run_id", lambda: next(generated))

    first = run_context.start_cycle()
    second = run_context.start_cycle()

    assert first == "cycle-one"
    assert second == "cycle-two"
    assert run_context.get_cycle_id() == "cycle-two"


def test_new_run_id_combines_timestamp_and_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated identifiers combine a UTC timestamp with a short UUID suffix."""
    fake_uuid = type("FakeUuid", (), {"hex": "1234567890abcdef"})()
    monkeypatch.setattr(run_context, "_utc_timestamp", lambda: "20260102T030405Z")
    monkeypatch.setattr(run_context.uuid, "uuid4", lambda: fake_uuid)

    assert run_context._new_run_id() == "20260102T030405Z-12345678"


@pytest.mark.parametrize(
    ("configured", "expected"),
    (
        ("/tmp/monitor.log", "/tmp/monitor_run-id.log"),
        ("/tmp/monitor", "/tmp/monitor_run-id.log"),
    ),
)
def test_get_log_file_path_adds_run_id(
    monkeypatch: pytest.MonkeyPatch, configured: str, expected: str
) -> None:
    """Log paths gain the process identifier whether or not a suffix is configured."""
    monkeypatch.setenv("LOG_FILE", configured)
    monkeypatch.setattr(run_context, "PROCESS_RUN_ID", "run-id")

    assert run_context.get_log_file_path() == expected


def test_run_id_environment_override_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUN_ID overrides generated process identifiers during module initialization."""
    original = run_context.PROCESS_RUN_ID
    monkeypatch.setenv("RUN_ID", " supplied-run ")
    try:
        importlib.reload(run_context)
        assert run_context.PROCESS_RUN_ID == "supplied-run"
    finally:
        run_context.PROCESS_RUN_ID = original
