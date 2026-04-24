"""Tests for super-rare forge materials — granted_passive grafting + constraints.

Covers:
- forge_equipment accepts at most one super_material_key (singular arg).
- Unknown super_material_key fails with a helpful message.
- Below-grade super material is rejected.
- Valid super material stamps super_material_key onto item_data.
- compute_equipment_stats merges the super material's granted_passive onto totals.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.data.registry import registry
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems.forge import (
    check_forge_requirements,
    forge_equipment,
    get_affix_count,
    max_affix_total,
    roll_affixes,
)


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


def _test_char():
    """Minimal Character stub satisfying forge_equipment's attribute reads."""
    return SimpleNamespace(
        merit=1_000_000,
        qi_realm=9,
        stats=SimpleNamespace(comprehension=0),
    )


def _first_forgable_base(min_grade: int) -> str:
    """Pick a base that supports implicit_by_grade for the requested item grade."""
    for base in registry.bases.values():
        if "implicit_by_grade" in base:
            return base["key"]
    pytest.skip("no forgable base in registry")
    return ""  # unreachable


# ── Smoke — registry picks up super_materials.json ─────────────────────────
def test_registry_loads_super_materials():
    mat = registry.get_super_material("SuperThienThuKiep")
    assert mat is not None
    assert mat["type"] == "super_material"
    assert "heal_can_crit" in mat["granted_passive"]


def test_get_super_material_returns_none_for_non_super():
    # A regular forge_material must not be treated as super
    mat = registry.get_super_material("KiemTamThach")
    assert mat is None


# ── forge_equipment — validation paths ─────────────────────────────────────
def test_forge_rejects_unknown_super_material():
    char = _test_char()
    base_key = _first_forgable_base(min_grade=5)
    res = forge_equipment(
        char, base_key, grade=5, consumed_materials=[],
        super_material_key="NotARealMaterial",
    )
    assert res.success is False
    assert "không tồn tại" in res.message.lower()


def test_forge_rejects_below_grade_super_material():
    """R8 super material can't be used on R5 forge."""
    char = _test_char()
    base_key = _first_forgable_base(min_grade=5)
    res = forge_equipment(
        char, base_key, grade=5, consumed_materials=[],
        super_material_key="SuperThanhQuangXaLoi",  # min_item_grade=8
    )
    assert res.success is False
    assert "cấp" in res.message.lower()


def test_forge_stamps_super_material_key_on_success():
    """Valid super material flows through to item_data."""
    char = _test_char()
    base_key = _first_forgable_base(min_grade=5)
    res = forge_equipment(
        char, base_key, grade=5, consumed_materials=[],
        super_material_key="SuperCuuChuanLinhTinh",  # min_item_grade=5
    )
    assert res.success is True
    assert res.item_data is not None
    assert res.item_data["super_material_key"] == "SuperCuuChuanLinhTinh"


# ── compute_equipment_stats — passive merging ──────────────────────────────
def test_compute_equipment_merges_super_material_passive():
    """granted_passive values are summed onto totals when the item is equipped."""
    inst = SimpleNamespace(
        location="equipped",
        computed_stats={"atk": 50},
        unique_key=None,
        super_material_key="SuperCuuChuanLinhTinh",  # +80 crit, +100 crit_dmg
    )
    totals = compute_equipment_stats([inst])
    assert totals["atk"] == 50.0
    assert totals["crit_rating"] == 80.0
    assert totals["crit_dmg_rating"] == 100.0


def test_compute_equipment_merges_bool_passive_bool_safe():
    """Bool passives (heal_can_crit, barrier_on_cleanse) merge via OR — not sum."""
    inst = SimpleNamespace(
        location="equipped",
        computed_stats={},
        unique_key=None,
        super_material_key="SuperThienThuKiep",  # heal_can_crit: true
    )
    totals = compute_equipment_stats([inst])
    assert totals["heal_can_crit"] is True
    # Bool passives stay bool — not coerced to float 1.0
    assert totals["heal_pct"] == pytest.approx(0.12)


# ── Max affix total — Thiên gets +1 across all grades; G9 bumps to 6 ──────
@pytest.mark.parametrize("grade,expected", [
    (1, 5), (2, 5), (3, 5), (4, 5), (5, 5), (6, 5), (7, 5), (8, 5),
    (9, 6),
])
def test_max_affix_total_per_grade(grade, expected):
    assert max_affix_total(grade) == expected


def test_get_affix_count_base_thien_is_3p2s():
    """Thiên base = (3,2) at every grade except G9."""
    for g in (1, 2, 3, 4, 5, 6, 7, 8):
        assert get_affix_count(g, "thien") == (3, 2), f"grade {g} drifted"


def test_get_affix_count_grade9_thien_is_3p3s():
    assert get_affix_count(9, "thien") == (3, 3)


def test_get_affix_count_lower_qualities():
    """Hoàng/Huyền/Địa use the bumped base table at every grade."""
    assert get_affix_count(1, "hoan") == (1, 1)
    assert get_affix_count(5, "huyen") == (2, 1)
    assert get_affix_count(9, "dia") == (2, 2)


def test_roll_affixes_grade1_thien_rolls_5_total():
    """New: even grade 1 Thiên rolls 5 affixes."""
    affixes = roll_affixes(slot="armor", grade=1, quality="thien")
    assert len(affixes) == 5


def test_roll_affixes_grade9_thien_rolls_6_total():
    """Grade 9 Thiên still gets the extra suffix → 6 affixes total."""
    affixes = roll_affixes(slot="armor", grade=9, quality="thien")
    assert len(affixes) == 6


def test_roll_affixes_grade7_dia_rolls_4_total():
    """Địa now rolls 4 affixes (2,2) across all grades under the bumped base table."""
    affixes = roll_affixes(slot="armor", grade=7, quality="dia")
    assert len(affixes) == 4


# ── Required material qty = max_affix_total(grade) ────────────────────────
def test_check_forge_requirements_uses_max_affix_qty_grade1():
    """Grade 1 now needs 5 materials (max_affix_total=5), not the stale JSON qty."""
    char = _test_char()
    bag = {"MatHuyetTinh": 5}  # grade 1 material, 5 copies
    ok, msg, option = check_forge_requirements(char, grade=1, materials_in_bag=bag)
    assert ok, msg
    assert option is not None
    assert option["materials"][0]["qty"] == 5


def test_check_forge_requirements_fails_when_below_max_affix_qty():
    char = _test_char()
    bag = {"MatHuyetTinh": 4}  # 1 short of max_affix_total(1)=5
    ok, _, _ = check_forge_requirements(char, grade=1, materials_in_bag=bag)
    assert ok is False


def test_check_forge_requirements_grade9_needs_6_materials():
    """Grade 9 needs 6 materials (max_affix_total=6)."""
    char = _test_char()
    # Need some grade 6 material — find one from materials.json
    grade6_mat = next(
        (k for k, item in registry.items.items()
         if item.get("type") == "material" and item.get("grade") == 6),
        None,
    )
    if grade6_mat is None:
        pytest.skip("no grade 6 material in registry")
    bag = {grade6_mat: 6}
    ok, msg, option = check_forge_requirements(char, grade=9, materials_in_bag=bag)
    assert ok, msg
    assert option["materials"][0]["qty"] == 6


def test_bagged_item_passive_not_applied():
    """Items in the bag (location != 'equipped') must not contribute passives."""
    inst = SimpleNamespace(
        location="bag",
        computed_stats={"atk": 50},
        unique_key=None,
        super_material_key="SuperCuuChuanLinhTinh",
    )
    totals = compute_equipment_stats([inst])
    assert totals == {}
