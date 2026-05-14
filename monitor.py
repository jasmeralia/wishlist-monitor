"""Wishlist monitor entry point: runs once or as a polling daemon."""

# ruff: noqa: E402  -- load_dotenv() must run before local imports that read env vars at import time

import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv()

# pylint: disable=wrong-import-position
from core import run_context, storage
from core.diff import diff_items
from core.emailer import get_global_recipients, send_email
from core.logger import get_logger
from core.models import FetchResult
from core.report_html import build_html_report
from core.retention import env_int, prune_diagnostics
from fetchers import FETCHERS

# pylint: enable=wrong-import-position

logger = get_logger(__name__)

POLL_MINUTES = env_int("POLL_MINUTES", 10, 1)
MODE = os.getenv("MODE", "daemon").lower()  # "daemon" or "once"
CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.json")
REMOVAL_THRESHOLD = env_int("REMOVAL_THRESHOLD", 20, 1)
REMOVAL_DROP_PERCENT_THRESHOLD = env_int("REMOVAL_DROP_PERCENT_THRESHOLD", 25, 1)
REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD = env_int(
    "REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD", 10, 1
)
DEBUG_DUMP_PRUNE_INTERVAL_MINUTES = env_int("DEBUG_DUMP_PRUNE_INTERVAL_MINUTES", 60, 1)


def _wishlist_url(platform: str, identifier: str) -> str | None:
    platform = platform.strip().lower()
    if not identifier:
        return None
    if identifier.startswith("http://") or identifier.startswith("https://"):
        return identifier
    if platform == "amazon":
        return f"https://www.amazon.com/hz/wishlist/ls/{identifier}"
    if platform == "throne":
        return f"https://throne.com/{identifier}"
    return None


def jitter_sleep_minutes(minutes: int) -> None:
    """Sleep for approximately *minutes* with ±10% random jitter."""
    base = max(1, minutes)
    jitter = random.uniform(-0.1 * base, 0.1 * base)
    total = base + jitter
    logger.info("Sleeping %.1f minutes before next cycle.", total)
    time.sleep(total * 60)


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    """Load and validate the JSON config file, raising SystemExit on error."""
    if not os.path.exists(path):
        logger.error("Config file not found at %s", path)
        raise SystemExit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg: Dict[str, Any] = json.load(f)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to load config.json at %s: %s", path, e)
        raise SystemExit(1) from e

    if not isinstance(cfg, dict) or "wishlists" not in cfg:
        logger.error("config.json must be an object with a 'wishlists' key.")
        raise SystemExit(1)

    if not isinstance(cfg["wishlists"], list) or not cfg["wishlists"]:
        logger.error("config.json 'wishlists' must be a non-empty list.")
        raise SystemExit(1)

    return cfg


def get_recipients_for_wishlist(wl: Dict[str, Any]) -> List[str]:
    """Return per-wishlist recipients, falling back to global recipients."""
    wl_recipients = wl.get("recipients")
    if isinstance(wl_recipients, list):
        cleaned = [r.strip() for r in wl_recipients if isinstance(r, str) and r.strip()]
        if cleaned:
            return cleaned
    return get_global_recipients()


def normalize_fetch_result(result: Any) -> FetchResult:
    """Convert legacy fetcher tuple results to FetchResult."""
    if isinstance(result, FetchResult):
        return result

    items, dump_paths = result
    return FetchResult(
        items=items or [],
        dump_paths=dump_paths or [],
        complete=True,
        failure_reason=None,
    )


def process_wishlist(wl: Dict[str, Any]) -> None:
    """Fetch, diff, and notify for a single wishlist config entry."""
    platform = wl.get("platform", "").strip().lower()
    name = wl.get("name", "").strip()
    identifier = wl.get("identifier", "").strip()
    enabled = wl.get("enabled", True)

    if not platform or not name or not identifier:
        logger.error(
            "Invalid wishlist entry (missing platform/name/identifier): %s", wl
        )
        return

    if not enabled:
        logger.info("Wishlist '%s' (%s) is disabled; skipping.", name, platform)
        return

    fetcher = FETCHERS.get(platform)
    if not fetcher:
        logger.error(
            "No fetcher registered for platform '%s'; skipping wishlist '%s'.",
            platform,
            name,
        )
        return

    wishlist_id = identifier
    logger.info(
        "Processing wishlist: platform=%s, name=%s, identifier=%s",
        platform,
        name,
        identifier,
    )

    previous_items = storage.get_previous_items(platform, wishlist_id)
    previous_count = len(previous_items)

    fetch_result = normalize_fetch_result(fetcher(identifier, name))
    items = fetch_result.items
    dump_paths = fetch_result.dump_paths

    if not fetch_result.complete:
        logger.error(
            "Incomplete fetch for %s '%s' (%s); skipping diff/save/notification. "
            "reason=%s previous_count=%d fetched_count=%d dumps=%s run_id=%s cycle_id=%s",
            platform,
            name,
            wishlist_id,
            fetch_result.failure_reason or "<unknown>",
            previous_count,
            len(items),
            [str(p) for p in dump_paths],
            run_context.PROCESS_RUN_ID,
            run_context.get_cycle_id(),
        )
        return

    if not items:
        if previous_count > 0:
            logger.error(
                "Fetch returned zero items for %s:%s but previous count is %d; skipping diff.",
                platform,
                wishlist_id,
                previous_count,
            )
        else:
            logger.info(
                "Fetch returned zero items for %s:%s and no previous items; skipping.",
                platform,
                wishlist_id,
            )
        return

    added, removed, price_changes = diff_items(previous_items, items)
    new_count = len(items)

    if (
        previous_count >= REMOVAL_MIN_PREVIOUS_COUNT_FOR_DROP_GUARD
        and new_count < previous_count
    ):
        drop_pct = (previous_count - new_count) * 100.0 / previous_count
        if drop_pct >= REMOVAL_DROP_PERCENT_THRESHOLD:
            logger.warning(
                "REMOVAL DROP GUARD TRIGGERED for %s '%s' (%s): "
                "count dropped from %d to %d (%.1f%%, threshold=%d%%). "
                "Skipping database save and notifications. "
                "This may indicate a partial scrape/parse failure.",
                platform,
                name,
                wishlist_id,
                previous_count,
                new_count,
                drop_pct,
                REMOVAL_DROP_PERCENT_THRESHOLD,
            )
            logger.warning(
                "Drop guard diagnostics — previous_count=%d, fetched_count=%d, "
                "added=%d, removed=%d, price_changes=%d, run_id=%s, cycle_id=%s",
                previous_count,
                new_count,
                len(added),
                len(removed),
                len(price_changes),
                run_context.PROCESS_RUN_ID,
                run_context.get_cycle_id(),
            )
            if dump_paths:
                logger.warning(
                    "HTML captures for this cycle: %s",
                    [str(p) for p in dump_paths],
                )
            logger.debug("Full removed items: %s", [vars(item) for item in removed])
            logger.debug("Full fetched items: %s", [vars(item) for item in items])
            return

    if len(removed) >= REMOVAL_THRESHOLD:
        logger.warning(
            "REMOVAL THRESHOLD EXCEEDED for %s '%s' (%s): "
            "%d items would be removed (threshold=%d). "
            "Skipping database save and notifications. "
            "This may indicate a scrape/parse failure.",
            platform,
            name,
            wishlist_id,
            len(removed),
            REMOVAL_THRESHOLD,
        )
        logger.warning(
            "Threshold guard diagnostics — previous_count=%d, fetched_count=%d, "
            "added=%d, removed=%d, price_changes=%d",
            previous_count,
            new_count,
            len(added),
            len(removed),
            len(price_changes),
        )
        logger.warning(
            "Removed item IDs and names: %s",
            [(item.item_id, item.name) for item in removed],
        )
        logger.warning(
            "Added item IDs and names: %s",
            [(item.item_id, item.name) for item in added],
        )
        if dump_paths:
            logger.warning(
                "HTML captures for this cycle: %s",
                [str(p) for p in dump_paths],
            )
        logger.debug("Full removed items: %s", [vars(item) for item in removed])
        logger.debug("Full fetched items: %s", [vars(item) for item in items])
        return

    readded_diagnostics = storage.find_readded_item_diagnostics(
        platform,
        wishlist_id,
        added,
        current_run_id=run_context.PROCESS_RUN_ID,
        current_cycle_id=run_context.get_cycle_id(),
    )
    if readded_diagnostics:
        logger.warning(
            "Detected %d items readded after their most recent prior event was removal: %s",
            len(readded_diagnostics),
            [
                {
                    "item_id": item.item_id,
                    "name": item.name,
                    "removed_run_id": item.removed_run_id,
                    "removed_cycle_id": item.removed_cycle_id,
                    "current_run_id": item.current_run_id,
                    "current_cycle_id": item.current_cycle_id,
                }
                for item in readded_diagnostics
            ],
        )

    # Threshold not met — clean up dumps unless DEBUG logging is active
    if not logger.isEnabledFor(logging.DEBUG):
        for p in dump_paths:
            try:
                os.remove(p)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    storage.save_items_and_events(
        platform,
        wishlist_id,
        items,
        added,
        removed,
        price_changes,
        run_id=run_context.PROCESS_RUN_ID,
        cycle_id=run_context.get_cycle_id(),
    )

    if not (added or removed or price_changes):
        logger.info("No changes for %s '%s' (%s).", platform, name, wishlist_id)
        return

    subject = (
        f"[Wishlist Monitor] Changes detected on {platform.capitalize()} for {name}"
    )
    html_body = build_html_report(
        platform,
        name,
        added,
        removed,
        price_changes,
        previous_count,
        new_count,
        wishlist_url=_wishlist_url(platform, identifier),
        diagnostics={
            "run_id": run_context.PROCESS_RUN_ID,
            "cycle_id": run_context.get_cycle_id(),
            "log_file": run_context.get_log_file_path(),
        },
        readded_diagnostics=readded_diagnostics,
    )

    recipients = get_recipients_for_wishlist(wl)
    if not recipients:
        logger.error("No recipients for wishlist '%s' (platform=%s).", name, platform)
    else:
        send_email(subject, html_body, None, recipients)


def _wishlist_debug_id(wl: Dict[str, Any]) -> str:
    platform = wl.get("platform", "").strip().lower()
    name = wl.get("name", "").strip()
    if not platform and not name:
        return "<invalid>"
    return f"{platform}:{name}"


def _debug_log_wishlist_order(phase: str, wishlists: List[Dict[str, Any]]) -> None:
    try:
        order = [_wishlist_debug_id(wl) for wl in wishlists if isinstance(wl, dict)]
    except Exception:  # pylint: disable=broad-exception-caught
        order = ["<error>"]
    logger.debug("%s wishlist order: %s", phase, order)


def run_once() -> int:
    """Run a single poll cycle across all configured wishlists."""
    cycle_id = run_context.start_cycle()
    logger.info(
        "Starting one-shot run: run_id=%s cycle_id=%s log_file=%s.",
        run_context.PROCESS_RUN_ID,
        cycle_id,
        run_context.get_log_file_path(),
    )
    prune_diagnostics()
    storage.ensure_db()
    cfg = load_config()
    wishlists = cfg.get("wishlists", [])

    _debug_log_wishlist_order("run_once BEFORE shuffle", wishlists)
    random.shuffle(wishlists)
    _debug_log_wishlist_order("run_once AFTER shuffle", wishlists)

    for wl in wishlists:
        try:
            process_wishlist(wl)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Unhandled error in run_once: %s", e)

    return 0


def run_daemon() -> None:
    """Run as a continuous daemon, polling each wishlist on its configured interval."""
    logger.info(
        "Starting daemon; poll every %d minutes. run_id=%s log_file=%s.",
        POLL_MINUTES,
        run_context.PROCESS_RUN_ID,
        run_context.get_log_file_path(),
    )
    storage.ensure_db()
    last_run_map: Dict[Tuple[str, str], float] = {}
    last_prune_ts = 0.0

    while True:
        try:
            cfg = load_config()
            wishlists = cfg.get("wishlists", [])
            now = time.time()
            cycle_id = run_context.start_cycle()

            prune_interval_seconds = max(1, DEBUG_DUMP_PRUNE_INTERVAL_MINUTES) * 60
            if now - last_prune_ts >= prune_interval_seconds:
                prune_diagnostics()
                last_prune_ts = now

            seed = time.time_ns()
            random.seed(seed)
            logger.debug(
                "Daemon cycle start %s with seed %d (%d wishlists, cycle_id=%s).",
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                seed,
                len(wishlists),
                cycle_id,
            )

            _debug_log_wishlist_order("daemon BEFORE shuffle", wishlists)
            random.shuffle(wishlists)
            _debug_log_wishlist_order("daemon AFTER shuffle", wishlists)

            logger.debug(
                "Daemon processing order: %s",
                [_wishlist_debug_id(wl) for wl in wishlists],
            )

            for wl in wishlists:
                if not isinstance(wl, dict):
                    logger.error("Invalid WL entry: %s", wl)
                    continue

                platform = wl.get("platform", "").strip().lower()
                name = wl.get("name", "").strip()
                if not platform or not name:
                    logger.error("Invalid WL (missing platform or name): %s", wl)
                    continue

                key = (platform, name)
                poll_val = wl.get("poll_minutes")

                try:
                    poll_minutes = (
                        int(poll_val) if poll_val is not None else POLL_MINUTES
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    poll_minutes = POLL_MINUTES

                poll_minutes = max(1, poll_minutes)

                last_ts = last_run_map.get(key)
                if last_ts:
                    elapsed = (now - last_ts) / 60
                    if elapsed < poll_minutes:
                        logger.debug(
                            "Skip %s:%s (%.1f < %d minutes).",
                            platform,
                            name,
                            elapsed,
                            poll_minutes,
                        )
                        continue

                logger.debug(
                    "Processing WL %s:%s (poll_minutes=%d).",
                    platform,
                    name,
                    poll_minutes,
                )

                try:
                    process_wishlist(wl)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.exception("Error processing %s:%s: %s", platform, name, e)
                finally:
                    last_run_map[key] = time.time()

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Unhandled error in daemon loop: %s", e)

        jitter_sleep_minutes(POLL_MINUTES)


if __name__ == "__main__":
    try:
        if MODE == "once":
            raise SystemExit(run_once())
        run_daemon()
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Fatal monitor error: %s", e)
        raise SystemExit(2) from e
