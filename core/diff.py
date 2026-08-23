"""Diff logic for computing added, removed, and price-changed items between polls."""

import os
from typing import NamedTuple

from .models import Item
from .retention import env_bool

PRICE_NOTIFY_THRESHOLD = float(os.getenv("PRICE_NOTIFY_THRESHOLD", "20"))
NOTIFY_ON_AVAILABILITY_CHANGE = env_bool("NOTIFY_ON_AVAILABILITY_CHANGE", True)
NOTIFY_ON_PRICE_INCREASE = env_bool("NOTIFY_ON_PRICE_INCREASE", True)


class _DiffSettings(NamedTuple):
    """Resolved thresholds/toggles for one diff_items() call."""

    threshold: float
    decrease_threshold: float
    notify_availability: bool
    notify_increase: bool
    notify_decrease: bool


def _resolve_settings(
    price_notify_threshold: float | None,
    price_notify_threshold_decrease: float | None,
    notify_on_availability_change: bool | None,
    notify_on_price_increase: bool | None,
    notify_on_price_decrease: bool | None,
) -> _DiffSettings:
    """Merge per-call overrides over the module-level env-configured defaults."""
    threshold = (
        PRICE_NOTIFY_THRESHOLD
        if price_notify_threshold is None
        else price_notify_threshold
    )
    return _DiffSettings(
        threshold=threshold,
        decrease_threshold=(
            threshold
            if price_notify_threshold_decrease is None
            else price_notify_threshold_decrease
        ),
        notify_availability=(
            NOTIFY_ON_AVAILABILITY_CHANGE
            if notify_on_availability_change is None
            else notify_on_availability_change
        ),
        notify_increase=(
            NOTIFY_ON_PRICE_INCREASE
            if notify_on_price_increase is None
            else notify_on_price_increase
        ),
        notify_decrease=(
            True if notify_on_price_decrease is None else notify_on_price_decrease
        ),
    )


def _price_change_for_item(
    old_item: Item, new_item: Item, settings: _DiffSettings
) -> tuple[Item, int, int] | None:
    """Return the (item, before, after) price-change tuple for one item pair, or None."""
    before = old_item.price_cents
    after = new_item.price_cents
    availability_changed = old_item.available != new_item.available

    if before == after and not availability_changed:
        return None

    # If either price is unknown/negative, or availability itself flipped,
    # always include (subject to the availability-change toggle) rather than
    # applying the percentage threshold below.
    if (
        before is None
        or after is None
        or before < 0
        or after < 0
        or availability_changed
    ):
        if availability_changed and not settings.notify_availability:
            return None
        return (new_item, before, after)

    is_increase = after > before
    if is_increase and not settings.notify_increase:
        return None
    if not is_increase and after != before and not settings.notify_decrease:
        return None

    # Threshold logic (like your Amazon monitor)
    pct = 100.0 if before == 0 else abs(after - before) * 100.0 / abs(before)
    effective_threshold = (
        settings.threshold if is_increase else settings.decrease_threshold
    )
    if pct < effective_threshold:
        return None

    return (new_item, before, after)


def diff_items(
    previous: dict[str, Item],
    current: list[Item],
    *,
    price_notify_threshold: float | None = None,
    price_notify_threshold_decrease: float | None = None,
    notify_on_availability_change: bool | None = None,
    notify_on_price_increase: bool | None = None,
    notify_on_price_decrease: bool | None = None,
) -> tuple[list[Item], list[Item], list[tuple[Item, int, int]]]:
    """
    Compute added, removed, and price_changes between previous and current.
    - previous: mapping item_id -> Item
    - current: list of Items
    Returns:
      (added_items, removed_items, price_changes[(item_after, before_cents, after_cents)])

    An item whose `available` flag flips (with or without a price change alongside
    it, e.g. Amazon representing "unavailable" as a negative price) is reported
    through price_changes too, gated by NOTIFY_ON_AVAILABILITY_CHANGE instead of
    the percentage threshold below.

    The keyword-only parameters default to the module-level env-configured
    globals, preserving existing behavior for callers that don't pass them.
    They let a caller (e.g. per-wishlist notification policy) override
    thresholds/toggles for a single diff without touching global state.
    `price_notify_threshold_decrease`, when given, applies only to price
    decreases; increases and unset decreases keep using `price_notify_threshold`.
    """
    settings = _resolve_settings(
        price_notify_threshold,
        price_notify_threshold_decrease,
        notify_on_availability_change,
        notify_on_price_increase,
        notify_on_price_decrease,
    )

    new_map = {it.item_id: it for it in current}
    old_ids = set(previous.keys())
    new_ids = set(new_map.keys())

    added = [new_map[iid] for iid in new_ids - old_ids]
    removed = [previous[iid] for iid in old_ids - new_ids]

    price_changes: list[tuple[Item, int, int]] = []
    for iid in old_ids & new_ids:
        change = _price_change_for_item(previous[iid], new_map[iid], settings)
        if change is not None:
            price_changes.append(change)

    return added, removed, price_changes
