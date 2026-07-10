"""Thiên Mệnh Thạch — the ultrarare "drops anywhere" unlock/swap stone.

Covers the three seams the feature touches:
  * loot: ``inject_global_drops`` + ``GameRegistry.get_loot_table`` append the
    stone to EVERY non-empty table (zones, dungeons, chests, world bosses) at
    a uniform absolute weight;
  * unlock: ``_required_materials`` merges the rarity-based stone cost on top
    of any per-entry materials (Phàm Thể exempt);
  * swap: ``resolve_constitution_swap`` accepts the stone as fallback currency,
    preferring the dedicated Hoán Thể Tinh.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.constants.constitution_process import THIEN_MENH_THACH_KEY
from src.game.engine.loot import inject_global_drops
from src.game.systems.constitution_process import resolve_constitution_swap


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Item data ────────────────────────────────────────────────────────────────


def test_stone_item_exists() -> None:
    item = registry.get_item(THIEN_MENH_THACH_KEY)
    assert item is not None
    assert item["type"] == "constitution_material"
    assert item["grade"] == 4


# ── Global drop injection ────────────────────────────────────────────────────


def test_inject_global_drops_empty_table_stays_empty() -> None:
    assert inject_global_drops([], registry.global_drops) == []


def test_inject_global_drops_returns_copies() -> None:
    out = inject_global_drops([{"item_key": "X", "weight": 100}], registry.global_drops)
    assert out == registry.global_drops
    out[0]["weight"] = 999_999
    assert registry.global_drops[0]["weight"] != 999_999


def test_stone_weight_is_ultrarare_independent_entry() -> None:
    """Absolute weight out of POOL_RANGE (1M): must stay a fraction of a
    percent and roll independently (no pool_id)."""
    (entry,) = [
        e for e in registry.global_drops if e["item_key"] == THIEN_MENH_THACH_KEY
    ]
    assert 0 < entry["weight"] <= 1_000  # ≤0.1% per roll
    assert "pool_id" not in entry


def test_stone_injected_into_every_loot_source_kind() -> None:
    """Zones, dungeon families, chests, and world bosses all carry the stone."""
    sample_keys = ["LootZone_1", "LootZone_9"]
    for prefix in ("LootChest", "LootTVDS", "LootTheChat", "LootWB", "LootBoss"):
        sample_keys += [k for k in registry.loot_tables if k.startswith(prefix)][:2]
    checked = 0
    for key in sample_keys:
        if not registry.loot_tables.get(key):
            continue
        table = registry.get_loot_table(key)
        stones = [e for e in table if e["item_key"] == THIEN_MENH_THACH_KEY]
        assert len(stones) == 1, key
        checked += 1
    assert checked >= 6  # the sweep actually covered multiple source kinds


def test_stone_injected_into_all_static_tables() -> None:
    for key, static in registry.loot_tables.items():
        if not static:
            continue
        keys = [e["item_key"] for e in registry.get_loot_table(key)]
        assert THIEN_MENH_THACH_KEY in keys, key


def test_unknown_table_key_yields_no_stone() -> None:
    assert registry.get_loot_table("LootDoesNotExist") == []


def test_static_json_untouched_by_injection() -> None:
    """get_loot_table must not mutate the registry's static table in place."""
    key = "LootZone_1"
    before = len(registry.loot_tables[key])
    registry.get_loot_table(key)
    registry.get_loot_table(key)
    assert len(registry.loot_tables[key]) == before


# ── Unlock (activation) cost ─────────────────────────────────────────────────


def test_legendary_unlock_requires_one_stone() -> None:
    from src.bot.cogs.constitution import _required_materials

    body = registry.get_constitution("TheChat_CuuThienCuongPhong")
    mats = _required_materials(body)
    assert mats[THIEN_MENH_THACH_KEY] == 1


def test_mythic_unlock_requires_two_stones_plus_own_mats() -> None:
    from src.bot.cogs.constitution import _required_materials

    body = registry.get_constitution("TheChat_HoangCoThanhThe")
    mats = _required_materials(body)
    assert mats[THIEN_MENH_THACH_KEY] == 2
    assert mats.get("HoangCoLongLan") == 1  # per-entry mats preserved


def test_pham_the_needs_no_stone() -> None:
    from src.bot.cogs.constitution import _required_materials

    mats = _required_materials(registry.get_constitution("ConstitutionPhamThe"))
    assert THIEN_MENH_THACH_KEY not in mats


# ── Swap currency ────────────────────────────────────────────────────────────


def test_swap_prefers_hoan_the_tinh_when_owned() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB", "ConstitutionA,ConstitutionB",
        "ConstitutionB", owned_essence_qty=1, owned_stone_qty=5,
    )
    assert out["outcome"] == "SWAPPED"
    assert out["consumed"] == {"ConsProcHoanTheTinh": 1}


def test_swap_falls_back_to_stone() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB", "ConstitutionA,ConstitutionB",
        "ConstitutionB", owned_essence_qty=0, owned_stone_qty=1,
    )
    assert out["outcome"] == "SWAPPED"
    assert out["consumed"] == {THIEN_MENH_THACH_KEY: 1}


def test_swap_rejects_with_neither_currency() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB", "ConstitutionA,ConstitutionB",
        "ConstitutionB", owned_essence_qty=0, owned_stone_qty=0,
    )
    assert out == {"outcome": "NO_ESSENCE"}
