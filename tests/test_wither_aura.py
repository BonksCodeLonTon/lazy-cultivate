"""Tests for Khô Mộc Thần Thể — per-turn moc drain aura with self-leech.

The aura is a passive periodic effect: every turn end, the holder deals
``hp_max × wither_aura_pct`` moc damage to the opponent, then heals itself by
the dealt amount (routed through the standard ``_apply_heal`` so heal-crit
and bleed-heal-reduction stay consistent). Damage is amplified by
``final_dmg_bonus`` and ``dot_dmg_bonus``; reduced by the target's moc
resistance after ``element_pen["moc"]``.
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


def test_aura_deals_pct_of_owner_hp_max():
    player = make_combatant("p", hp_max=10_000, hp=5_000, wither_aura_pct=0.04)
    enemy = make_combatant("e", hp_max=20_000, hp=20_000)
    make_session(player, enemy)._process_periodic(player)

    # 10_000 × 0.04 = 400 base, no amplifiers, no resist
    assert 20_000 - enemy.hp == 400


def test_aura_heals_owner_by_damage_dealt():
    player = make_combatant("p", hp_max=10_000, hp=5_000, wither_aura_pct=0.04)
    enemy = make_combatant("e", hp_max=20_000, hp=20_000)
    make_session(player, enemy)._process_periodic(player)

    # Drain = 400 → holder heals by 400 (5000 → 5400)
    assert player.hp == 5_400


def test_aura_heal_clamped_at_hp_max():
    player = make_combatant("p", hp_max=10_000, hp=9_900, wither_aura_pct=0.04)
    enemy = make_combatant("e", hp_max=20_000, hp=20_000)
    make_session(player, enemy)._process_periodic(player)

    # Drain dealt = 400; only 100 heal "fits" before hp_max cap.
    assert player.hp == 10_000
    assert 20_000 - enemy.hp == 400


def test_no_aura_when_pct_is_zero():
    player = make_combatant("p", wither_aura_pct=0.0, hp=5_000, hp_max=10_000)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    make_session(player, enemy)._process_periodic(player)

    assert enemy.hp == 20_000
    assert player.hp == 5_000


def test_dead_actor_does_not_apply_aura():
    player = make_combatant("p", hp=0, hp_max=10_000, wither_aura_pct=0.04)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    make_session(player, enemy)._process_periodic(player)

    assert enemy.hp == 20_000
    assert player.hp == 0


# ── Owner-HP scaling regression ─────────────────────────────────────────────


def test_aura_scales_with_owner_hp_max_not_target():
    holder = make_combatant("p", hp_max=5_000, hp=5_000, wither_aura_pct=0.04)
    fat_target = make_combatant("e", hp_max=100_000, hp=100_000)
    make_session(holder, fat_target)._process_periodic(holder)
    assert 100_000 - fat_target.hp == 200  # 5_000 × 0.04, not target-scaled


# ── Amplifiers ───────────────────────────────────────────────────────────────


def test_aura_scales_with_final_dmg_bonus():
    player = make_combatant("p", hp=5_000, wither_aura_pct=0.04, final_dmg_bonus=0.50)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    make_session(player, enemy)._process_periodic(player)

    # 400 × (1 + 0.50) = 600
    assert 20_000 - enemy.hp == 600
    assert player.hp == 5_000 + 600


def test_aura_scales_with_dot_dmg_bonus():
    player = make_combatant("p", hp=5_000, wither_aura_pct=0.04, dot_dmg_bonus=0.25)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    make_session(player, enemy)._process_periodic(player)

    # 400 × (1 + 0.25) = 500
    assert 20_000 - enemy.hp == 500


# ── Resistance / shred ───────────────────────────────────────────────────────


def test_aura_reduced_by_moc_resistance():
    player = make_combatant("p", hp=5_000, wither_aura_pct=0.04)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, resistances={"moc": 0.50})
    make_session(player, enemy)._process_periodic(player)

    # 400 × (1 - 0.50) = 200
    assert 20_000 - enemy.hp == 200
    assert player.hp == 5_000 + 200


def test_aura_moc_res_shred_recovers_damage():
    player = make_combatant("p", hp=5_000, wither_aura_pct=0.04, element_pen={"moc": 0.30})
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, resistances={"moc": 0.50})
    make_session(player, enemy)._process_periodic(player)

    # Effective res = 0.50 - 0.30 = 0.20 → 400 × 0.80 = 320
    assert 20_000 - enemy.hp == 320


def test_aura_resistance_capped_at_max_elemental_res():
    player = make_combatant("p", hp=5_000, wither_aura_pct=0.04)
    enemy = make_combatant("e", hp=20_000, hp_max=20_000, resistances={"moc": 0.99})
    make_session(player, enemy)._process_periodic(player)

    # Cap at 0.90 → 400 × (1 - 0.90) ≈ 39 (float truncation)
    assert 20_000 - enemy.hp == 39


# ── Heal interactions (bleed reduces leech) ──────────────────────────────────


def test_leech_heal_reduced_when_holder_is_bleeding():
    """Bleed on the holder reduces the leech-back heal — same rule as any
    other heal that flows through ``_apply_heal``."""
    player = make_combatant(
        "p", hp=5_000, hp_max=10_000, wither_aura_pct=0.04,
        bleed_heal_reduce=0.50,
    )
    player.bleed_stacks = 1
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    make_session(player, enemy)._process_periodic(player)

    # Damage dealt unaffected (400), but heal back is halved → 200.
    assert 20_000 - enemy.hp == 400
    assert player.hp == 5_000 + 200


# ── Solar + Wither stack independently ──────────────────────────────────────


def test_solar_and_wither_stack_independently():
    """A holder with both auras applies both per turn — moc and hoa hits land
    on the opponent in the same periodic pass."""
    player = make_combatant(
        "p", hp_max=10_000, hp=5_000,
        solar_aura_pct=0.03, wither_aura_pct=0.04,
    )
    enemy = make_combatant("e", hp=20_000, hp_max=20_000)
    make_session(player, enemy)._process_periodic(player)

    # Solar 300 + Wither 400 = 700 damage; only the wither leech heals (+400).
    assert 20_000 - enemy.hp == 700
    assert player.hp == 5_000 + 400


# NOTE: the registry-presence check for the specific wither-aura body was
# dropped with the v12 roster removal — the synthetic-combatant aura tests
# above already pin the engine mechanic without depending on shipped content.
