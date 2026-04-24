"""Tests for src/game/systems/chest.py."""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.systems.chest import ChestOpenResult, open_chest


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def test_open_unknown_chest_returns_error():
    result = open_chest("ChestDoesNotExist")
    assert isinstance(result, ChestOpenResult)
    assert result.ok is False
    assert result.loot == []
    assert "ChestDoesNotExist" in result.message


def test_open_chest_hoang_returns_loot():
    result = open_chest("ChestHoang", rng=random.Random(42))
    assert result.ok is True
    assert result.message == ""
    assert isinstance(result.loot, list)
    # loot entries are dicts with item_key + quantity
    for drop in result.loot:
        assert "item_key" in drop and "quantity" in drop
        assert drop["quantity"] >= 1


def test_open_chest_is_deterministic_with_seeded_rng():
    a = open_chest("ChestHuyen", rng=random.Random(7))
    b = open_chest("ChestHuyen", rng=random.Random(7))
    assert a.loot == b.loot
