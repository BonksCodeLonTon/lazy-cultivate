"""Phase 5 validation tests for the Skill Mastery items + drops.

Pure data tests — no DB, no combat. Covers:
- All six mastery items resolve with the right grade/type/price/text.
- Only the Dao Fruit (MasteryThongThienDaoQua) is hidden.
- Every item_key the core gate table references resolves as a real item.
- Drop placement per zone matches the design contract.
- The fruit is a genuine chase drop in the world-boss table.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.registry import registry
from src.game.constants.skill_mastery import (
    DINH_DAO_CHAU_KEY,
    GATES,
    HO_DAO_PHU_KEY,
)

ROOT = Path(__file__).resolve().parents[1]
LOOT_DIR = ROOT / "src" / "data" / "loot_tables"

# Expected grade per mastery item key.
EXPECTED_GRADES: dict[str, int] = {
    "MasteryLinhNgoPhu": 1,
    "MasteryTamDacNgoc": 2,
    "MasteryDaoVanTinh": 3,
    "MasteryThongThienDaoQua": 4,
    "MasteryHoDaoPhu": 2,
    "MasteryDinhDaoChau": 3,
}
ALL_MASTERY_KEYS = list(EXPECTED_GRADES)
FRUIT_KEY = "MasteryThongThienDaoQua"


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _read_loot(filename: str) -> dict:
    path = LOOT_DIR / filename
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _zone_entries(zone_idx: int) -> list[dict]:
    """Flatten all drop entries across every table in a zone file."""
    data = _read_loot(f"zone_{zone_idx:02d}.json")
    entries: list[dict] = []
    for table in data.values():
        entries.extend(table)
    return entries


def _entries_for_key_in_zone(zone_idx: int, item_key: str) -> list[dict]:
    return [e for e in _zone_entries(zone_idx) if e["item_key"] == item_key]


def _zone_has(zone_idx: int, item_key: str) -> bool:
    return bool(_entries_for_key_in_zone(zone_idx, item_key))


# ── 1. Resolution + fields ──────────────────────────────────────────────────

def test_all_six_mastery_items_resolve():
    for key, expected_grade in EXPECTED_GRADES.items():
        item = registry.get_item(key)
        assert item is not None, f"{key} did not resolve via registry.get_item"
        assert item["type"] == "mastery_material", f"{key} wrong type"
        assert item["grade"] == expected_grade, f"{key} wrong grade"
        # Drop-only: never purchasable for merit.
        assert item.get("shop_price_merit") == 0, f"{key} should be drop-only"
        # Display text must be present.
        assert item.get("vi"), f"{key} missing vi"
        assert item.get("en"), f"{key} missing en"
        assert item.get("description_vi"), f"{key} missing description_vi"


# ── 2. Hidden flag ──────────────────────────────────────────────────────────

def test_only_fruit_is_hidden():
    fruit = registry.get_item(FRUIT_KEY)
    assert fruit.get("hidden") is True, "fruit must be hidden"
    for key in ALL_MASTERY_KEYS:
        if key == FRUIT_KEY:
            continue
        item = registry.get_item(key)
        assert not item.get("hidden"), f"{key} must NOT be hidden"


# ── 3. Core ↔ data contract ─────────────────────────────────────────────────

def test_item_keys_match_core_gate_table():
    referenced: set[str] = set()
    for ceiling in (5, 10, 15, 20):
        gate = GATES[ceiling]
        referenced.add(gate["item_key"])
    referenced.add(HO_DAO_PHU_KEY)
    referenced.add(DINH_DAO_CHAU_KEY)

    for key in referenced:
        item = registry.get_item(key)
        assert item is not None, f"core references {key} but registry has no such item"
        assert item["type"] == "mastery_material", f"{key} resolved but wrong type"


# ── 4. Drop placement by zone ───────────────────────────────────────────────

def test_drop_placement_by_zone():
    # (item_key, set-of-zones-it-MUST-appear-in)
    placement: dict[str, set[int]] = {
        "MasteryLinhNgoPhu": {1, 2, 3, 4},
        "MasteryTamDacNgoc": {5, 6, 7, 8},
        "MasteryDaoVanTinh": {9, 10},
        "MasteryHoDaoPhu": {3, 4, 5, 6, 7, 8},
        "MasteryDinhDaoChau": {7, 8, 9, 10},
    }

    for item_key, expected_zones in placement.items():
        present = {z for z in range(1, 11) if _zone_has(z, item_key)}
        assert present == expected_zones, (
            f"{item_key} placement mismatch: "
            f"expected zones {sorted(expected_zones)}, found {sorted(present)}"
        )
        # Every present entry drops exactly 1.
        for z in present:
            for entry in _entries_for_key_in_zone(z, item_key):
                assert entry["qty_min"] == 1, f"{item_key} zone_{z:02d} qty_min != 1"
                assert entry["qty_max"] == 1, f"{item_key} zone_{z:02d} qty_max != 1"

    # The fruit lives ONLY in world_bosses.json — never in any zone_*.
    fruit_zones = {z for z in range(1, 11) if _zone_has(z, FRUIT_KEY)}
    assert fruit_zones == set(), (
        f"{FRUIT_KEY} must not appear in any zone; found {sorted(fruit_zones)}"
    )


# ── 5. Fruit is an ultra-rare chase drop ────────────────────────────────────

def test_fruit_is_ultra_rare_in_world_boss_table():
    world_bosses = _read_loot("world_bosses.json")

    fruit_lists = 0
    for boss_key, table in world_bosses.items():
        fruit_entries = [e for e in table if e["item_key"] == FRUIT_KEY]
        if not fruit_entries:
            continue
        fruit_lists += 1
        for entry in fruit_entries:
            assert entry["qty_min"] == 1, f"{boss_key}: fruit qty_min != 1"
            assert entry["qty_max"] == 1, f"{boss_key}: fruit qty_max != 1"
            fruit_weight = entry["weight"]
            largest_weight = max(e["weight"] for e in table)
            assert fruit_weight <= 0.05 * largest_weight, (
                f"{boss_key}: fruit weight {fruit_weight} is not <= 5% of "
                f"largest weight {largest_weight} — not a chase drop"
            )

    assert fruit_lists > 0, "fruit appears in no world-boss drop list"
