"""Unit tests for item diffing behavior."""

from core.diff import diff_items
from core.models import Item


def _item(item_id: str, price_cents: int = 100) -> Item:
    """Build a minimal test item."""
    return Item(item_id=item_id, name=f"Item {item_id}", price_cents=price_cents)


def test_added_removed_and_price_changes_are_reported() -> None:
    """Diffing reports all three supported change types."""
    previous = {
        "kept": _item("kept", 100),
        "removed": _item("removed", 200),
        "price": _item("price", 100),
    }
    current = [_item("kept", 100), _item("price", 150), _item("added", 300)]

    added, removed, price_changes = diff_items(previous, current)

    assert [item.item_id for item in added] == ["added"]
    assert [item.item_id for item in removed] == ["removed"]
    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("price", 100, 150)
    ]


def test_small_price_change_below_threshold_is_ignored() -> None:
    """Price changes below the default percentage threshold are ignored."""
    previous = {"price": _item("price", 100)}
    current = [_item("price", 110)]

    _, _, price_changes = diff_items(previous, current)

    assert price_changes == []


def test_unknown_price_changes_are_always_reported() -> None:
    """Unknown price changes should be visible even without a percentage."""
    previous = {"price": _item("price", -1)}
    current = [_item("price", 100)]

    _, _, price_changes = diff_items(previous, current)

    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("price", -1, 100)
    ]
