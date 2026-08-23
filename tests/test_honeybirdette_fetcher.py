"""Unit tests for the Honey Birdette Shopify catalog fetcher."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from tenacity import RetryError

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

from fetchers import honeybirdette

# All product fixtures default to a genuine live markdown (compare_at > price),
# matching BASIC_MATCHES below, so most tests only need to override what they're
# actually exercising. Tests about sale-detection itself set price/compare_at
# explicitly.


def _bra_product(
    handle: str = "test-bra",
    variant_id: int = 1001,
    band: str = "32",
    cup: str = "DD/E",
    price: str = "87.00",
    compare_at: str | None = "145.00",
    available: bool = True,
    cup_option_name: str = "CUP",
) -> dict[str, Any]:
    return {
        "id": 1,
        "title": "Test Bra",
        "handle": handle,
        "product_type": "Bra",
        "options": [{"name": "Size"}, {"name": cup_option_name}],
        "images": [{"src": "https://cdn.example.test/bra.jpg"}],
        "variants": [
            {
                "id": variant_id,
                "title": f"{band} / {cup}",
                "option1": band,
                "option2": cup,
                "option3": None,
                "price": price,
                "compare_at_price": compare_at,
                "available": available,
                "featured_image": None,
            }
        ],
    }


def _single_size_product(
    product_type: str,
    handle: str,
    variant_id: int,
    size: str,
    price: str = "20.00",
    compare_at: str | None = "35.00",
    available: bool = True,
) -> dict[str, Any]:
    return {
        "id": 2,
        "title": f"Test {product_type}",
        "handle": handle,
        "product_type": product_type,
        "options": [{"name": "Size"}],
        "images": [{"src": "https://cdn.example.test/item.jpg"}],
        "variants": [
            {
                "id": variant_id,
                "title": size,
                "option1": size,
                "option2": None,
                "option3": None,
                "price": price,
                "compare_at_price": compare_at,
                "available": available,
                "featured_image": None,
            }
        ],
    }


def _thong_product(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("size", "XS")
    return _single_size_product("Thong", "test-thong", 2001, **kwargs)


def _hosiery_product(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("size", "S")
    return _single_size_product("Hosiery", "test-stockings", 3001, **kwargs)


def _page_text(products: list[dict[str, Any]]) -> str:
    return json.dumps({"products": products})


def _single_page_fetcher(products: list[dict[str, Any]]) -> Any:
    """Return a `_fetch_page` replacement serving one page then empty pages."""

    def _fetch(_base_url: str, page: int) -> str:
        if page == 1:
            return _page_text(products)
        return _page_text([])

    return _fetch


BASIC_MATCHES = {
    "matches": [
        {"type": "bra", "band": "32", "cup": "DD/E"},
        {"type": "thong", "size": "XS"},
        {"type": "sheers", "size": "S"},
    ]
}


@pytest.fixture(autouse=True)
def _low_plausibility_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small fixture catalogs shouldn't trip the implausibly-low-count guard."""
    monkeypatch.setattr(honeybirdette, "MIN_PLAUSIBLE_PRODUCTS", 1)


def test_normalize_base_url_variants() -> None:
    """Identifiers normalize to a base storefront URL, defaulting to the US store."""
    assert honeybirdette._normalize_base_url("us") == honeybirdette.DEFAULT_BASE_URL
    assert honeybirdette._normalize_base_url("") == honeybirdette.DEFAULT_BASE_URL
    assert (
        honeybirdette._normalize_base_url("https://us.honeybirdette.com/")
        == "https://us.honeybirdette.com"
    )
    assert (
        honeybirdette._normalize_base_url("us.honeybirdette.com")
        == "https://us.honeybirdette.com"
    )


def test_normalize_token_and_cup() -> None:
    """Size/band tokens normalize whitespace/case; cup tokens also normalize separators."""
    assert honeybirdette._normalize_token(" xs ") == "XS"
    assert honeybirdette._normalize_token("32") == "32"
    assert honeybirdette._normalize_cup("DD/E") == "DD/E"
    assert honeybirdette._normalize_cup("dd-e") == "DD/E"
    assert honeybirdette._normalize_cup(" DD / E ") == "DD/E"


def test_parse_matches_accepts_valid_entries() -> None:
    """Valid bra, thong, and sheers/stockings match entries all parse."""
    rules = honeybirdette._parse_matches(BASIC_MATCHES)
    assert [r.category for r in rules] == ["bra", "thong", "sheers"]
    assert rules[0].band == "32"
    assert rules[0].cup == "DD/E"
    assert rules[1].size == "XS"


@pytest.mark.parametrize(
    "options",
    (
        None,
        {},
        {"matches": "not-a-list"},
        {"matches": ["not-a-dict"]},
        {"matches": [{"type": "unknown", "size": "XS"}]},
        {"matches": [{"type": "bra", "band": "32"}]},
        {"matches": [{"type": "bra", "cup": "DD/E"}]},
        {"matches": [{"type": "thong"}]},
    ),
)
def test_parse_matches_skips_invalid_entries(options: dict[str, Any] | None) -> None:
    """Malformed or unrecognized match entries are skipped, not raised."""
    assert honeybirdette._parse_matches(options) == []


def test_fetch_items_parses_matching_bra_thong_and_sheers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching 32 DD/E bra, XS thong, and S sheers/stockings variant are all found."""
    products = [_bra_product(), _thong_product(), _hosiery_product()]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert result.complete
    assert result.allow_empty
    by_binding = {it.binding: it for it in result.items}
    assert set(by_binding) == {"32 DD/E", "XS", "S"}
    assert by_binding["32 DD/E"].item_id == "1001"
    assert by_binding["32 DD/E"].price_cents == 8700
    assert by_binding["XS"].item_id == "2001"
    assert by_binding["S"].item_id == "3001"


def test_fetch_items_matches_case_variant_cup_option_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live storefront uses both 'CUP' and 'Cup' as the option name; both match."""
    products = [_bra_product(cup_option_name="Cup")]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert len(result.items) == 1


def test_fetch_items_excludes_wrong_size_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bra variant with the wrong band/cup is not matched, even though it's on sale."""
    products = [_bra_product(band="34", cup="D")]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert result.complete
    assert result.items == []


def test_fetch_items_excludes_wrong_product_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-matching product_type (e.g. a Dress in the right size) is never matched."""
    dress = _single_size_product("Dress", "test-dress", 4001, size="XS")
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher([dress]))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert result.items == []


def test_fetch_items_excludes_full_price_when_sale_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-price variants (no compare_at, or compare_at == price) are excluded by default."""
    products = [
        _bra_product(variant_id=101, price="145.00", compare_at=None),
        _bra_product(variant_id=102, price="145.00", compare_at="145.00"),
    ]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert result.items == []


def test_fetch_items_identifies_compare_at_greater_than_price_as_sale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compare_at_price > price is treated as a genuine live markdown."""
    products = [_bra_product(price="87.00", compare_at="145.00")]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.price_cents == 8700
    assert item.compare_at_price_cents == 14500


def test_fetch_items_includes_out_of_stock_sale_variant_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching sale variant that's out of stock stays in the snapshot as unavailable."""
    products = [_bra_product(available=False)]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert len(result.items) == 1
    assert result.items[0].available is False


def test_fetch_items_handles_unknown_price_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A variant with an unparseable price is skipped without failing the whole fetch."""
    broken = _bra_product(variant_id=1002, price="call-for-price", compare_at="145.00")
    healthy = _thong_product()
    monkeypatch.setattr(
        honeybirdette, "_fetch_page", _single_page_fetcher([broken, healthy])
    )

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert result.complete
    assert [it.item_id for it in result.items] == ["2001"]


def test_fetch_items_item_id_stable_across_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same live variant yields the same item_id on repeated fetches."""
    products = [_bra_product()]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    first = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)
    second = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert first.items[0].item_id == second.items[0].item_id == "1001"


def test_fetch_items_sale_only_false_includes_full_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sale_only=false tracks matching variants regardless of markdown state."""
    products = [_bra_product(price="145.00", compare_at=None)]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))
    options = {**BASIC_MATCHES, "sale_only": False}

    result = honeybirdette.fetch_items("us", "HB Test", options)

    assert len(result.items) == 1
    assert result.items[0].compare_at_price_cents == -1


def test_fetch_items_no_matches_configured_is_a_complete_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No valid match rules yields a complete, allow_empty result rather than a failure."""
    products = [_bra_product()]
    monkeypatch.setattr(honeybirdette, "_fetch_page", _single_page_fetcher(products))

    result = honeybirdette.fetch_items("us", "HB Test", {"matches": []})

    assert result.complete
    assert result.allow_empty
    assert result.items == []


def test_fetch_items_missing_products_key_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structurally changed response (no 'products' list) fails closed."""
    monkeypatch.setattr(
        honeybirdette,
        "_fetch_page",
        lambda _base, _page: json.dumps({"unexpected": True}),
    )

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert not result.complete
    assert "missing_products_key" in (result.failure_reason or "")


def test_fetch_items_invalid_json_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON response (e.g. an anti-bot interstitial) fails closed."""
    monkeypatch.setattr(
        honeybirdette,
        "_fetch_page",
        lambda _base, _page: "<html>are you a robot?</html>",
    )

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert not result.complete
    assert "invalid_json_page" in (result.failure_reason or "")


def test_fetch_items_implausibly_low_product_count_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalog far smaller than expected fails closed instead of a false empty success."""
    monkeypatch.setattr(honeybirdette, "MIN_PLAUSIBLE_PRODUCTS", 5)
    monkeypatch.setattr(
        honeybirdette, "_fetch_page", _single_page_fetcher([_bra_product()])
    )

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert not result.complete
    assert "implausibly_low_product_count" in (result.failure_reason or "")


def test_fetch_items_retry_error_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated fetch failures are normalized to an incomplete result."""
    monkeypatch.setattr(
        honeybirdette, "_fetch_page", mock.Mock(side_effect=RetryError(mock.Mock()))
    )

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert not result.complete
    assert "fetch_failed_after_retries" in (result.failure_reason or "")


def test_fetch_items_unexpected_error_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception during fetch is normalized to an incomplete result."""
    monkeypatch.setattr(
        honeybirdette, "_fetch_page", mock.Mock(side_effect=RuntimeError("boom"))
    )

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert not result.complete
    assert "unexpected_error" in (result.failure_reason or "")


def test_fetch_items_paginates_until_a_short_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple full pages are concatenated until a short/empty page ends pagination."""
    monkeypatch.setattr(honeybirdette, "PAGE_LIMIT", 2)
    monkeypatch.setattr(honeybirdette, "PAGE_SLEEP_SECONDS", 0)

    # Page 1 items don't match any configured rule (wrong band/cup); page 2's does.
    page1 = [
        _bra_product(variant_id=1, handle="p1", band="34", cup="D"),
        _bra_product(variant_id=2, handle="p2", band="34", cup="D"),
    ]
    page2 = [_bra_product(variant_id=3, handle="p3")]

    def _fetch(_base_url: str, page: int) -> str:
        if page == 1:
            return _page_text(page1)
        if page == 2:
            return _page_text(page2)
        return _page_text([])

    monkeypatch.setattr(honeybirdette, "_fetch_page", _fetch)

    result = honeybirdette.fetch_items("us", "HB Test", BASIC_MATCHES)

    assert result.complete
    assert [it.item_id for it in result.items] == ["3"]
