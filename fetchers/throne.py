"""Throne wishlist fetcher with three extraction strategies: NEXT_DATA, JSON-LD, grid."""

import os
import json
import re
import hashlib
from pathlib import Path
from typing import Any, List, Optional

import requests
from bs4 import BeautifulSoup, Tag
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, RetryError

from core.dumps import write_dump
from core.models import FetchResult, Item
from core.logger import get_logger

logger = get_logger(__name__)


USER_AGENT = os.getenv(
    "THRONE_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
PROXY_URL = os.getenv("THRONE_PROXY_URL", "").strip()
DEBUG_LOG_SAMPLES = os.getenv("THRONE_DEBUG_LOG_SAMPLES", "true").lower() == "true"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
if PROXY_URL:
    SESSION.proxies.update({"http": PROXY_URL, "https": PROXY_URL})


def _normalize_target(target: str) -> str:
    if target.startswith("http://") or target.startswith("https://"):
        return target
    return f"https://throne.com/{target}"


def _extract_slug(identifier: str) -> str:
    """Extract the Throne username slug from an identifier (username or URL)."""
    s = identifier.strip()
    s = re.sub(r"^https?://(?:www\.)?throne\.com/", "", s)
    s = s.strip("/")
    return s.split("/", 1)[0]


@retry(wait=wait_exponential_jitter(initial=1, max=30), stop=stop_after_attempt(5))
def _fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _extract_items_next_data(html: str, slug: str = "") -> Optional[List[Item]]:
    """Extract items from the __NEXT_DATA__ JSON script tag."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        data = json.loads(script.string)
    except Exception:  # pylint: disable=broad-exception-caught
        return None

    found: list[Any] = []

    def is_item_list(lst: Any) -> bool:
        if not isinstance(lst, list):
            return False
        count = 0
        for x in lst:
            if isinstance(x, dict) and (
                ("name" in x or "title" in x)
                and any(k in x for k in ("price", "price_cents", "priceCents"))
            ):
                count += 1
        return count >= 1

    def deep_iter(node: Any) -> None:
        nonlocal found
        if isinstance(node, dict):
            for v in node.values():
                deep_iter(v)
        elif isinstance(node, list):
            if is_item_list(node):
                found = node
                return
            for v in node:
                deep_iter(v)

    deep_iter(data)
    if not found:
        return None

    items: List[Item] = []
    for it in found:
        name = it.get("name") or it.get("title") or ""
        price = None
        price_field = None
        for k in ("price", "price_cents", "priceCents", "priceCents"):
            if k in it:
                price = it.get(k)
                price_field = k
                break
        currency = it.get("currency") or it.get("currencyCode") or "USD"
        url = it.get("url") or it.get("productUrl") or it.get("url_path") or ""
        uuid_id = it.get("id") or it.get("itemId") or it.get("uuid")
        if not url and slug and uuid_id:
            url = f"https://throne.com/{slug}/item/{uuid_id}"

        # Prefer explicit image fields from Throne JSON; fall back to extras.
        image_val = it.get("imgLink") or it.get("image") or it.get("imageUrl")

        if not image_val:
            extra_imgs = it.get("extraImgLinks")
            if isinstance(extra_imgs, list) and extra_imgs:
                image_val = extra_imgs[0]

        image = str(image_val) if image_val is not None else ""
        item_id = (
            it.get("id")
            or it.get("uuid")
            or (url and hashlib.sha1(url.encode()).hexdigest())
        )
        avail = it.get("available")
        if avail is None:
            avail = 1

        price_cents = None
        if price is None:
            price_cents = -1
        else:
            if price_field and "cent" in price_field.lower():
                try:
                    price_cents = int(price)
                except Exception:  # pylint: disable=broad-exception-caught
                    price_cents = -1
            elif isinstance(price, int):
                price_cents = price if price > 1000 else int(round(float(price) * 100))
            elif isinstance(price, float):
                price_cents = int(round(price * 100)) if price < 1000 else int(price)
            else:
                s = (
                    str(price)
                    .strip()
                    .replace("$", "")
                    .replace("£", "")
                    .replace("€", "")
                    .replace(",", "")
                )
                try:
                    if re.fullmatch(r"\d+", s):
                        v = int(s)
                        price_cents = v if v > 1000 else int(round(v * 100))
                    else:
                        price_cents = int(round(float(s) * 100))
                except Exception:  # pylint: disable=broad-exception-caught
                    price_cents = -1

        items.append(
            Item(
                item_id=str(item_id)
                if item_id
                else hashlib.sha1((name + url).encode()).hexdigest(),
                name=str(name).strip(),
                price_cents=price_cents if price_cents is not None else -1,
                currency=currency,
                product_url=url,
                image_url=image or "",
                available=bool(avail),
            )
        )

    return items


def _parse_jsonld_offer(offers: Any, default_currency: str = "USD") -> tuple[int, str]:
    """Extract (price_cents, currency) from a JSON-LD offers object or list."""
    price_cents = -1
    currency = default_currency
    offer = (
        offers
        if isinstance(offers, dict)
        else (offers[0] if isinstance(offers, list) and offers else None)
    )
    if offer is None:
        return price_cents, currency
    price = offer.get("price")
    currency = offer.get("priceCurrency") or currency
    try:
        if price is not None:
            price_cents = int(round(float(str(price)) * 100))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return price_cents, currency


def _parse_jsonld_item_entry(el: Any) -> Optional[Item]:
    """Parse a single JSON-LD itemListElement entry into an Item, or return None."""
    item = el.get("item") if isinstance(el, dict) else el
    if not isinstance(item, dict):
        return None
    name = item.get("name") or ""
    url = item.get("url") or ""
    offers = item.get("offers")
    price_cents, currency = _parse_jsonld_offer(offers) if offers else (-1, "USD")
    item_id = item.get("@id") or (url and hashlib.sha1(url.encode()).hexdigest())
    return Item(
        item_id=str(item_id)
        if item_id
        else hashlib.sha1((name + url).encode()).hexdigest(),
        name=name.strip(),
        price_cents=price_cents,
        currency=currency,
        product_url=url or "",
        image_url=item.get("image") or "",
        available=True,
    )


def _extract_items_jsonld(html: str) -> Optional[List[Item]]:
    """Extract items from JSON-LD ItemList script tags."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Item] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        data_list = data if isinstance(data, list) else [data]
        for d in data_list:
            if d.get("@type") == "ItemList" and isinstance(
                d.get("itemListElement"), list
            ):
                for el in d["itemListElement"]:
                    item = _parse_jsonld_item_entry(el)
                    if item is not None:
                        out.append(item)
    if not out:
        return None
    uniq: dict[str, Item] = {}
    for it in out:
        uniq[it.item_id] = it
    return list(uniq.values())


def _extract_items_grid(html: str) -> Optional[List[Item]]:
    """Extract items by scanning anchor tags with adjacent price text."""
    soup = BeautifulSoup(html, "html.parser")
    items: List[Item] = []
    price_re = re.compile(r"(?<!\w)([$€£])\s?([0-9]+(?:[.,][0-9]{2})?)")

    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if not txt or len(txt) < 3:
            continue
        lower = txt.lower()
        if any(
            k in lower
            for k in (
                "login",
                "sign up",
                "about",
                "contact",
                "faq",
                "feature requests",
                "how it works",
                "follow",
                "wishlist",
                "gifters",
            )
        ):
            continue

        price_cents = -1
        currency = "USD"
        container = a
        found_price = False
        for _ in range(4):
            if container is None:
                break
            text_block = container.get_text(" ", strip=True)
            m = price_re.search(text_block or "")
            if m:
                symbol, num = m.groups()
                if symbol == "€":
                    currency = "EUR"
                elif symbol == "£":
                    currency = "GBP"
                try:
                    price_cents = int(round(float(num.replace(",", ".")) * 100))
                except Exception:  # pylint: disable=broad-exception-caught
                    price_cents = -1
                found_price = True
                break
            parent = container.parent
            if isinstance(parent, Tag):
                container = parent
            else:
                break
        if not found_price:
            continue

        href_raw = a.get("href")
        if not isinstance(href_raw, str):
            continue

        href = href_raw
        if href.startswith("/"):
            href = "https://throne.com" + href
        key = href or txt
        item_id = hashlib.sha1(key.encode()).hexdigest()

        items.append(
            Item(
                item_id=item_id,
                name=txt,
                price_cents=price_cents,
                currency=currency,
                product_url=href,
                image_url="",
                available=True,
            )
        )

    if not items:
        return None

    uniq: dict[str, Item] = {}
    for it in items:
        uniq[it.item_id] = it
    return list(uniq.values())


def fetch_items(identifier: str, wishlist_name: str | None = None) -> FetchResult:
    """Fetch items from a Throne wishlist (by username or URL)."""
    url = _normalize_target(identifier)
    logger.info("Checking Throne wishlist '%s' at %s", wishlist_name or identifier, url)

    try:
        html = _fetch(url)
        dump_path = write_dump("throne", wishlist_name, html)
    except RetryError as e:
        logger.error("Throne fetch failed for %s after retries: %s", url, e)
        return FetchResult(
            items=[],
            dump_paths=[],
            complete=False,
            failure_reason=f"fetch_failed_after_retries: {e}",
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Throne fetch threw unexpected exception for %s: %s", url, e)
        return FetchResult(
            items=[],
            dump_paths=[],
            complete=False,
            failure_reason=f"unexpected_error: {e}",
        )

    dump_paths: list[Path | str] = [dump_path] if dump_path else []

    slug = _extract_slug(identifier)
    items = _extract_items_next_data(html, slug)
    if not items:
        logger.debug("Throne NEXT_DATA extraction failed or empty; trying JSON-LD")
        items = _extract_items_jsonld(html)
    if not items:
        logger.debug("Throne JSON-LD extraction failed or empty; trying grid")
        items = _extract_items_grid(html)

    if not items:
        logger.warning("Throne parsed 0 items for %s.", url)
        return FetchResult(
            items=[],
            dump_paths=dump_paths,
            complete=False,
            failure_reason="no_items_parsed",
        )

    if DEBUG_LOG_SAMPLES:
        logger.debug("Throne sample items for %s: %s", url, items[:3])

    logger.info("Throne: found %d items for %s", len(items), url)
    return FetchResult(items=items, dump_paths=dump_paths, complete=True)
