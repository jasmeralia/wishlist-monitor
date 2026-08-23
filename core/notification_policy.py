"""Per-wishlist notification policy resolution.

Wishlist entries in config.json may include an optional "notifications" object
to override which change types are surfaced in email reports, and at what
price-decrease threshold, without touching the process-wide env defaults used
by Amazon/Throne. Entries without a "notifications" key are unaffected.
"""

from dataclasses import dataclass
from typing import Any

from .diff import NOTIFY_ON_AVAILABILITY_CHANGE, NOTIFY_ON_PRICE_INCREASE


@dataclass
class NotificationSettings:
    """Resolved per-wishlist notification policy passed into diffing/reporting."""

    notify_added: bool
    notify_removed: bool
    notify_price_increase: bool
    notify_price_decrease: bool
    notify_availability: bool
    price_decrease_threshold_percent: float | None


def resolve_notification_settings(wl: dict[str, Any]) -> NotificationSettings:
    """Merge a wishlist's "notifications" overrides over the global defaults."""
    raw = wl.get("notifications")
    overrides: dict[str, Any] = raw if isinstance(raw, dict) else {}

    threshold = overrides.get("price_decrease_threshold_percent")
    try:
        threshold = float(threshold) if threshold is not None else None
    except (TypeError, ValueError):
        threshold = None

    return NotificationSettings(
        notify_added=bool(overrides.get("added", True)),
        notify_removed=bool(overrides.get("removed", True)),
        notify_price_increase=bool(
            overrides.get("price_increase", NOTIFY_ON_PRICE_INCREASE)
        ),
        notify_price_decrease=bool(overrides.get("price_decrease", True)),
        notify_availability=bool(
            overrides.get("availability", NOTIFY_ON_AVAILABILITY_CHANGE)
        ),
        price_decrease_threshold_percent=threshold,
    )
