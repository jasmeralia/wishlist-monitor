"""Unit tests for HTML wishlist report rendering."""

import importlib
import os
import tempfile

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from core import report_html
from core.models import Item
from core.storage import ReaddedItemDiagnostic


@pytest.mark.parametrize(
    ("cents", "currency", "expected"),
    (
        (None, "USD", "Unavailable"),
        (-1, "USD", "Unavailable"),
        (0, "USD", "$0.00"),
        (1234, "USD", "$12.34"),
        (1234, "EUR", "12.34"),
    ),
)
def test_cents_to_str_formats_prices(
    cents: int | None, currency: str, expected: str
) -> None:
    """Prices render consistently across missing, negative, zero, and currency cases."""
    assert report_html._cents_to_str(cents, currency) == expected


def test_build_html_report_renders_all_change_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A populated dark report includes changes, links, diagnostics, and bindings."""
    monkeypatch.setattr(report_html, "EMAIL_THEME", "dark")
    monkeypatch.setattr(report_html, "APP_VERSION", "1.2.3")
    added = Item(
        "added",
        "Added Book",
        1299,
        binding="Hardcover",
        product_url="https://example.test/added",
        image_url="https://example.test/added.jpg",
    )
    removed = Item("removed", "Removed Gift", -1)
    increased = Item("up", "Price Up", 1500)
    decreased = Item("down", "Price Down", 500)
    unavailable = Item("gone", "Unavailable", -1)
    diagnostic = ReaddedItemDiagnostic(
        item_id="added",
        name="Added Book",
        removed_ts="2026-01-01",
        removed_run_id="old-run",
        removed_cycle_id="old-cycle",
        current_run_id="new-run",
        current_cycle_id="new-cycle",
    )

    rendered = report_html.build_html_report(
        "amazon",
        "Books",
        [added],
        [removed],
        [(increased, 1000, 1500), (decreased, 1000, 500), (unavailable, 500, -1)],
        4,
        3,
        wishlist_url="https://example.test/list",
        diagnostics={"run_id": "run-id", "cycle_id": "cycle-id"},
        readded_diagnostics=[diagnostic],
    )

    assert "Amazon Wishlist Update: Books" in rendered
    assert "Added Book" in rendered
    assert "Hardcover" in rendered
    assert "Removed Gift" in rendered
    assert "(+50.0%)" in rendered
    assert "(-50.0%)" in rendered
    assert "Unavailable" in rendered
    assert "Readded after removal" in rendered
    assert "https://example.test/list" in rendered
    assert "Run ID: run-id" in rendered
    assert "v1.2.3" in rendered


def test_build_html_report_supports_light_empty_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The light template renders an empty report without an optional wishlist link."""
    monkeypatch.setattr(report_html, "EMAIL_THEME", "light")

    rendered = report_html.build_html_report(
        "throne", "Gifts", [], [], [], 0, 0, wishlist_url=None
    )

    assert "Throne Wishlist Update: Gifts" in rendered
    assert "0 added" in rendered
    assert "View wishlist" not in rendered


def test_invalid_theme_falls_back_to_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid EMAIL_THEME is normalized to dark during module initialization."""
    original_theme = report_html.EMAIL_THEME
    monkeypatch.setenv("EMAIL_THEME", "neon")
    try:
        importlib.reload(report_html)
        assert report_html.EMAIL_THEME == "dark"
    finally:
        report_html.EMAIL_THEME = original_theme
