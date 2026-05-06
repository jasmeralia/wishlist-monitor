"""Logging setup: configures the root logger once from environment variables."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import run_context

_CONFIGURED = False


class RunContextFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    """Attach process and cycle identifiers to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_context.PROCESS_RUN_ID
        record.cycle_id = run_context.get_cycle_id() or "-"
        return True


def setup_logging() -> None:
    """Configure the root logger from environment variables (idempotent)."""
    global _CONFIGURED  # pylint: disable=global-statement
    if _CONFIGURED:
        return

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_to_file = os.getenv("LOG_TO_FILE", "true").lower() == "true"
    log_file = run_context.get_log_file_path()
    log_max_bytes = int(os.getenv("LOG_MAX_BYTES", str(2 * 1024 * 1024)))
    log_backups = int(os.getenv("LOG_BACKUPS", "3"))
    log_to_stdout = os.getenv("LOG_TO_STDOUT", "true").lower() == "true"

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [run=%(run_id)s cycle=%(cycle_id)s] "
        "[%(name)s] %(message)s"
    )
    run_filter = RunContextFilter()

    # Avoid duplicate handlers
    if not root.handlers:
        if log_to_stdout:
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(getattr(logging, log_level, logging.INFO))
            ch.setFormatter(formatter)
            ch.addFilter(run_filter)
            root.addHandler(ch)

        if log_to_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                fh = RotatingFileHandler(
                    log_file,
                    maxBytes=log_max_bytes,
                    backupCount=log_backups,
                )
                fh.setLevel(getattr(logging, log_level, logging.INFO))
                fh.setFormatter(formatter)
                fh.addFilter(run_filter)
                root.addHandler(fh)
            except Exception as e:  # pylint: disable=broad-exception-caught
                root.warning("Failed to initialize file logging: %s", e)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger for *name*, ensuring logging is configured first."""
    setup_logging()
    return logging.getLogger(name)
