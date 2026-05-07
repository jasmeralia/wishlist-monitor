"""Run and cycle identifiers used to correlate logs, emails, and events."""

import contextvars
import datetime
import os
import uuid
from pathlib import Path


def _utc_timestamp() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


PROCESS_RUN_ID = (
    os.getenv("RUN_ID", "").strip() or f"{_utc_timestamp()}-{uuid.uuid4().hex[:8]}"
)

_CYCLE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("cycle_id", default="")


def _new_run_id() -> str:
    """Return a short sortable identifier suitable for logs and emails."""
    return f"{_utc_timestamp()}-{uuid.uuid4().hex[:8]}"


def start_cycle() -> str:
    """Create and store a new cycle identifier for the current execution context."""
    cycle_id = _new_run_id()
    _CYCLE_ID.set(cycle_id)
    return cycle_id


def get_cycle_id() -> str:
    """Return the active cycle identifier, or an empty string if none is set."""
    return _CYCLE_ID.get()


def get_log_file_path() -> str:
    """Return the configured log path with the process run ID added to its filename."""
    log_file = Path(os.getenv("LOG_FILE", "/data/wishlist_monitor.log"))
    suffix = log_file.suffix or ".log"
    stem = log_file.stem if log_file.suffix else log_file.name
    return str(log_file.with_name(f"{stem}_{PROCESS_RUN_ID}{suffix}"))
