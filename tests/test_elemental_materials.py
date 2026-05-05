"""Tests for the per-element forge materials.

Covers:
- Registry loads all 18 elemental materials (9 dmg + 9 res, G2).
- Each material's ``affix_bias`` points at the correct affix key.
- ``_get_affix_bias`` honors a damage material → biases the dmg prefix.
- ``_get_affix_bias`` honors a resistance material → biases the res suffix.
- Mid-tier zone loot tables (4–6) include every elemental material.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.registry import registry
from src.game.constants.elements import ALL_ELEMENTS
from src.game.systems.forge import _get_affix_bias

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Registry coverage ──────────────────────────────────────────────────────

def test_dmg_materials_loaded_for_every_element():
    for e in ALL_ELEMENTS:
        key = f"MatElemDmg{e.value.capitalize()}"
        assert key in registry.items, f"missing {key}"
        item = registry.items[key]
        assert item["type"] == "forge_material"
        assert item["grade"] == 2
        assert item["affix_bias"] == [f"pfx_dmg_{e.value}"]
        assert item.get("shop_price_merit") == 2000


def test_res_materials_loaded_for_every_element():
    for e in ALL_ELEMENTS:
        key = f"MatElemRes{e.value.capitalize()}"
        assert key in registry.items, f"missing {key}"
        item = registry.items[key]
        assert item["type"] == "forge_material"
        assert item["grade"] == 2
        assert item["affix_bias"] == [f"sfx_res_{e.value}"]
        assert item.get("shop_price_merit") == 2000


# ── Bias lookup ────────────────────────────────────────────────────────────

def test_get_affix_bias_dmg_material_for_each_element():
    for e in ALL_ELEMENTS:
        bias = _get_affix_bias([f"MatElemDmg{e.value.capitalize()}"])
        assert bias == {f"pfx_dmg_{e.value}"}


def test_get_affix_bias_res_material_for_each_element():
    for e in ALL_ELEMENTS:
        bias = _get_affix_bias([f"MatElemRes{e.value.capitalize()}"])
        assert bias == {f"sfx_res_{e.value}"}


def test_mixed_dmg_and_res_materials_union_correctly():
    bias = _get_affix_bias(["MatElemDmgKim", "MatElemResHoa"])
    assert bias == {"pfx_dmg_kim", "sfx_res_hoa"}


# ── Zone loot tables ───────────────────────────────────────────────────────

def _zone_drops(zone_idx: int) -> set[str]:
    path = ROOT / "src" / "data" / "loot_tables" / f"zone_{zone_idx:02d}.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    table = data[f"LootZone_{zone_idx}"]
    return {entry["item_key"] for entry in table}


@pytest.mark.parametrize("zone_idx", [4, 5, 6])
def test_each_mid_zone_drops_every_elemental_material(zone_idx):
    drops = _zone_drops(zone_idx)
    for e in ALL_ELEMENTS:
        for prefix in ("MatElemDmg", "MatElemRes"):
            key = f"{prefix}{e.value.capitalize()}"
            assert key in drops, f"zone_{zone_idx:02d} missing {key}"
