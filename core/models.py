"""Data models shared across all fetchers and the monitor core."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Item:  # pylint: disable=too-many-instance-attributes
    """
    Normalized representation of a wishlist item across all platforms.
    Prices are stored in cents for consistency.
    """

    item_id: str
    name: str
    price_cents: int = -1
    currency: str = "USD"
    product_url: str = ""
    image_url: str = ""
    available: bool = True
    binding: str = ""
    # Original/list price in cents when the platform exposes one; -1 if unknown.
    compare_at_price_cents: int = -1


@dataclass
class FetchResult:
    """
    Result of a wishlist fetch, including whether the item list is complete.

    Partial fetches can be useful for diagnostics, but must not be treated as a
    current wishlist snapshot for removal detection.
    """

    items: list[Item]
    dump_paths: list[Path | str]
    complete: bool = True
    failure_reason: str | None = None
    # True when a *complete* fetch legitimately produced zero items (e.g. a
    # sale-only monitor with no current matches), as opposed to zero items
    # meaning a scrape/parse problem. Lets the monitor process an empty
    # snapshot (including recording removals) instead of skipping the cycle.
    allow_empty: bool = False
