"""Unit tests for item diffing behavior."""

import pytest

from core import diff
from core.diff import diff_items
from core.models import Item


def _item(item_id: str, price_cents: int = 100, available: bool = True) -> Item:
    """Build a minimal test item."""
    return Item(
        item_id=item_id,
        name=f"Item {item_id}",
        price_cents=price_cents,
        available=available,
    )


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


def test_availability_flip_without_price_change_is_reported() -> None:
    """An availability flip with an unchanged price is still reported (e.g. Throne)."""
    previous = {"item": _item("item", 100, available=True)}
    current = [_item("item", 100, available=False)]

    _, _, price_changes = diff_items(previous, current)

    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("item", 100, 100)
    ]


def test_availability_change_suppressed_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTIFY_ON_AVAILABILITY_CHANGE=false suppresses availability-only transitions."""
    monkeypatch.setattr(diff, "NOTIFY_ON_AVAILABILITY_CHANGE", False)
    previous = {
        "unavailable": _item("unavailable", 100, available=True),
        "price": _item("price", 100, available=True),
    }
    current = [
        _item("unavailable", -1, available=False),
        _item("price", 150, available=True),
    ]

    _, _, price_changes = diff_items(previous, current)

    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("price", 100, 150)
    ]


def test_price_increase_suppressed_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTIFY_ON_PRICE_INCREASE=false suppresses increases but not decreases."""
    monkeypatch.setattr(diff, "NOTIFY_ON_PRICE_INCREASE", False)
    previous = {
        "increase": _item("increase", 100, available=True),
        "decrease": _item("decrease", 200, available=True),
    }
    current = [
        _item("increase", 150, available=True),
        _item("decrease", 100, available=True),
    ]

    _, _, price_changes = diff_items(previous, current)

    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("decrease", 200, 100)
    ]


def test_price_increase_still_reported_when_availability_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTIFY_ON_PRICE_INCREASE only gates the threshold path, not availability flips."""
    monkeypatch.setattr(diff, "NOTIFY_ON_PRICE_INCREASE", False)
    previous = {"item": _item("item", -1, available=False)}
    current = [_item("item", 100, available=True)]

    _, _, price_changes = diff_items(previous, current)

    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("item", -1, 100)
    ]


def test_price_change_from_zero_uses_full_percentage() -> None:
    """A change from a zero price is treated as a full percentage change."""
    previous = {"item": _item("item", 0)}
    current = [_item("item", 1)]

    _, _, price_changes = diff_items(previous, current)

    assert [(item.item_id, before, after) for item, before, after in price_changes] == [
        ("item", 0, 1)
    ]
