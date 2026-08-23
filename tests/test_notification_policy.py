"""Unit tests for per-wishlist notification policy resolution."""

import os
import tempfile

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core.notification_policy import resolve_notification_settings


def test_no_notifications_key_preserves_global_defaults() -> None:
    """A wishlist without a "notifications" key gets legacy always-on behavior."""
    settings = resolve_notification_settings({"platform": "amazon", "name": "List"})

    assert settings.notify_added is True
    assert settings.notify_removed is True
    assert settings.notify_price_decrease is True
    assert settings.price_decrease_threshold_percent is None


def test_notifications_overrides_are_applied() -> None:
    """Explicit per-source overrides are used verbatim."""
    settings = resolve_notification_settings(
        {
            "notifications": {
                "added": True,
                "removed": False,
                "price_decrease": True,
                "price_increase": False,
                "availability": True,
                "price_decrease_threshold_percent": 0,
            }
        }
    )

    assert settings.notify_added is True
    assert settings.notify_removed is False
    assert settings.notify_price_decrease is True
    assert settings.notify_price_increase is False
    assert settings.notify_availability is True
    assert settings.price_decrease_threshold_percent == 0.0


def test_non_dict_notifications_value_is_ignored() -> None:
    """A malformed "notifications" value (not an object) falls back to defaults."""
    settings = resolve_notification_settings({"notifications": "oops"})

    assert settings.notify_added is True
    assert settings.notify_removed is True


def test_invalid_threshold_value_falls_back_to_none() -> None:
    """A non-numeric threshold override is ignored rather than raising."""
    settings = resolve_notification_settings(
        {"notifications": {"price_decrease_threshold_percent": "lots"}}
    )

    assert settings.price_decrease_threshold_percent is None
