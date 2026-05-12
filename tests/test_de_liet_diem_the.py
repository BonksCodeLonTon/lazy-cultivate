"""Tests for the buffed ConstitutionHoaDiem_Leg (Đế Liệt Diệm Thể).

Mechanic: when the holder takes damage, ``damage_to_fire_convert_pct`` of
the incoming amount is reclassified as fire damage to the holder, then
mitigated by the holder's own hoa resistance. The unconverted remainder is
taken as raw HP loss. Net effect: paired with high ``res_hoa``, the
constitution turns a fraction of every hit into a soft tickle.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.constants.balance import MAX_ELEMENTAL_RES
from src.game.systems.combat import CombatSession
from src.game.systems.combatant import Combatant


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def make_combatant(key: str = "p", **overrides) -> Combatant:
    defaults = dict(
        name=key,
        hp=10_000, hp_max=10_000,
        mp=500, mp_max=500,
        spd=10, element=None,
        atk=100, matk=100, def_stat=20,
    )
    defaults.update(overrides)
    return Combatant(key=key, **defaults)


def make_session(player: Combatant, enemy: Combatant, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed),
        max_turns=5,
    )


# ── Self fire conversion math ───────────────────────────────────────────────


def test_no_conversion_takes_full_damage():
    c = make_combatant(hp=10_000, hp_max=10_000)
    applied = c.take_damage(1000)
    assert applied == 1000
    assert c.hp == 9_000


def test_conversion_with_zero_res_is_no_op():
    """Without resistance to the conversion element, the converted portion
    takes full damage, so the holder loses the same amount."""
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.40},
        resistances={"hoa": 0.0},
    )
    applied = c.take_damage(1000)
    assert applied == 1000


def test_conversion_with_hoa_res_reduces_taken_damage():
    """1000 dmg with 40% conversion + 75% hoa res → 600 + 400×0.25 = 700."""
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.40},
        resistances={"hoa": 0.75},
    )
    applied = c.take_damage(1000)
    assert applied == 700
    assert c.hp == 9_300


def test_conversion_with_partial_res():
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.40},
        resistances={"hoa": 0.30},
    )
    applied = c.take_damage(1000)
    # 600 + 400×0.70 = 880
    assert applied == 880


def test_conversion_res_clamped_at_max():
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.40},
        resistances={"hoa": 0.99},
    )
    applied = c.take_damage(1000)
    expected = 600 + max(1, int(400 * (1.0 - MAX_ELEMENTAL_RES)))
    assert applied == expected


# ── Generic multi-element conversion ────────────────────────────────────────


def test_multiple_element_conversions_route_through_each_res():
    """A holder with conversions on two elements should route each portion
    through that element's own resistance."""
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.30, "thuy": 0.20},
        resistances={"hoa": 0.50, "thuy": 0.50},
    )
    applied = c.take_damage(1000)
    # total_pct = 0.50, unconverted = 500
    # hoa share = 0.30/0.50 = 0.60 → 1000×0.50×0.60 = 300 → ×(1-0.50) = 150
    # thuy share = 0.20/0.50 = 0.40 → 1000×0.50×0.40 = 200 → ×(1-0.50) = 100
    # total = 500 + 150 + 100 = 750
    assert applied == 750


def test_aggregate_conversion_capped_at_one():
    """If sum of pcts > 1.0, the aggregate is clamped to 1.0 (so the holder
    can't end up with negative damage from over-investing)."""
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.70, "thuy": 0.50},
        resistances={"hoa": 0.0, "thuy": 0.0},
    )
    applied = c.take_damage(1000)
    # total_pct clamped to 1.0 → unconverted = 0; each share takes full
    # damage with no res so the total stays close to 1000 (modulo small
    # int-truncation across the per-element split).
    assert 998 <= applied <= 1000


# ── Conversion stacks with deferral (order: convert → defer) ────────────────


def test_conversion_then_defer_combine():
    c = make_combatant(
        hp=10_000, hp_max=10_000,
        damage_taken_convert_pct={"hoa": 0.40},
        resistances={"hoa": 0.75},
        damage_defer_turns=3,
        damage_defer_pct=0.30,
    )
    # Convert: 1000 → 700. Defer 30% of 700 = 210 (queue), 490 immediate.
    applied = c.take_damage(1000)
    assert applied == 700 - 210
    assert sum(c.deferred_damage_queue) == 210


# ── element_dmg_bonus integration ───────────────────────────────────────────


def test_element_dmg_bonus_amplifies_matching_skill():
    """``element_dmg_bonus`` adds to ``final_dmg_bonus`` only when the skill
    element matches."""
    from src.game.engine.damage.combat_hit import build_attack_stats

    actor = make_combatant("a", element_dmg_bonus={"hoa": 0.20, "thuy": 0.10})
    target = make_combatant("t")

    hoa_stats = build_attack_stats(actor, target, actor_mods={}, skill_element="hoa")
    thuy_stats = build_attack_stats(actor, target, actor_mods={}, skill_element="thuy")
    kim_stats = build_attack_stats(actor, target, actor_mods={}, skill_element="kim")

    assert hoa_stats.final_dmg_bonus == pytest.approx(0.20)
    assert thuy_stats.final_dmg_bonus == pytest.approx(0.10)
    assert kim_stats.final_dmg_bonus == pytest.approx(0.0)


# ── Registry sanity ─────────────────────────────────────────────────────────


def test_de_liet_diem_constitution_uses_generic_dicts():
    c = registry.get_constitution("ConstitutionHoaDiem_Leg")
    assert c is not None
    bonuses = c["stat_bonuses"]
    assert bonuses["damage_taken_convert_pct"]["hoa"] == pytest.approx(0.40)
    assert bonuses["element_dmg_bonus"]["hoa"] > 0
    assert "damage_to_fire_convert_pct" not in bonuses
    assert "damage_to_fire_reflect_pct" not in bonuses


def test_dict_bonuses_merge_per_element_additively():
    """Two constitutions with overlapping element dicts should merge per-key,
    not clobber. Validates the ``_merge_bonus_dict`` extension."""
    from src.game.systems.cultivation import _merge_bonus_dict

    target: dict = {}
    _merge_bonus_dict(target, {"element_dmg_bonus": {"hoa": 0.20}})
    _merge_bonus_dict(target, {"element_dmg_bonus": {"hoa": 0.10, "kim": 0.15}})
    assert target["element_dmg_bonus"] == {"hoa": pytest.approx(0.30), "kim": pytest.approx(0.15)}


# ── Per-element resistance pickup (still in place) ──────────────────────────


def test_res_hoa_bonus_pickup_through_compute_combat_stats():
    from src.game.models.character import Character
    from src.game.systems.character_stats import compute_combat_stats

    char = Character(
        player_id=1, discord_id=1, name="t",
        body_realm=5, qi_realm=5, formation_realm=5,
        constitution_type="",
    )
    char.stats.res_hoa = 0.10
    cs = compute_combat_stats(char, equip_stats={"res_hoa": 0.20})
    assert cs.resistances["hoa"] == pytest.approx(0.30)


def test_res_hoa_pickup_clamped_at_max_elemental_res():
    """Without ``hoa_max_resist_bonus``, the player's hoa res caps at
    ``RES_SOFT_CAP`` (0.75). The hard cap ``MAX_ELEMENTAL_RES`` (0.90)
    can only be reached by stacking ``<elem>_max_resist_bonus``."""
    from src.game.constants.balance import RES_SOFT_CAP
    from src.game.models.character import Character
    from src.game.systems.character_stats import compute_combat_stats

    char = Character(
        player_id=1, discord_id=1, name="t",
        body_realm=5, qi_realm=5, formation_realm=5,
        constitution_type="",
    )
    char.stats.res_hoa = 0.50
    # No cap-lifter — clamps at the soft cap (0.75).
    cs = compute_combat_stats(char, equip_stats={"res_hoa": 0.50})
    assert cs.resistances["hoa"] == pytest.approx(RES_SOFT_CAP)

    # With cap-lifter sufficient to reach hard cap.
    cs2 = compute_combat_stats(
        char, equip_stats={"res_hoa": 0.50, "hoa_max_resist_bonus": 0.20}
    )
    assert cs2.resistances["hoa"] == pytest.approx(MAX_ELEMENTAL_RES)
