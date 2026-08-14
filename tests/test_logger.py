"""Unit tests for logging configuration and run context."""

import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import logger, run_context


@pytest.fixture()
def isolated_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[logging.Logger]:
    """Reset and restore root logging state around each configuration test."""
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    old_configured = logger._CONFIGURED
    root.handlers = []
    logger._CONFIGURED = False
    monkeypatch.setenv("LOG_TO_FILE", "false")
    monkeypatch.setenv("LOG_TO_STDOUT", "true")
    try:
        yield root
    finally:
        for handler in root.handlers:
            if handler not in old_handlers:
                handler.close()
        root.handlers = old_handlers
        root.setLevel(old_level)
        logger._CONFIGURED = old_configured


def test_run_context_filter_attaches_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The logging filter adds process and active-cycle identifiers."""
    monkeypatch.setattr(run_context, "PROCESS_RUN_ID", "run-123")
    monkeypatch.setattr(run_context, "get_cycle_id", lambda: "cycle-456")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)

    assert logger.RunContextFilter().filter(record)
    assert record.run_id == "run-123"
    assert record.cycle_id == "cycle-456"


def test_run_context_filter_uses_placeholder_without_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The logging filter uses a dash when no cycle is active."""
    monkeypatch.setattr(run_context, "get_cycle_id", lambda: "")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)

    logger.RunContextFilter().filter(record)

    assert record.cycle_id == "-"


def test_setup_logging_wires_stdout_once(
    isolated_logging: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdout logging is configured once with the requested level and filter."""
    isolated_logging.handlers = []
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    logger.setup_logging()
    logger.setup_logging()

    assert isolated_logging.level == logging.DEBUG
    assert len(isolated_logging.handlers) == 1
    handler = isolated_logging.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.filters and isinstance(handler.filters[0], logger.RunContextFilter)


def test_setup_logging_wires_file_and_stdout(
    isolated_logging: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """File and stdout handlers can both be configured with rotation settings."""
    isolated_logging.handlers = []
    log_path = tmp_path / "logs" / "monitor.log"
    monkeypatch.setenv("LOG_TO_FILE", "true")
    monkeypatch.setenv("LOG_TO_STDOUT", "true")
    monkeypatch.setenv("LOG_FILE", str(log_path))
    monkeypatch.setenv("LOG_MAX_BYTES", "1234")
    monkeypatch.setenv("LOG_BACKUPS", "2")

    logger.setup_logging()

    assert len(isolated_logging.handlers) == 2
    file_handlers = [
        handler
        for handler in isolated_logging.handlers
        if isinstance(handler, logger.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes == 1234
    assert file_handlers[0].backupCount == 2


def test_setup_logging_warns_when_file_handler_creation_fails(
    isolated_logging: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file-handler setup failure logs a warning instead of escaping."""
    isolated_logging.handlers = []
    monkeypatch.setenv("LOG_TO_FILE", "true")
    monkeypatch.setenv("LOG_TO_STDOUT", "false")
    monkeypatch.setattr(logger.os, "makedirs", mock.Mock(side_effect=OSError("denied")))
    warning = mock.Mock()
    monkeypatch.setattr(isolated_logging, "warning", warning)

    logger.setup_logging()

    warning.assert_called_once()
    assert logger._CONFIGURED


def test_get_logger_configures_and_returns_named_logger(
    isolated_logging: logging.Logger,
) -> None:
    """The logger factory ensures configuration and returns the named logger."""
    isolated_logging.handlers = []
    result = logger.get_logger("wishlist.test")

    assert result is logging.getLogger("wishlist.test")
    assert logger._CONFIGURED
