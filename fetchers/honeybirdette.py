"""Honey Birdette wishlist fetcher: Shopify catalog scan with configurable
category/size markdown matching.

Honey Birdette's US storefront (us.honeybirdette.com) is a standard Shopify
store. Its public, unauthenticated `/products.json` endpoint paginates the
full product catalog and exposes per-variant price, compare_at_price,
availability, and named options (e.g. Size/CUP for bras). There is no
reliable sale-collection URL for this store (its "outlet" collection is
app-backed and its own /products.json endpoint returns no products), so this
fetcher discovers the full catalog directly and determines markdown state
per-variant from `compare_at_price > price`, which is the only authoritative,
live signal for a genuine markdown (as opposed to search-engine snippets,
promotional banners, or stale collection pages).
"""

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential_jitter

from core.dumps import write_dump
from core.logger import get_logger
from core.models import FetchResult, Item

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://us.honeybirdette.com"

USER_AGENT = os.getenv(
    "HONEYBIRDETTE_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
PAGE_LIMIT = 250
MAX_PAGES = int(os.getenv("HONEYBIRDETTE_MAX_PAGES", "20"))
PAGE_SLEEP_SECONDS = float(os.getenv("HONEYBIRDETTE_PAGE_SLEEP_SECONDS", "1"))
MIN_PLAUSIBLE_PRODUCTS = int(os.getenv("HONEYBIRDETTE_MIN_PRODUCTS", "50"))

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

# Config "type" (as used in options.matches) -> accepted Shopify product_type
# values (lowercased). Honey Birdette exposes stockings/stay-ups/garter-belt
# stockings under the single "Hosiery" product_type.
CATEGORY_PRODUCT_TYPES: dict[str, set[str]] = {
    "bra": {"bra"},
    "thong": {"thong"},
    "sheers": {"hosiery"},
    "stockings": {"hosiery"},
    "hosiery": {"hosiery"},
}


@dataclass
class MatchRule:
    """A single configured category/size match rule from options.matches."""

    category: str
    band: str | None = None
    cup: str | None = None
    size: str | None = None


def _normalize_base_url(identifier: str) -> str:
    """Resolve a config `identifier` to a base storefront URL."""
    ident = (identifier or "").strip()
    if ident.startswith(("http://", "https://")):
        return ident.rstrip("/")
    if "." in ident and " " not in ident:
        return f"https://{ident}".rstrip("/")
    return DEFAULT_BASE_URL


def _normalize_token(value: str) -> str:
    """Normalize whitespace/case for a size or band token."""
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def _normalize_cup(value: str) -> str:
    """Normalize a cup-size token, treating '-' and '/' interchangeably."""
    return _normalize_token(value).replace(" ", "").replace("-", "/")


def _parse_matches(options: dict[str, Any] | None) -> list[MatchRule]:
    """Parse and validate options.matches, skipping and logging bad entries."""
    if not isinstance(options, dict):
        return []
    raw_matches = options.get("matches")
    if not isinstance(raw_matches, list):
        return []

    rules: list[MatchRule] = []
    for entry in raw_matches:
        if not isinstance(entry, dict):
            logger.warning("Honey Birdette: skipping non-object match entry: %r", entry)
            continue

        match_type = str(entry.get("type", "")).strip().lower()
        if match_type not in CATEGORY_PRODUCT_TYPES:
            logger.warning(
                "Honey Birdette: skipping match entry with unknown type %r "
                "(expected one of %s).",
                entry.get("type"),
                sorted(CATEGORY_PRODUCT_TYPES),
            )
            continue

        if match_type == "bra":
            band = entry.get("band")
            cup = entry.get("cup")
            if (
                not isinstance(band, str)
                or not band.strip()
                or not isinstance(cup, str)
                or not cup.strip()
            ):
                logger.warning(
                    "Honey Birdette: skipping bra match entry missing band/cup: %r",
                    entry,
                )
                continue
            rules.append(
                MatchRule(category=match_type, band=band.strip(), cup=cup.strip())
            )
        else:
            size = entry.get("size")
            if not isinstance(size, str) or not size.strip():
                logger.warning(
                    "Honey Birdette: skipping %s match entry missing size: %r",
                    match_type,
                    entry,
                )
                continue
            rules.append(MatchRule(category=match_type, size=size.strip()))

    return rules


@retry(wait=wait_exponential_jitter(initial=1, max=30), stop=stop_after_attempt(5))
def _fetch_page(base_url: str, page: int) -> str:
    url = f"{base_url}/products.json?limit={PAGE_LIMIT}&page={page}"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _fetch_catalog(
    base_url: str, wishlist_name: str | None
) -> tuple[list[dict[str, Any]], list[Path | str], str | None]:
    """
    Fetch and concatenate every page of the product catalog.

    Returns (products, dump_paths, failure_reason). failure_reason is None on
    success. Any partial products collected before a failure are returned for
    diagnostics only and must not be treated as a complete/authoritative
    catalog by the caller.
    """
    products: list[dict[str, Any]] = []
    dump_paths: list[Path | str] = []

    for page in range(1, MAX_PAGES + 1):
        try:
            text = _fetch_page(base_url, page)
        except RetryError as e:
            logger.error(
                "Honey Birdette: page %d fetch failed after retries: %s", page, e
            )
            return products, dump_paths, f"fetch_failed_after_retries: {e}"
        except Exception as e:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            logger.error(
                "Honey Birdette: page %d fetch threw unexpected exception: %s", page, e
            )
            return products, dump_paths, f"unexpected_error: {e}"

        dump_path = write_dump("honeybirdette", wishlist_name, text, page_index=page)
        if dump_path:
            dump_paths.append(dump_path)

        try:
            data = json.loads(text)
        except (ValueError, TypeError) as e:
            logger.error(
                "Honey Birdette: page %d did not parse as JSON (possible anti-bot "
                "interstitial): %s",
                page,
                e,
            )
            return products, dump_paths, f"invalid_json_page_{page}: {e}"

        page_products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(page_products, list):
            logger.error(
                "Honey Birdette: page %d JSON missing a 'products' list; "
                "storefront structure may have changed.",
                page,
            )
            return products, dump_paths, f"missing_products_key_page_{page}"

        if not page_products:
            break

        products.extend(page_products)

        if len(page_products) < PAGE_LIMIT:
            break

        if page < MAX_PAGES:
            time.sleep(PAGE_SLEEP_SECONDS)
    else:
        logger.warning(
            "Honey Birdette: reached MAX_PAGES=%d without an empty page; catalog "
            "may be larger than expected and was truncated.",
            MAX_PAGES,
        )

    if len(products) < MIN_PLAUSIBLE_PRODUCTS:
        return (
            products,
            dump_paths,
            f"implausibly_low_product_count: {len(products)} < {MIN_PLAUSIBLE_PRODUCTS}",
        )

    return products, dump_paths, None


def _option_index(options: list[Any], name: str) -> int | None:
    """Return the 0-based index of the variant option named *name* (case-insensitive)."""
    for i, opt in enumerate(options or []):
        if isinstance(opt, dict) and str(opt.get("name", "")).strip().lower() == name:
            return i
    return None


def _variant_option_value(variant: dict[str, Any], index: int) -> str | None:
    value = variant.get(f"option{index + 1}")
    return value if isinstance(value, str) else None


def _product_image_url(product: dict[str, Any], variant: dict[str, Any]) -> str:
    featured = variant.get("featured_image")
    if isinstance(featured, dict) and isinstance(featured.get("src"), str):
        return featured["src"]
    images = product.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and isinstance(first.get("src"), str):
            return first["src"]
    return ""


def _parse_price_cents(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return round(float(raw) * 100)
    except (TypeError, ValueError):
        return None


def _bra_binding(
    options: list[Any], variant: dict[str, Any], rule: MatchRule
) -> str | None:
    size_idx = _option_index(options, "size")
    cup_idx = _option_index(options, "cup")
    if size_idx is None or cup_idx is None:
        return None
    band_val = _variant_option_value(variant, size_idx)
    cup_val = _variant_option_value(variant, cup_idx)
    if band_val is None or cup_val is None:
        return None
    if _normalize_token(band_val) != _normalize_token(rule.band or ""):
        return None
    if _normalize_cup(cup_val) != _normalize_cup(rule.cup or ""):
        return None
    return f"{band_val.strip()} {cup_val.strip()}"


def _single_size_binding(
    options: list[Any], variant: dict[str, Any], rule: MatchRule
) -> str | None:
    size_idx = _option_index(options, "size")
    if size_idx is None:
        return None
    size_val = _variant_option_value(variant, size_idx)
    if size_val is None:
        return None
    if _normalize_token(size_val) != _normalize_token(rule.size or ""):
        return None
    return size_val.strip()


def _variant_binding_for_rule(
    product: dict[str, Any], variant: dict[str, Any], rule: MatchRule
) -> str | None:
    """Return a normalized display binding (e.g. '32 DD/E') if the variant matches *rule*."""
    options = product.get("options")
    if not isinstance(options, list):
        return None
    if rule.category == "bra":
        return _bra_binding(options, variant, rule)
    return _single_size_binding(options, variant, rule)


def _build_item(
    product: dict[str, Any],
    variant: dict[str, Any],
    binding: str,
    base_url: str,
    sale_only: bool,
) -> Item | None:
    """Build an Item for a matched variant, or None if it should be excluded."""
    variant_id = variant.get("id")
    if variant_id is None:
        return None

    price_cents = _parse_price_cents(variant.get("price"))
    if price_cents is None or price_cents < 0:
        logger.debug(
            "Honey Birdette: skipping variant %s with unparseable price %r",
            variant_id,
            variant.get("price"),
        )
        return None

    compare_at_cents = _parse_price_cents(variant.get("compare_at_price"))
    if compare_at_cents is None or compare_at_cents <= 0:
        compare_at_cents = -1

    is_sale = compare_at_cents > 0 and compare_at_cents > price_cents
    if sale_only and not is_sale:
        return None

    handle = product.get("handle") or ""
    title = str(product.get("title") or "").strip()

    return Item(
        item_id=str(variant_id),
        name=title,
        price_cents=price_cents,
        currency="USD",
        product_url=f"{base_url}/products/{handle}" if handle else base_url,
        image_url=_product_image_url(product, variant),
        available=bool(variant.get("available", False)),
        binding=binding,
        compare_at_price_cents=compare_at_cents,
    )


def _match_products(
    products: list[dict[str, Any]],
    rules: list[MatchRule],
    base_url: str,
    sale_only: bool,
) -> list[Item]:
    """Match the fetched catalog against configured rules and build Items."""
    items: list[Item] = []
    seen_variant_ids: set[str] = set()

    for product in products:
        if not isinstance(product, dict):
            continue
        product_type = str(product.get("product_type") or "").strip().lower()
        variants = product.get("variants")
        if not isinstance(variants, list):
            continue

        applicable_rules = [
            rule
            for rule in rules
            if product_type in CATEGORY_PRODUCT_TYPES.get(rule.category, set())
        ]
        if not applicable_rules:
            continue

        for variant in variants:
            if not isinstance(variant, dict):
                continue

            binding = None
            for rule in applicable_rules:
                binding = _variant_binding_for_rule(product, variant, rule)
                if binding is not None:
                    break
            if binding is None:
                continue

            item = _build_item(product, variant, binding, base_url, sale_only)
            if item is None or item.item_id in seen_variant_ids:
                continue
            seen_variant_ids.add(item.item_id)
            items.append(item)

    return items


def fetch_items(
    identifier: str,
    wishlist_name: str | None = None,
    options: dict[str, Any] | None = None,
) -> FetchResult:
    """
    Fetch Honey Birdette's live US storefront catalog and return matching
    variants per `options.matches`.

    By default (`options.sale_only` unset or true) only variants with a
    genuine live markdown (`compare_at_price > price`) are returned, even if
    they're currently out of stock, so a restock can be detected as an
    availability change rather than a re-discovery. Set `sale_only` to false
    to track matching variants regardless of markdown state.

    Zero matching variants is a normal, expected outcome (no current sale in
    the configured sizes) and is reported via `FetchResult.allow_empty=True`
    rather than treated as a scrape failure.
    """
    base_url = _normalize_base_url(identifier)
    rules = _parse_matches(options)
    sale_only = True
    if isinstance(options, dict) and "sale_only" in options:
        sale_only = bool(options.get("sale_only"))

    if not rules:
        logger.warning(
            "Honey Birdette: wishlist '%s' has no valid options.matches configured; "
            "nothing to monitor.",
            wishlist_name or identifier,
        )

    logger.info(
        "Checking Honey Birdette catalog at %s for '%s' (%d match rule(s), sale_only=%s).",
        base_url,
        wishlist_name or identifier,
        len(rules),
        sale_only,
    )

    products, dump_paths, failure_reason = _fetch_catalog(base_url, wishlist_name)
    if failure_reason is not None:
        return FetchResult(
            items=[],
            dump_paths=dump_paths,
            complete=False,
            failure_reason=failure_reason,
        )

    items = _match_products(products, rules, base_url, sale_only)

    logger.info(
        "Honey Birdette: scanned %d catalog products, %d matching item(s) for '%s'.",
        len(products),
        len(items),
        wishlist_name or identifier,
    )

    return FetchResult(
        items=items, dump_paths=dump_paths, complete=True, allow_empty=True
    )
