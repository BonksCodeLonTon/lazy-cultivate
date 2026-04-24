"""Tests for Đan Lô integration with shop and chest systems."""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.systems.chest import open_chest
from src.game.systems.economy import FIXED_SHOP_ITEMS, ROTATING_POOL


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Registry / data shape ───────────────────────────────────────────────────


def test_all_furnaces_load_and_have_tiers():
    furnaces = registry.all_furnaces()
    assert len(furnaces) == 8  # 4 normal + 4 unique
    for f in furnaces:
        assert 1 <= f["furnace_tier"] <= 4
        assert isinstance(f["is_unique"], bool)
        assert f["stack_max"] == 1
        if f["is_unique"]:
            assert f.get("quality_bonus"), f"unique furnace {f['key']} needs quality_bonus"


def test_unique_furnace_bonuses_escalate_with_tier():
    """Higher-tier unique furnaces should have strictly larger total quality bonus."""
    uniques = [f for f in registry.all_furnaces() if f.get("is_unique")]
    uniques.sort(key=lambda f: f["furnace_tier"])
    totals = [sum(f["quality_bonus"].values()) for f in uniques]
    assert totals == sorted(totals), f"unique furnace bonuses not monotonic: {totals}"


# ── Shop integration ────────────────────────────────────────────────────────


def test_normal_furnaces_are_listed_in_shops():
    """Normal Đan Lô must be buyable somewhere via the merit economy."""
    fixed_keys = {s["item_key"] for s in FIXED_SHOP_ITEMS}
    rotating_keys = {s["item_key"] for s in ROTATING_POOL}
    for grade in (1, 2, 3, 4):
        key = f"DanLoThuong_G{grade}"
        assert key in fixed_keys or key in rotating_keys, f"{key} missing from shop"


def test_unique_furnaces_are_NOT_sold_directly():
    """Unique Đan Lô should only be obtainable via chests, not the shop."""
    all_shop_keys = (
        {s["item_key"] for s in FIXED_SHOP_ITEMS}
        | {s["item_key"] for s in ROTATING_POOL}
    )
    uniques = {f["key"] for f in registry.all_furnaces() if f.get("is_unique")}
    overlap = uniques & all_shop_keys
    assert overlap == set(), f"unique furnaces leaked into shop: {overlap}"


def test_danlo_chests_are_buyable_in_rotating_pool():
    """At least the low-tier Đan Lô chests should appear in rotating shop pool."""
    rotating_keys = {s["item_key"] for s in ROTATING_POOL}
    assert "ChestDanLoG1" in rotating_keys
    assert "ChestDanLoG2" in rotating_keys


# ── Chest drop behaviour ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "chest_key,expected_unique",
    [
        ("ChestDanLoG1", "DanLoBachLuyen_G1"),
        ("ChestDanLoG2", "DanLoTuLinh_G2"),
        ("ChestDanLoG3", "DanLoHuyenHoang_G3"),
        ("ChestDanLoG4", "DanLoThienMenh_G4"),
    ],
)
def test_danlo_chest_drops_unique_furnace_at_high_rate(chest_key: str, expected_unique: str):
    """Opening a Đan Lô chest should yield the tier's unique furnace most of the time."""
    hits = 0
    trials = 200
    for seed in range(trials):
        r = open_chest(chest_key, rng=random.Random(seed))
        assert r.ok
        if any(d["item_key"] == expected_unique for d in r.loot):
            hits += 1
    # The unique has weight 100 vs normal weight 15 in the same pool (pool_id),
    # so it should hit at least ~75% of the time over 200 trials.
    assert hits / trials > 0.75, f"{chest_key} dropped {expected_unique} only {hits}/{trials}"


def test_duoc_vien_chest_drops_mostly_herbs_with_rare_furnace():
    hits_furnace = 0
    hits_herb = 0
    trials = 200
    for seed in range(trials):
        r = open_chest("ChestDuocVien", rng=random.Random(seed))
        for d in r.loot:
            item = registry.get_item(d["item_key"])
            if not item:
                continue
            if item.get("type") == "furnace":
                hits_furnace += 1
            elif item.get("type") == "herb":
                hits_herb += 1
    assert hits_herb > 0, "herbs are the primary drop of Dược Viên chest"
    # Furnace rate should be rare — under 20% of 200 chests
    assert hits_furnace < trials * 0.20
