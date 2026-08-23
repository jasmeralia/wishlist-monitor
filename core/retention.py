"""Retention policies for generated diagnostic files."""

import os
import time
from pathlib import Path

from . import run_context, storage
from .logger import get_logger

logger = get_logger(__name__)

DEBUG_DUMP_PATTERNS = ("amazon_*.html", "throne_*.html", "honeybirdette_*.html")


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int) -> int:
    """Read an integer environment variable with fallback and lower bound."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using %d.", name, raw, default)
        return default


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _prune_files(
    label: str,
    files: list[Path],
    max_age_days: int,
    max_files: int,
) -> None:
    cutoff = time.time() - (max_age_days * 86400)
    scanned = 0
    deleted_by_age = 0
    deleted_by_count = 0
    errors = 0

    remaining: list[Path] = []
    for path in files:
        scanned += 1
        try:
            stat = path.stat()
        except OSError as exc:
            logger.debug("Failed to stat %s file %s: %s", label, path, exc)
            errors += 1
            continue

        if max_age_days > 0 and stat.st_mtime < cutoff:
            try:
                path.unlink()
                deleted_by_age += 1
            except OSError as exc:
                logger.debug("Failed to delete old %s file %s: %s", label, path, exc)
                errors += 1
            continue

        remaining.append(path)

    if 0 < max_files < len(remaining):
        remaining.sort(key=_mtime)
        for path in remaining[: len(remaining) - max_files]:
            try:
                path.unlink()
                deleted_by_count += 1
            except OSError as exc:
                logger.debug("Failed to delete excess %s file %s: %s", label, path, exc)
                errors += 1

    if deleted_by_age or deleted_by_count or errors:
        logger.info(
            "%s pruning complete: scanned=%d deleted_by_age=%d "
            "deleted_by_count=%d errors=%d.",
            label.capitalize(),
            scanned,
            deleted_by_age,
            deleted_by_count,
            errors,
        )


def _debug_dump_files(debug_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in DEBUG_DUMP_PATTERNS:
        files.extend(p for p in debug_dir.glob(pattern) if p.is_file())
    return files


def prune_debug_dumps() -> None:
    """Delete old debug dumps and enforce a maximum retained file count."""
    if not env_bool("DEBUG_DUMP_PRUNE_ENABLED", True):
        return

    debug_dir = Path(os.getenv("DEBUG_DIR", "/data/debug_dumps"))
    if not debug_dir.exists():
        return
    if not debug_dir.is_dir():
        logger.warning(
            "DEBUG_DIR is not a directory; skipping dump pruning: %s", debug_dir
        )
        return

    _prune_files(
        "debug dump",
        _debug_dump_files(debug_dir),
        env_int("DEBUG_DUMP_MAX_AGE_DAYS", 7, 0),
        env_int("DEBUG_DUMP_MAX_FILES", 500, 0),
    )


def _log_files() -> list[Path]:
    configured_log = Path(os.getenv("LOG_FILE", "/data/wishlist_monitor.log"))
    log_dir = configured_log.parent
    if not log_dir.exists() or not log_dir.is_dir():
        return []

    suffix = configured_log.suffix or ".log"
    stem = configured_log.stem if configured_log.suffix else configured_log.name
    current_log = Path(run_context.get_log_file_path()).resolve()
    files: list[Path] = []

    for path in log_dir.glob(f"{stem}_*{suffix}*"):
        if not path.is_file():
            continue
        try:
            if path.resolve() == current_log:
                continue
        except OSError:
            pass
        files.append(path)

    return files


def prune_log_files() -> None:
    """Delete old process-run log files and enforce a maximum retained count."""
    if not env_bool("LOG_PRUNE_ENABLED", True):
        return

    _prune_files(
        "log file",
        _log_files(),
        env_int("LOG_MAX_AGE_DAYS", 7, 0),
        env_int("LOG_MAX_FILES", 100, 0),
    )


def prune_item_observations() -> None:
    """Delete item_observations rows older than the configured retention window."""
    if not env_bool("OBSERVATION_PRUNE_ENABLED", True):
        return

    max_age_days = env_int("OBSERVATION_RETENTION_DAYS", 120, 0)
    deleted = storage.prune_observations(max_age_days)
    if deleted:
        logger.info(
            "Observation pruning complete: deleted=%d (retention=%d days).",
            deleted,
            max_age_days,
        )


def prune_diagnostics() -> None:
    """Prune all generated diagnostic files and stale observation history."""
    prune_debug_dumps()
    prune_log_files()
    prune_item_observations()
