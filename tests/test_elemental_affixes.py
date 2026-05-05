"""Tests for elemental damage prefixes + elemental resistance suffixes.

Covers:
- Registry contains the 19 new affix entries.
- Specific-element affixes roll values 2× the matching general affix.
- ``element_dmg_all`` from equip_stats fans out to every element in
  ``element_dmg_bonus``.
- Per-element ``element_dmg_<elem>`` adds only to that element.
- General + specific stack additively.
- Per-element ``res_<elem>`` from equip_stats lifts only that element's
  resistance line.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.constants.elements import ALL_ELEMENTS
from src.game.models.character import Character, CharacterStats
from src.game.systems.character_stats import compute_combat_stats


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Registry coverage ──────────────────────────────────────────────────────

def test_general_damage_prefix_loaded():
    assert "pfx_dmg_elem_all" in registry.affixes
    a = registry.affixes["pfx_dmg_elem_all"]
    assert a["type"] == "prefix"
    assert a["stat"] == "element_dmg_all"
    assert a["is_pct"] is True


def test_specific_damage_prefixes_loaded_for_every_element():
    for e in ALL_ELEMENTS:
        key = f"pfx_dmg_{e.value}"
        assert key in registry.affixes, f"missing affix {key}"
        a = registry.affixes[key]
        assert a["type"] == "prefix"
        assert a["stat"] == f"element_dmg_{e.value}"
        assert a["is_pct"] is True


def test_specific_resistance_suffixes_loaded_for_every_element():
    for e in ALL_ELEMENTS:
        key = f"sfx_res_{e.value}"
        assert key in registry.affixes, f"missing affix {key}"
        a = registry.affixes[key]
        assert a["type"] == "suffix"
        assert a["stat"] == f"res_{e.value}"
        assert a["is_pct"] is True


# ── Specific = 2× general ─────────────────────────────────────────────────

def test_specific_damage_is_double_general_at_every_grade():
    general = registry.affixes["pfx_dmg_elem_all"]["by_grade"]
    for e in ALL_ELEMENTS:
        spec = registry.affixes[f"pfx_dmg_{e.value}"]["by_grade"]
        for i, ((g_lo, g_hi), (s_lo, s_hi)) in enumerate(zip(general, spec)):
            assert s_lo == pytest.approx(g_lo * 2.0, rel=1e-3), (
                f"{e.value} grade {i+1} lo drift"
            )
            assert s_hi == pytest.approx(g_hi * 2.0, rel=1e-3), (
                f"{e.value} grade {i+1} hi drift"
            )


def test_specific_resistance_is_double_general_at_every_grade():
    general = registry.affixes["sfx_res_all"]["by_grade"]
    for e in ALL_ELEMENTS:
        spec = registry.affixes[f"sfx_res_{e.value}"]["by_grade"]
        for i, ((g_lo, g_hi), (s_lo, s_hi)) in enumerate(zip(general, spec)):
            assert s_lo == pytest.approx(g_lo * 2.0, rel=1e-3), (
                f"{e.value} grade {i+1} lo drift"
            )
            assert s_hi == pytest.approx(g_hi * 2.0, rel=1e-3), (
                f"{e.value} grade {i+1} hi drift"
            )


# ── Runtime: equip_stats merge ────────────────────────────────────────────

def _stub_char() -> Character:
    return Character(
        player_id=1, discord_id=1, name="Test",
        body_realm=1, body_level=1,
        qi_realm=1, qi_level=1,
        formation_realm=1, formation_level=1,
        linh_can=[], stats=CharacterStats(),
    )


def test_element_dmg_all_fans_out_to_every_element():
    cs = compute_combat_stats(_stub_char(), equip_stats={"element_dmg_all": 0.15})
    for e in ALL_ELEMENTS:
        assert cs.element_dmg_bonus.get(e.value, 0.0) == pytest.approx(0.15)


def test_specific_element_dmg_only_affects_that_element():
    cs = compute_combat_stats(_stub_char(), equip_stats={"element_dmg_kim": 0.30})
    assert cs.element_dmg_bonus.get("kim", 0.0) == pytest.approx(0.30)
    for e in ALL_ELEMENTS:
        if e.value == "kim":
            continue
        assert cs.element_dmg_bonus.get(e.value, 0.0) == pytest.approx(0.0)


def test_general_and_specific_dmg_stack_additively():
    cs = compute_combat_stats(_stub_char(), equip_stats={
        "element_dmg_all": 0.15,
        "element_dmg_kim": 0.30,
    })
    assert cs.element_dmg_bonus.get("kim", 0.0) == pytest.approx(0.45)
    # other elements only get the general portion
    assert cs.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(0.15)


def test_specific_res_lifts_only_that_element():
    cs = compute_combat_stats(_stub_char(), equip_stats={"res_hoa": 0.20})
    res = cs.resistances
    assert res["hoa"] == pytest.approx(0.20)
    for e in ALL_ELEMENTS:
        if e.value == "hoa":
            continue
        # baseline 0 for a stub character
        assert res[e.value] == pytest.approx(0.0)


def test_res_all_and_specific_stack():
    cs = compute_combat_stats(_stub_char(), equip_stats={
        "res_all": 0.10,
        "res_kim": 0.20,
    })
    res = cs.resistances
    assert res["kim"] == pytest.approx(0.30)
    assert res["hoa"] == pytest.approx(0.10)
