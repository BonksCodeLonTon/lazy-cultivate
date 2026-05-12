"""Tests for Thái Dương Thần Thể — per-turn fire aura damage.

The aura is a passive periodic effect: every turn end, the holder deals
``hp_max × solar_aura_pct`` fire damage to the opponent. Damage is amplified
by ``final_dmg_bonus``, ``burn_dmg_bonus``, and ``bonus_dmg_vs_burn`` (when
the target has burn stacks); reduced by the target's hoa resistance after
``element_pen["hoa"]``.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
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


# ── Base aura ────────────────────────────────────────────────────────────────


def test_aura_deals_pct_of_hp_max_per_turn():
    player = make_combatant("p", hp_max=10_000, hp=10_000, solar_aura_pct=0.04)
    enemy = make_combatant("e", hp_max=20_000, hp=20_000)
    session = make_session(player, enemy)

    session._process_periodic(player)

    # 10_000 × 0.04 = 400 base, no amplifiers, no resist
    assert enemy.hp == 20_000 - 400


def test_aura_scales_with_owner_hp_max_not_target():
    """Regression guard: damage must scale with the aura HOLDER's hp_max, not
    the target's. A 5k-HP holder vs a 100k-HP target should deal 200 (5_000 ×
    0.04), not 4_000."""
    holder = make_combatant("p", hp_max=5_000, hp=5_000, solar_aura_pct=0.04)
    fat_target = make_combatant("e", hp_max=100_000, hp=100_000)
    make_session(holder, fat_target)._process_periodic(holder)
    assert 100_000 - fat_target.hp == 200

    # Inverse: 100k-HP holder deals 4_000 even if target only has 5k HP.
    big_holder = make_combatant("p", hp_max=100_000, hp=100_000, solar_aura_pct=0.04)
    small_target = make_combatant("e", hp_max=5_000, hp=5_000)
    make_session(big_holder, small_target)._process_periodic(big_holder)
    assert 5_000 - small_target.hp == 4_000


def test_aura_uses_owner_current_hp_max_after_pct_buffs():
    """If holder's hp_max grows (e.g., from hp_pct items, realm-up), aura
    damage grows with it. The aura reads ``combatant.hp_max`` live, so any
    upstream change to that field is reflected immediately."""
    a = make_combatant("p", hp_max=10_000, hp=10_000, solar_aura_pct=0.05)
    b = make_combatant("p", hp_max=20_000, hp=20_000, solar_aura_pct=0.05)
    e1 = make_combatant("e", hp=999_999, hp_max=999_999)
    e2 = make_combatant("e", hp=999_999, hp_max=999_999)
    make_session(a, e1)._process_periodic(a)
    make_session(b, e2)._process_periodic(b)
    assert (999_999 - e1.hp) * 2 == (999_999 - e2.hp)


def test_no_aura_when_pct_is_zero():
    player = make_combatant("p", solar_aura_pct=0.0)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    session = make_session(player, enemy)

    session._process_periodic(player)

    assert enemy.hp == 20_000


def test_dead_actor_does_not_apply_aura():
    player = make_combatant("p", hp=0, solar_aura_pct=0.04)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    session = make_session(player, enemy)

    session._process_periodic(player)

    assert enemy.hp == 20_000


# ── Amplifiers ───────────────────────────────────────────────────────────────


def test_aura_scales_with_final_dmg_bonus():
    player = make_combatant("p", solar_aura_pct=0.04, final_dmg_bonus=0.50)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    session = make_session(player, enemy)

    session._process_periodic(player)

    # 400 × (1 + 0.50) = 600
    assert 20_000 - enemy.hp == 600


def test_aura_scales_with_burn_dmg_bonus():
    player = make_combatant("p", solar_aura_pct=0.04, dot_dmg_bonus_by_kind={"burn": 0.30})
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    session = make_session(player, enemy)

    session._process_periodic(player)

    # 400 × (1 + 0.30) = 520
    assert 20_000 - enemy.hp == 520


def test_aura_bonus_vs_burning_target_only_applies_with_stacks():
    # Without stacks
    p1 = make_combatant("p1", solar_aura_pct=0.04, bonus_dmg_vs_burn=0.50)
    e1 = make_combatant("e1", hp=20_000, hp_max=20_000, burn_stacks=0)
    make_session(p1, e1)._process_periodic(p1)
    no_burn_dmg = 20_000 - e1.hp

    # With stacks
    p2 = make_combatant("p2", solar_aura_pct=0.04, bonus_dmg_vs_burn=0.50)
    e2 = make_combatant("e2", hp=20_000, hp_max=20_000, burn_stacks=3)
    make_session(p2, e2)._process_periodic(p2)
    burn_dmg = 20_000 - e2.hp

    assert no_burn_dmg == 400
    assert burn_dmg == 600  # 400 × (1 + 0.50)


# ── Resistance / shred ───────────────────────────────────────────────────────


def test_aura_reduced_by_hoa_resistance():
    player = make_combatant("p", solar_aura_pct=0.04)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, resistances={"hoa": 0.50})
    make_session(player, enemy)._process_periodic(player)

    # 400 × (1 - 0.50) = 200
    assert 20_000 - enemy.hp == 200


def test_aura_fire_res_shred_recovers_damage():
    player = make_combatant("p", solar_aura_pct=0.04, element_pen={"hoa": 0.30})
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, resistances={"hoa": 0.50})
    make_session(player, enemy)._process_periodic(player)

    # Effective res = 0.50 - 0.30 = 0.20 → 400 × 0.80 = 320
    assert 20_000 - enemy.hp == 320


def test_aura_resistance_capped_at_max_elemental_res():
    player = make_combatant("p", solar_aura_pct=0.04)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, resistances={"hoa": 0.99})
    make_session(player, enemy)._process_periodic(player)

    # Cap at 0.90 → 400 × (1 - 0.90) ≈ 39 (float truncation)
    assert 20_000 - enemy.hp == 39


# ── Combined: full Thái Dương kit ───────────────────────────────────────────


def test_thai_duong_kit_all_stack():
    player = make_combatant(
        "p",
        solar_aura_pct=0.04,
        final_dmg_bonus=0.15,
        dot_dmg_bonus_by_kind={"burn": 0.30},
        bonus_dmg_vs_burn=0.20,
        element_pen={"hoa": 0.15},
    )
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, burn_stacks=2, resistances={"hoa": 0.10})
    make_session(player, enemy)._process_periodic(player)

    # base = 10_000 × 0.04 = 400
    # mult = 1 + 0.15 + 0.30 + 0.20 = 1.65
    # res  = max(0, 0.10 - 0.15) = 0.0  →  no reduction
    # dmg  = int(400 × 1.65) = 660
    assert 20_000 - enemy.hp == 660


# ── Registry: the new constitution exists and is wired ──────────────────────


def test_thai_duong_constitution_is_registered():
    c = registry.get_constitution("ConstitutionThaiDuongThanThe")
    assert c is not None
    assert c["rarity"] == "legendary"
    assert c["element"] == "hoa"
    assert c["stat_bonuses"]["solar_aura_pct"] > 0
