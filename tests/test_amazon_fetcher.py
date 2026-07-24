"""Unit tests for the Amazon wishlist fetcher's parse_item_li()."""

import os
import tempfile

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from bs4 import BeautifulSoup

from fetchers.amazon import parse_item_li


def _li(inner_html: str, item_id: str = "test-id") -> BeautifulSoup:
    """Wrap inner_html in a minimal awl-item-wrapper li and return its Tag."""
    html = f'<li class="awl-item-wrapper" id="{item_id}">{inner_html}</li>'
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("li")


def test_binding_extracted_from_price_recipe_span() -> None:
    """parse_item_li() captures format text from the price-recipe container."""
    li = _li(
        "<h3>Fourth Wing</h3>"
        '<div data-cy="price-recipe">'
        '  <span class="a-size-mini a-color-base puis-medium-weight-text a-text-bold">Hardcover</span>'
        "</div>",
        item_id="ASIN001",
    )
    item = parse_item_li(li)
    assert item.binding == "Hardcover"


def test_binding_empty_when_no_format_present() -> None:
    """parse_item_li() sets binding to empty string for items without a format span."""
    li = _li("<h3>Snack Item</h3>", item_id="ASIN002")
    item = parse_item_li(li)
    assert item.binding == ""


def test_binding_kindle_edition() -> None:
    """parse_item_li() handles Kindle Edition format."""
    li = _li(
        "<h3>Some Book</h3>"
        '<div data-cy="price-recipe">'
        '  <span class="a-size-mini a-color-base puis-medium-weight-text a-text-bold">Kindle Edition</span>'
        "</div>",
        item_id="ASIN003",
    )
    item = parse_item_li(li)
    assert item.binding == "Kindle Edition"


def test_binding_paperback() -> None:
    """parse_item_li() handles Paperback format."""
    li = _li(
        "<h3>Another Book</h3>"
        '<div data-cy="price-recipe">'
        '  <span class="a-size-mini a-color-base puis-medium-weight-text a-text-bold">Paperback</span>'
        "</div>",
        item_id="ASIN004",
    )
    item = parse_item_li(li)
    assert item.binding == "Paperback"
