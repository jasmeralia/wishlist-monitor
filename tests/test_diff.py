"""Unit tests for item diffing behavior."""

import unittest

from core.diff import diff_items
from core.models import Item


def _item(item_id: str, price_cents: int = 100) -> Item:
    """Build a minimal test item."""
    return Item(item_id=item_id, name=f"Item {item_id}", price_cents=price_cents)


class DiffItemsTests(unittest.TestCase):
    """Tests for added, removed, and price-change diff logic."""

    def test_added_removed_and_price_changes_are_reported(self) -> None:
        """Diffing reports all three supported change types."""
        previous = {
            "kept": _item("kept", 100),
            "removed": _item("removed", 200),
            "price": _item("price", 100),
        }
        current = [_item("kept", 100), _item("price", 150), _item("added", 300)]

        added, removed, price_changes = diff_items(previous, current)

        self.assertEqual([item.item_id for item in added], ["added"])
        self.assertEqual([item.item_id for item in removed], ["removed"])
        self.assertEqual(
            [(item.item_id, before, after) for item, before, after in price_changes],
            [("price", 100, 150)],
        )

    def test_small_price_change_below_threshold_is_ignored(self) -> None:
        """Price changes below the default percentage threshold are ignored."""
        previous = {"price": _item("price", 100)}
        current = [_item("price", 110)]

        _, _, price_changes = diff_items(previous, current)

        self.assertEqual(price_changes, [])

    def test_unknown_price_changes_are_always_reported(self) -> None:
        """Unknown price changes should be visible even without a percentage."""
        previous = {"price": _item("price", -1)}
        current = [_item("price", 100)]

        _, _, price_changes = diff_items(previous, current)

        self.assertEqual(
            [(item.item_id, before, after) for item, before, after in price_changes],
            [("price", -1, 100)],
        )


if __name__ == "__main__":
    unittest.main()
