"""Fetcher registry: maps platform names to their fetch_items callables."""

from typing import Any

from . import amazon
from . import throne

FETCHERS: dict[str, Any] = {
    "amazon": amazon.fetch_items,
    "throne": throne.fetch_items,
}
