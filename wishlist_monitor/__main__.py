"""Entry point for ``python -m wishlist_monitor`` (native cron and local runs)."""

from core.logger import get_logger
from monitor import MODE, run_daemon, run_once

logger = get_logger(__name__)


def main() -> None:
    """Run one poll cycle or the daemon loop according to ``MODE``."""
    if MODE == "once":
        raise SystemExit(run_once())
    run_daemon()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Fatal monitor error")
        raise SystemExit(2) from exc
