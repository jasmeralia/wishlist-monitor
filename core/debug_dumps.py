"""Retention policy for fetcher HTML debug dumps."""
import os
import time
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

DEBUG_DUMP_PATTERNS = ("amazon_*.html", "throne_*.html")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using %d.", name, raw, default)
        return default


def _dump_files(debug_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in DEBUG_DUMP_PATTERNS:
        files.extend(p for p in debug_dir.glob(pattern) if p.is_file())
    return files


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def prune_debug_dumps() -> None:
    """Delete old debug dumps and enforce a maximum retained file count."""
    if not _env_bool("DEBUG_DUMP_PRUNE_ENABLED", True):
        return

    debug_dir = Path(os.getenv("DEBUG_DIR", "/data/debug_dumps"))
    if not debug_dir.exists():
        return
    if not debug_dir.is_dir():
        logger.warning("DEBUG_DIR is not a directory; skipping dump pruning: %s", debug_dir)
        return

    max_age_days = _env_int("DEBUG_DUMP_MAX_AGE_DAYS", 3, 0)
    max_files = _env_int("DEBUG_DUMP_MAX_FILES", 500, 0)
    cutoff = time.time() - (max_age_days * 86400)
    scanned = 0
    deleted_by_age = 0
    deleted_by_count = 0
    errors = 0

    remaining: list[Path] = []
    for path in _dump_files(debug_dir):
        scanned += 1
        try:
            stat = path.stat()
        except OSError as exc:
            logger.debug("Failed to stat debug dump %s: %s", path, exc)
            errors += 1
            continue

        if max_age_days > 0 and stat.st_mtime < cutoff:
            try:
                path.unlink()
                deleted_by_age += 1
            except OSError as exc:
                logger.debug("Failed to delete old debug dump %s: %s", path, exc)
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
                logger.debug("Failed to delete excess debug dump %s: %s", path, exc)
                errors += 1

    if deleted_by_age or deleted_by_count or errors:
        logger.info(
            "Debug dump pruning complete: scanned=%d deleted_by_age=%d "
            "deleted_by_count=%d errors=%d.",
            scanned,
            deleted_by_age,
            deleted_by_count,
            errors,
        )
