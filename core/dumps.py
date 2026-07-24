"""Shared HTML debug dump writer used by all fetchers."""

import datetime
import os
import re
from pathlib import Path

from core import run_context
from core.logger import get_logger

logger = get_logger(__name__)


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def write_dump(
    platform: str,
    wishlist_name: str | None,
    html: str,
    page_index: int | None = None,
) -> Path | None:
    """Write an HTML debug dump to DEBUG_DIR; return its path or None on failure."""
    debug_dir = Path(os.getenv("DEBUG_DIR", "/data/debug_dumps"))
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe = _sanitize(wishlist_name or "unknown")
        cycle_id = run_context.get_cycle_id()
        cycle_suffix = f"_{cycle_id}" if cycle_id else ""
        page_suffix = f"_page{page_index}" if page_index is not None else ""
        path = debug_dir / f"{platform}_{safe}{page_suffix}_{ts}{cycle_suffix}.html"
        path.write_text(html, encoding="utf-8")
        logger.debug("Dumped %s HTML to %s", platform, path)
        return path
    except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
        logger.debug("Failed to dump %s HTML: %s", platform, exc)
        return None
