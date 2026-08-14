"""Unit tests for the Amazon wishlist fetcher's parse_item_li()."""

import os
import tempfile
from unittest import mock

import pytest
import requests

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["LOG_TO_FILE"] = "false"

from bs4 import BeautifulSoup

from core.models import Item
from fetchers import amazon
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


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "https://www.amazon.com/hz/wishlist/ls/ABC123",
            "https://www.amazon.com/gp/aw/ls?lid=ABC123&ty=wishlist",
        ),
        (
            "https://www.amazon.com/gp/registry/wishlist/DEF456/",
            "https://www.amazon.com/gp/aw/ls?lid=DEF456&ty=wishlist",
        ),
        (
            "https://www.amazon.com/gp/registry/list/GHI789",
            "https://www.amazon.com/gp/aw/ls?lid=GHI789&ty=wishlist",
        ),
        ("https://example.test/custom", "https://example.test/custom"),
    ),
)
def test_normalize_wishlist_url_supports_known_shapes(
    source: str, expected: str
) -> None:
    """Known desktop wishlist URL shapes normalize to the mobile endpoint."""
    assert amazon.normalize_wishlist_url(source) == expected


def test_ensure_absolute_url_handles_absolute_and_relative_values() -> None:
    """Product and pagination URLs are normalized against the Amazon base URL."""
    assert amazon.ensure_absolute_url("https://example.test/item") == (
        "https://example.test/item"
    )
    assert amazon.ensure_absolute_url("/dp/ABC") == "https://www.amazon.com/dp/ABC"
    assert amazon.ensure_absolute_url("dp/ABC") == "https://www.amazon.com/dp/ABC"


@pytest.mark.parametrize(
    "html",
    (
        "Robot Check",
        "Enter the characters you see below",
        '<form action="/errors/validateCaptcha">',
        "To discuss automated access to Amazon data",
        "Type the characters you see in this image",
    ),
)
def test_looks_like_captcha_detects_block_markers(html: str) -> None:
    """Each supported Amazon block-page marker is detected."""
    assert amazon.looks_like_captcha_or_block(html)


def test_looks_like_captcha_accepts_normal_html() -> None:
    """Ordinary wishlist HTML is not mistaken for a CAPTCHA page."""
    assert not amazon.looks_like_captcha_or_block("<html>Wishlist</html>")


@pytest.mark.parametrize("status", (503, 418))
def test_fetch_page_raw_rejects_error_statuses(status: int) -> None:
    """Amazon HTTP errors become the fetcher's domain exception."""
    session = mock.Mock()
    session.get.return_value = mock.Mock(status_code=status, text="error")

    with pytest.raises(amazon.AmazonError):
        amazon.fetch_page_raw(session, "https://example.test", {})


def test_fetch_page_raw_returns_success_body() -> None:
    """A successful Amazon response returns its HTML body."""
    session = mock.Mock()
    session.get.return_value = mock.Mock(status_code=200, text="<html>ok</html>")

    assert (
        amazon.fetch_page_raw(session, "https://example.test", {}) == "<html>ok</html>"
    )
    session.get.assert_called_once_with("https://example.test", headers={}, timeout=30)


@pytest.mark.parametrize(
    ("attributes", "price"),
    (
        ('data-price="12.34"', 1234),
        ('data-price="inf"', -1),
        ('data-price="invalid"', -1),
        ("", 1234),
    ),
)
def test_parse_item_li_handles_price_sources(attributes: str, price: int) -> None:
    """Item parsing supports data prices, invalid values, and split-price fallback."""
    html = (
        f'<li class="awl-item-wrapper" id="ABC" {attributes}>'
        '<a href="/dp/ABC?ref=test"><h2>Rich Item</h2></a>'
        '<img src="/images/item.jpg">'
        '<span class="a-price-whole">12</span>'
        '<span class="a-price-fraction">34</span>'
        "</li>"
    )
    li = BeautifulSoup(html, "html.parser").select_one("li")

    item = amazon.parse_item_li(li)

    assert item.price_cents == price
    assert item.available == (price >= 0)
    assert item.product_url == "https://www.amazon.com/dp/ABC"
    assert item.image_url == "https://www.amazon.com/images/item.jpg"
    assert item.name == "Rich Item"


def test_parse_item_li_handles_missing_fraction_and_invalid_split_price() -> None:
    """Split prices default the fraction and tolerate invalid whole-number text."""
    whole_only = _li('<span class="a-price-whole">1,234</span>', "whole")
    invalid = _li(
        '<span class="a-price-whole">oops</span>'
        '<span class="a-price-fraction">xx</span>',
        "invalid",
    )

    assert amazon.parse_item_li(whole_only).price_cents == 123400
    assert amazon.parse_item_li(invalid).price_cents == -1


def test_extract_items_from_soup_parses_all_containers() -> None:
    """The soup helper parses each supported wishlist item container."""
    soup = BeautifulSoup(
        '<li class="awl-item-wrapper" id="one"><h3>One</h3></li>'
        '<div class="g-item-sortable" id="two"><h3>Two</h3></div>',
        "html.parser",
    )

    assert [item.item_id for item in amazon.extract_items_from_soup(soup)] == [
        "one",
        "two",
    ]


def test_apply_global_spacing_sleeps_only_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global request spacing sleeps for the remaining interval and updates state."""
    times = iter((105.0, 110.0))
    sleeper = mock.Mock()
    monkeypatch.setattr(amazon, "AMAZON_MIN_SPACING", 10)
    monkeypatch.setattr(amazon, "_LAST_AMAZON_FETCH_TS", 100.0)
    monkeypatch.setattr(amazon.time, "time", lambda: next(times))
    monkeypatch.setattr(amazon.time, "sleep", sleeper)

    amazon._apply_global_spacing("List", "id", 2)

    sleeper.assert_called_once_with(5.0)
    assert amazon._LAST_AMAZON_FETCH_TS == 110.0


def test_fetch_items_handles_empty_page_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetched page without item containers ends pagination successfully."""
    monkeypatch.setattr(amazon, "AMAZON_MIN_SPACING", 0)
    monkeypatch.setattr(amazon, "_LAST_AMAZON_FETCH_TS", 0.0)
    monkeypatch.setattr(amazon, "write_dump", lambda *_args, **_kwargs: None)
    fetch = mock.Mock(return_value="<html><body>empty</body></html>")
    monkeypatch.setattr(amazon, "fetch_page_raw", fetch)

    result = amazon.fetch_items("https://www.amazon.com/hz/wishlist/ls/ABC", "List")

    assert result.complete
    assert result.items == []
    assert fetch.call_count == 1


def test_fetch_items_stops_on_duplicate_only_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination stops when a subsequent page contains no previously unseen items."""
    first = (
        '<li class="awl-item-wrapper" id="same"><h3>Same</h3></li>'
        '<form class="scroll-state"><input class="showMoreUrl" value="/next"></form>'
    )
    duplicate = '<li class="awl-item-wrapper" id="same"><h3>Same</h3></li>'
    monkeypatch.setattr(amazon, "AMAZON_MIN_SPACING", 0)
    monkeypatch.setattr(amazon, "PAGE_SLEEP", 0)
    monkeypatch.setattr(amazon, "_LAST_AMAZON_FETCH_TS", 0.0)
    monkeypatch.setattr(amazon, "write_dump", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        amazon, "fetch_page_raw", mock.Mock(side_effect=[first, duplicate])
    )
    monkeypatch.setattr(amazon.time, "sleep", mock.Mock())

    result = amazon.fetch_items("ABC", "List")

    assert result.complete
    assert [item.item_id for item in result.items] == ["same"]


def test_fetch_items_skips_one_malformed_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An item-level parse error does not discard other items on the page."""
    html = (
        '<li class="awl-item-wrapper" id="bad"><h3>Bad</h3></li>'
        '<li class="awl-item-wrapper" id="good"><h3>Good</h3></li>'
    )
    parsed = Item("good", "Good")
    monkeypatch.setattr(amazon, "AMAZON_MIN_SPACING", 0)
    monkeypatch.setattr(amazon, "_LAST_AMAZON_FETCH_TS", 0.0)
    monkeypatch.setattr(amazon, "write_dump", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(amazon, "fetch_page_raw", mock.Mock(return_value=html))
    monkeypatch.setattr(
        amazon, "parse_item_li", mock.Mock(side_effect=[ValueError("bad"), parsed])
    )

    result = amazon.fetch_items("ABC", "List")

    assert [item.item_id for item in result.items] == ["good"]


def test_fetch_items_retries_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient request exceptions are retried without a real delay."""
    html = '<li class="awl-item-wrapper" id="ok"><h3>Okay</h3></li>'
    monkeypatch.setattr(amazon, "AMAZON_MIN_SPACING", 0)
    monkeypatch.setattr(amazon, "AMAZON_MAX_PAGE_RETRIES", 2)
    monkeypatch.setattr(amazon, "FAIL_SLEEP", 0)
    monkeypatch.setattr(amazon, "_LAST_AMAZON_FETCH_TS", 0.0)
    monkeypatch.setattr(amazon, "write_dump", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(amazon.time, "sleep", mock.Mock())
    monkeypatch.setattr(
        amazon,
        "fetch_page_raw",
        mock.Mock(side_effect=[requests.RequestException("temporary"), html]),
    )

    result = amazon.fetch_items("ABC", "List")

    assert result.complete
    assert [item.item_id for item in result.items] == ["ok"]
