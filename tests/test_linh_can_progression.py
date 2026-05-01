"""Tests for the level-aware Linh Căn (Spiritual Root) progression system.

Covers:
  • storage round-trips between flat and level-aware formats
  • the qi_realm-derived level cap
  • per-element passive bonus scaling and threshold unlocks
  • per-level proc-chance scaling helper
  • registry integrity for the new linh_can_material catalogue
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.constants.linh_can import (
    ALL_LINH_CAN, LINH_CAN_DATA, LINH_CAN_MAX_LEVEL,
    compute_linh_can_bonuses, format_linh_can, format_linh_can_levels,
    get_threshold_unlocks, max_linh_can_level, parse_linh_can,
    parse_linh_can_levels, scaled_proc_chance,
)


# ── Storage format ───────────────────────────────────────────────────────────

def test_parse_legacy_format_defaults_to_level_one():
    assert parse_linh_can_levels("kim,hoa") == {"kim": 1, "hoa": 1}


def test_parse_new_format_reads_explicit_levels():
    assert parse_linh_can_levels("kim:5,hoa:9,loi:3") == {"kim": 5, "hoa": 9, "loi": 3}


def test_parse_drops_unknown_elements():
    assert parse_linh_can_levels("kim:3,bogus:7,hoa:2") == {"kim": 3, "hoa": 2}


def test_parse_clamps_levels_to_valid_range():
    assert parse_linh_can_levels("kim:0,hoa:99") == {"kim": 1, "hoa": LINH_CAN_MAX_LEVEL}


def test_parse_handles_empty_and_whitespace():
    assert parse_linh_can_levels("") == {}
    assert parse_linh_can_levels("  kim:2 ,  hoa:3  ") == {"kim": 2, "hoa": 3}


def test_parse_linh_can_legacy_returns_keys_only():
    assert parse_linh_can("kim:5,hoa:3") == ["kim", "hoa"]


def test_format_round_trip():
    payload = {"kim": 5, "hoa": 9, "loi": 3}
    assert parse_linh_can_levels(format_linh_can_levels(payload)) == payload


def test_format_orders_by_canonical_element_list():
    out = format_linh_can_levels({"am": 3, "kim": 1, "hoa": 5})
    # Canonical order is ALL_LINH_CAN: kim, moc, thuy, hoa, tho, phong, loi, quang, am
    assert out == "kim:1,hoa:5,am:3"


def test_legacy_format_helper_produces_level_one_strings():
    # Legacy callers (e.g. starter preset) should still produce parseable strings.
    raw = format_linh_can(["kim", "hoa"])
    assert parse_linh_can_levels(raw) == {"kim": 1, "hoa": 1}


# ── Cap rule ────────────────────────────────────────────────────────────────

def test_max_level_caps_at_qi_realm_plus_one():
    assert max_linh_can_level(0) == 1     # Luyện Khí
    assert max_linh_can_level(1) == 2     # Trúc Cơ
    assert max_linh_can_level(8) == 9     # Đăng Tiên


def test_max_level_never_exceeds_global_max():
    assert max_linh_can_level(50) == LINH_CAN_MAX_LEVEL


def test_max_level_never_drops_below_one():
    assert max_linh_can_level(-3) == 1


# ── Bonus scaling ───────────────────────────────────────────────────────────

def test_compute_bonuses_scales_passive_with_level():
    # Kim base passive: final_dmg_bonus = 0.05; multiplier 0.5/level
    base = compute_linh_can_bonuses({"kim": 1})
    high = compute_linh_can_bonuses({"kim": 5})
    assert base["final_dmg_bonus"] == pytest.approx(0.05)
    # Lv5 = 0.05 * (1 + 4 * 0.5) = 0.05 * 3 = 0.15 (plus thresholds at lv3 + lv5)
    assert high["final_dmg_bonus"] == pytest.approx(0.15)


def test_compute_bonuses_grants_threshold_effects():
    # Kim Lv3 unlocks +4% true_dmg_pct
    bonuses_lv1 = compute_linh_can_bonuses({"kim": 1})
    bonuses_lv3 = compute_linh_can_bonuses({"kim": 3})
    assert "true_dmg_pct" not in bonuses_lv1
    assert bonuses_lv3["true_dmg_pct"] == pytest.approx(0.04)


def test_compute_bonuses_accepts_legacy_list_at_level_one():
    legacy = compute_linh_can_bonuses(["hoa"])
    explicit = compute_linh_can_bonuses({"hoa": 1})
    assert legacy == explicit


def test_compute_bonuses_aggregates_across_elements():
    bonuses = compute_linh_can_bonuses({"kim": 1, "hoa": 1})
    # Both contribute +0.05 final_dmg_bonus
    assert bonuses["final_dmg_bonus"] == pytest.approx(0.10)


def test_compute_bonuses_handles_boolean_thresholds():
    # Hoa Lv9 grants dot_can_crit=True
    bonuses = compute_linh_can_bonuses({"hoa": 9})
    assert bonuses.get("dot_can_crit") is True


def test_threshold_unlocks_lists_label_at_each_milestone():
    # Lv5 should expose Lv3 + Lv5 entries, not Lv7 / Lv9
    labels = get_threshold_unlocks("kim", 5)
    assert any(l.startswith("Lv3") for l in labels)
    assert any(l.startswith("Lv5") for l in labels)
    assert not any(l.startswith("Lv7") for l in labels)


# ── Proc-chance helper ─────────────────────────────────────────────────────

def test_scaled_proc_chance_adds_per_level_above_one():
    # Lv1 = base; Lv5 = base + 4 * per_level
    base = scaled_proc_chance(1, 0.20, 0.015)
    higher = scaled_proc_chance(5, 0.20, 0.015)
    assert base == pytest.approx(0.20)
    assert higher == pytest.approx(0.20 + 4 * 0.015)


def test_scaled_proc_chance_clamps_to_unit_interval():
    assert scaled_proc_chance(50, 0.50, 0.10) == 1.0
    assert scaled_proc_chance(1, -0.5, 0.0) == 0.0


# ── Registry integrity ─────────────────────────────────────────────────────

def test_registry_has_unlock_material_for_every_element():
    for elem in ALL_LINH_CAN:
        mats = registry.linh_can_materials_for(elem, "unlock")
        assert mats, f"missing unlock material for {elem}"
        assert mats[0]["linh_can_role"] == "unlock"


def test_registry_has_upgrade_material_for_every_element_and_level():
    for elem in ALL_LINH_CAN:
        for level in range(1, LINH_CAN_MAX_LEVEL + 1):
            mats = registry.linh_can_materials_for(elem, "upgrade", level)
            assert mats, f"missing upgrade material for {elem} lv{level}"


def test_registry_material_total_is_at_least_one_hundred():
    # 9 unlock + 9 elements × 9 upgrade levels + catalysts ≥ 100
    total = len(registry.items_by_type("linh_can_material"))
    assert total >= 100


def test_registry_material_names_are_unique():
    names = [
        item["vi"] for item in registry.items_by_type("linh_can_material")
    ]
    assert len(names) == len(set(names)), "linh_can material names must be unique"


# ── linh_can system module ─────────────────────────────────────────────────

def test_unlock_cost_demands_extra_catalyst_for_fourth_root():
    from src.game.systems.linh_can import unlock_cost
    cheap = unlock_cost("kim", existing_count=2)
    pricey = unlock_cost("kim", existing_count=3)
    # 4th+ root pulls in the Hồng Mông Khải Linh Châu catalyst
    assert "LCCatHongMongKhaiLinh" not in cheap.materials
    assert "LCCatHongMongKhaiLinh" in pricey.materials
    assert pricey.merit > cheap.merit


def test_upgrade_cost_for_target_level_includes_dedicated_material():
    from src.game.systems.linh_can import upgrade_cost
    cost = upgrade_cost("kim", target_level=5)
    assert "LCKim5" in cost.materials
    assert cost.merit > 0


def test_upgrade_cost_rejects_level_below_two():
    from src.game.systems.linh_can import LinhCanError, upgrade_cost
    with pytest.raises(LinhCanError):
        upgrade_cost("kim", target_level=1)


def test_upgrade_cost_rejects_level_above_max():
    from src.game.systems.linh_can import LinhCanError, upgrade_cost
    with pytest.raises(LinhCanError):
        upgrade_cost("kim", target_level=LINH_CAN_MAX_LEVEL + 1)


# ── LINH_CAN_DATA shape sanity ─────────────────────────────────────────────

def test_every_element_defines_scaling_block():
    for elem in ALL_LINH_CAN:
        scaling = LINH_CAN_DATA[elem].get("scaling", {})
        assert "passive_bonus_per_level" in scaling
        assert "thresholds" in scaling
        assert set(scaling["thresholds"]).issubset({3, 5, 7, 9})


# ── Khí Tu breadth multiplier ──────────────────────────────────────────────

def test_breadth_multiplier_returns_one_when_no_qualifying_elements():
    from src.game.constants.linh_can import linh_can_breadth_multiplier
    # All elements below the Lv7 threshold → no synergy.
    assert linh_can_breadth_multiplier({"kim": 3, "hoa": 6}) == 1.0


def test_breadth_multiplier_scales_per_qualifying_element():
    from src.game.constants.linh_can import (
        LINH_CAN_BREADTH_PER_ELEMENT, linh_can_breadth_multiplier,
    )
    # 3 elements at Lv7+ → 1.0 + 3 × per_element
    levels = {"kim": 7, "hoa": 8, "loi": 9, "moc": 4}
    expected = 1.0 + 3 * LINH_CAN_BREADTH_PER_ELEMENT
    assert linh_can_breadth_multiplier(levels) == pytest.approx(expected)


def test_breadth_multiplier_caps_at_max():
    from src.game.constants.linh_can import (
        LINH_CAN_BREADTH_MAX_MULT, linh_can_breadth_multiplier,
    )
    # All 9 at Lv9 → would exceed cap; clamped.
    levels = {elem: 9 for elem in ALL_LINH_CAN}
    assert linh_can_breadth_multiplier(levels) == LINH_CAN_BREADTH_MAX_MULT


def test_breadth_multiplier_only_counts_threshold_or_above():
    from src.game.constants.linh_can import (
        LINH_CAN_BREADTH_MIN_LEVEL, linh_can_breadth_multiplier,
    )
    # Exactly at the threshold counts, one below does not.
    above = {elem: LINH_CAN_BREADTH_MIN_LEVEL for elem in ALL_LINH_CAN}
    below = {elem: LINH_CAN_BREADTH_MIN_LEVEL - 1 for elem in ALL_LINH_CAN}
    assert linh_can_breadth_multiplier(above) > 1.0
    assert linh_can_breadth_multiplier(below) == 1.0


def test_is_khi_tu_requires_strict_qi_dominance():
    from src.game.systems.cultivation import is_khi_tu
    # Pure qi-only build — gate fires.
    assert is_khi_tu(body_realm=1, qi_realm=8, formation_realm=4)
    # Tied with formation — strict inequality means the gate doesn't fire.
    assert not is_khi_tu(body_realm=0, qi_realm=4, formation_realm=4)
    # Body cultivator dabbling in qi — gate stays closed.
    assert not is_khi_tu(body_realm=8, qi_realm=8, formation_realm=0)


def test_compute_combat_stats_applies_breadth_multiplier_when_khi_tu():
    """Direct integration test — same Character with 9× Lv9 Linh Căn,
    once as Khí Tu (gate fires) and once as The Tu (gate doesn't fire);
    the Khí Tu version must end up with strictly higher passive numbers
    on stats that aren't in the breadth-excluded list.
    """
    from src.game.models.character import Character, CharacterStats
    from src.game.systems.character_stats import compute_combat_stats

    levels = {elem: 9 for elem in ALL_LINH_CAN}

    khi_char = Character(
        player_id=1, discord_id=1, name="khi",
        body_realm=1, body_level=1, qi_realm=8, qi_level=9,
        formation_realm=2, formation_level=1,
        linh_can=list(ALL_LINH_CAN), linh_can_levels=dict(levels),
        constitution_type="ConstitutionVanTuong", stats=CharacterStats(),
    )
    the_char = Character(
        player_id=2, discord_id=2, name="the",
        body_realm=8, body_level=9, qi_realm=1, qi_level=1,
        formation_realm=1, formation_level=1,
        linh_can=list(ALL_LINH_CAN), linh_can_levels=dict(levels),
        constitution_type="ConstitutionVanTuong", stats=CharacterStats(),
    )
    khi_cs = compute_combat_stats(khi_char)
    the_cs = compute_combat_stats(the_char)
    # crit_rating accumulates Lôi + Lôi Lv5 threshold + Lôi Lv9 threshold +
    # Phong + Phong Lv9 + Kim Lv5; none of those are breadth-excluded so
    # the Khí Tu version should be strictly higher.
    assert khi_cs.crit_rating > the_cs.crit_rating
