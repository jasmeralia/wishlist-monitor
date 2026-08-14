"""Diff logic for computing added, removed, and price-changed items between polls."""

import os

from .models import Item
from .retention import env_bool

PRICE_NOTIFY_THRESHOLD = float(os.getenv("PRICE_NOTIFY_THRESHOLD", "20"))
NOTIFY_ON_AVAILABILITY_CHANGE = env_bool("NOTIFY_ON_AVAILABILITY_CHANGE", True)
NOTIFY_ON_PRICE_INCREASE = env_bool("NOTIFY_ON_PRICE_INCREASE", True)


def diff_items(
    previous: dict[str, Item], current: list[Item]
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
    """
    new_map = {it.item_id: it for it in current}
    old_ids = set(previous.keys())
    new_ids = set(new_map.keys())

    added = [new_map[iid] for iid in new_ids - old_ids]
    removed = [previous[iid] for iid in old_ids - new_ids]

    price_changes: list[tuple[Item, int, int]] = []

    for iid in old_ids & new_ids:
        old_item = previous[iid]
        new_item = new_map[iid]
        before = old_item.price_cents
        after = new_item.price_cents
        availability_changed = old_item.available != new_item.available

        if before == after and not availability_changed:
            continue

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
            if availability_changed and not NOTIFY_ON_AVAILABILITY_CHANGE:
                continue
            price_changes.append((new_item, before, after))
            continue

        # Threshold logic (like your Amazon monitor)
        if before == 0:
            pct = 100.0
        else:
            pct = abs(after - before) * 100.0 / abs(before)

        if pct < PRICE_NOTIFY_THRESHOLD:
            continue

        if after > before and not NOTIFY_ON_PRICE_INCREASE:
            continue

        price_changes.append((new_item, before, after))

    return added, removed, price_changes
