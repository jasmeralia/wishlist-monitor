"""Unit tests for Throne wishlist extraction strategies."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from tenacity import RetryError

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

from core.models import Item
from fetchers import throne


def _next_data_html(payload: object) -> str:
    """Wrap a JSON payload in a minimal Next.js data script."""
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def _jsonld_html(payload: object) -> str:
    """Wrap a JSON payload in a minimal JSON-LD script."""
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


def test_normalize_target_and_extract_slug() -> None:
    """Throne usernames normalize to URLs and URL identifiers reduce to slugs."""
    assert throne._normalize_target("creator") == "https://throne.com/creator"
    assert throne._normalize_target("https://throne.com/creator") == (
        "https://throne.com/creator"
    )
    assert throne._extract_slug(" creator/ ") == "creator"
    assert throne._extract_slug("https://www.throne.com/creator/item/123") == "creator"


def test_fetch_returns_response_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """The low-level fetch helper validates status and returns response text."""
    response = mock.Mock(text="<html>ok</html>")
    get = mock.Mock(return_value=response)
    monkeypatch.setattr(throne.SESSION, "get", get)

    assert throne._fetch("https://throne.com/creator") == "<html>ok</html>"
    get.assert_called_once_with("https://throne.com/creator", timeout=30)
    response.raise_for_status.assert_called_once_with()


def test_next_data_returns_none_for_missing_invalid_or_empty_payload() -> None:
    """NEXT_DATA extraction rejects missing, malformed, and item-free payloads."""
    assert throne._extract_items_next_data("<html></html>") is None
    assert (
        throne._extract_items_next_data('<script id="__NEXT_DATA__">{bad</script>')
        is None
    )
    assert (
        throne._extract_items_next_data(_next_data_html({"props": {"values": [1, 2]}}))
        is None
    )


def test_next_data_parses_supported_item_shapes() -> None:
    """NEXT_DATA extraction normalizes identifiers, prices, images, URLs, and availability."""
    payload = {
        "props": {
            "nested": {
                "items": [
                    {
                        "id": "cents",
                        "name": "Cent Item",
                        "price_cents": "1234",
                        "currencyCode": "EUR",
                        "imgLink": "https://img/cents",
                    },
                    {
                        "uuid": "bad-cents",
                        "title": "Bad Cents",
                        "priceCents": "oops",
                        "image": "https://img/bad",
                    },
                    {
                        "itemId": "large-int",
                        "name": "Large Integer",
                        "price": 1501,
                        "extraImgLinks": ["https://img/extra"],
                    },
                    {
                        "id": "small-int",
                        "name": "Small Integer",
                        "price": 12,
                        "available": False,
                    },
                    {
                        "id": "float",
                        "name": "Float Price",
                        "price": 12.5,
                        "productUrl": "https://shop/float",
                        "imageUrl": "https://img/float",
                    },
                    {
                        "id": "large-float",
                        "name": "Large Float",
                        "price": 1500.5,
                    },
                    {
                        "id": "string",
                        "name": "String Price",
                        "price": "$12.34",
                        "url_path": "https://shop/string",
                    },
                    {"name": "Missing Price", "price": None},
                    {"name": "Bad Price", "price": "not-money"},
                ]
            }
        }
    }

    items = throne._extract_items_next_data(_next_data_html(payload), "creator")

    assert items is not None
    by_name = {item.name: item for item in items}
    assert by_name["Cent Item"].price_cents == 1234
    assert by_name["Cent Item"].currency == "EUR"
    assert by_name["Cent Item"].product_url == "https://throne.com/creator/item/cents"
    assert by_name["Bad Cents"].price_cents == -1
    assert by_name["Large Integer"].price_cents == 1501
    assert by_name["Large Integer"].image_url == "https://img/extra"
    assert by_name["Small Integer"].price_cents == 1200
    assert not by_name["Small Integer"].available
    assert by_name["Float Price"].price_cents == 1250
    assert by_name["Large Float"].price_cents == 1500
    assert by_name["String Price"].price_cents == 1234
    assert by_name["Missing Price"].price_cents == -1
    assert by_name["Bad Price"].price_cents == -1
    assert by_name["Bad Price"].item_id


@pytest.mark.parametrize(
    ("offers", "expected"),
    (
        (None, (-1, "USD")),
        ([], (-1, "USD")),
        ({"price": "12.34", "priceCurrency": "GBP"}, (1234, "GBP")),
        ([{"price": 5}], (500, "USD")),
        ({"price": "bad"}, (-1, "USD")),
    ),
)
def test_parse_jsonld_offer_handles_shapes(
    offers: object, expected: tuple[int, str]
) -> None:
    """JSON-LD offer parsing handles dictionaries, lists, absence, and bad prices."""
    assert throne._parse_jsonld_offer(offers) == expected


def test_parse_jsonld_item_entry_handles_wrapped_and_invalid_entries() -> None:
    """JSON-LD item entries support wrappers and reject non-object values."""
    assert throne._parse_jsonld_item_entry("bad") is None
    item = throne._parse_jsonld_item_entry(
        {
            "item": {
                "@id": "item-id",
                "name": " Wrapped ",
                "url": "https://shop/item",
                "image": "https://img/item",
                "offers": {"price": 8, "priceCurrency": "EUR"},
            }
        }
    )

    assert item is not None
    assert item.item_id == "item-id"
    assert item.name == "Wrapped"
    assert item.price_cents == 800
    assert item.currency == "EUR"


def test_extract_items_jsonld_parses_lists_deduplicates_and_skips_bad_scripts() -> None:
    """JSON-LD extraction collects ItemLists while ignoring malformed and invalid entries."""
    item_list = {
        "@type": "ItemList",
        "itemListElement": [
            {"item": {"@id": "one", "name": "One", "url": "https://shop/one"}},
            {"item": {"@id": "one", "name": "One New", "url": "https://shop/one"}},
            "invalid",
        ],
    }
    html = '<script type="application/ld+json">{bad</script>' + _jsonld_html(
        [{"@type": "Thing"}, item_list]
    )

    items = throne._extract_items_jsonld(html)

    assert items is not None
    assert len(items) == 1
    assert items[0].name == "One New"
    assert throne._extract_items_jsonld(_jsonld_html({"@type": "Thing"})) is None


def test_extract_items_grid_parses_currencies_filters_and_deduplicates() -> None:
    """Grid extraction finds nearby prices, filters navigation, and deduplicates links."""
    html = """
    <nav><a href="/login">Login now $1.00</a></nav>
    <div><a href="/short">Hi</a><span>$2.00</span></div>
    <div><a href="/one">First Gift</a><span>€12,34</span></div>
    <div><a href="/one">First Gift Updated</a><span>€12,34</span></div>
    <section><div><a href="https://shop/two">Second Gift</a></div><span>£5.00</span></section>
    """

    items = throne._extract_items_grid(html)

    assert items is not None
    assert len(items) == 2
    by_currency = {item.currency: item for item in items}
    assert by_currency["EUR"].price_cents == 1234
    assert by_currency["EUR"].product_url == "https://throne.com/one"
    assert by_currency["GBP"].price_cents == 500
    assert by_currency["GBP"].product_url == "https://shop/two"
    assert throne._extract_items_grid('<a href="/none">No Price Gift</a>') is None


def test_fetch_items_prefers_next_data_and_records_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fetch orchestration uses NEXT_DATA first and returns its diagnostic dump."""
    html = _next_data_html({"items": [{"id": "one", "name": "One", "price": 10}]})
    dump = tmp_path / "dump.html"
    monkeypatch.setattr(throne, "_fetch", mock.Mock(return_value=html))
    monkeypatch.setattr(throne, "write_dump", mock.Mock(return_value=dump))

    result = throne.fetch_items("creator", "List")

    assert result.complete
    assert [item.item_id for item in result.items] == ["one"]
    assert result.dump_paths == [dump]


def test_fetch_items_falls_back_to_jsonld_then_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch orchestration tries JSON-LD and grid when earlier strategies are empty."""
    parsed = [Item("grid", "Grid Item")]
    monkeypatch.setattr(throne, "_fetch", mock.Mock(return_value="<html></html>"))
    monkeypatch.setattr(throne, "write_dump", mock.Mock(return_value=None))
    monkeypatch.setattr(
        throne, "_extract_items_next_data", mock.Mock(return_value=None)
    )
    monkeypatch.setattr(throne, "_extract_items_jsonld", mock.Mock(return_value=None))
    grid = mock.Mock(return_value=parsed)
    monkeypatch.setattr(throne, "_extract_items_grid", grid)

    result = throne.fetch_items("https://throne.com/creator")

    assert result.complete
    assert result.items == parsed
    grid.assert_called_once()


def test_fetch_items_returns_incomplete_when_no_strategy_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful page fetch with no parsed items is marked incomplete."""
    monkeypatch.setattr(throne, "_fetch", mock.Mock(return_value="<html></html>"))
    monkeypatch.setattr(throne, "write_dump", mock.Mock(return_value="dump.html"))

    result = throne.fetch_items("creator", "List")

    assert not result.complete
    assert result.failure_reason == "no_items_parsed"
    assert result.dump_paths == ["dump.html"]


def test_fetch_items_handles_retry_and_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch failures are normalized to incomplete results with useful reasons."""
    monkeypatch.setattr(
        throne, "_fetch", mock.Mock(side_effect=RetryError(mock.Mock()))
    )
    retry_result = throne.fetch_items("creator")
    monkeypatch.setattr(throne, "_fetch", mock.Mock(side_effect=RuntimeError("boom")))
    unexpected_result = throne.fetch_items("creator")

    assert not retry_result.complete
    assert "fetch_failed_after_retries" in (retry_result.failure_reason or "")
    assert not unexpected_result.complete
    assert unexpected_result.failure_reason == "unexpected_error: boom"
