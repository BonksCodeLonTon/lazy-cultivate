"""Tests for Niết Bàn Trùng Sinh — once-per-combat revive on lethal damage.

When a combatant carrying ``phoenix_revive_pct > 0`` would die for the first
time in a fight, ``_try_phoenix_revive`` restores HP to ``pct × hp_max``,
applies ``phoenix_revive_buff_pct`` to ATK/MATK/DEF + final_dmg_bonus +
final_dmg_reduce, purges DoT stacks, and flips the used flag so a second
death in the same fight is final.
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


# ── Trigger conditions ──────────────────────────────────────────────────────


def test_revive_does_not_fire_when_alive():
    p = make_combatant("p", hp=5_000, phoenix_revive_pct=0.7)
    session = make_session(p, make_combatant("e"))
    assert session._try_phoenix_revive(p) is False
    assert p.hp == 5_000
    assert p.phoenix_revive_used is False


def test_revive_does_not_fire_without_pct():
    p = make_combatant("p", hp=0, phoenix_revive_pct=0.0)
    session = make_session(p, make_combatant("e"))
    assert session._try_phoenix_revive(p) is False
    assert p.hp == 0


def test_revive_restores_hp_to_pct_of_max():
    p = make_combatant("p", hp=0, hp_max=10_000, phoenix_revive_pct=0.70)
    session = make_session(p, make_combatant("e"))
    assert session._try_phoenix_revive(p) is True
    assert p.hp == 7_000
    assert p.phoenix_revive_used is True


def test_revive_only_fires_once_per_fight():
    p = make_combatant("p", hp=0, hp_max=10_000, phoenix_revive_pct=0.70)
    session = make_session(p, make_combatant("e"))
    assert session._try_phoenix_revive(p) is True
    p.hp = 0  # die again
    assert session._try_phoenix_revive(p) is False
    assert p.hp == 0


# ── Stat buff ───────────────────────────────────────────────────────────────


def test_revive_applies_stat_buff():
    p = make_combatant(
        "p", hp=0, hp_max=10_000,
        atk=100, matk=200, def_stat=50,
        final_dmg_bonus=0.10, final_dmg_reduce=0.05,
        phoenix_revive_pct=0.70, phoenix_revive_buff_pct=0.20,
    )
    session = make_session(p, make_combatant("e"))
    session._try_phoenix_revive(p)

    assert p.atk == int(100 * 1.20)
    assert p.matk == int(200 * 1.20)
    assert p.def_stat == int(50 * 1.20)
    assert p.final_dmg_bonus == pytest.approx(0.30)
    assert p.final_dmg_reduce == pytest.approx(0.25)


def test_revive_buff_clamps_final_dmg_reduce_at_global_cap():
    """``final_dmg_reduce`` is bounded by ``MAX_FINAL_DMG_REDUCE`` (90%) — the
    revive buff must respect the same global cap as every other source."""
    from src.game.constants.balance import MAX_FINAL_DMG_REDUCE

    p = make_combatant(
        "p", hp=0, hp_max=10_000,
        final_dmg_reduce=0.85,
        phoenix_revive_pct=0.70, phoenix_revive_buff_pct=0.20,
    )
    session = make_session(p, make_combatant("e"))
    session._try_phoenix_revive(p)
    assert p.final_dmg_reduce == MAX_FINAL_DMG_REDUCE
    assert MAX_FINAL_DMG_REDUCE == pytest.approx(0.90)


def test_revive_with_zero_buff_only_restores_hp():
    p = make_combatant(
        "p", hp=0, hp_max=10_000, atk=100,
        phoenix_revive_pct=0.70, phoenix_revive_buff_pct=0.0,
    )
    session = make_session(p, make_combatant("e"))
    session._try_phoenix_revive(p)
    assert p.hp == 7_000
    assert p.atk == 100  # untouched


# ── Cleanse on revive ───────────────────────────────────────────────────────


def test_revive_clears_dot_stacks_and_effects():
    p = make_combatant(
        "p", hp=0, hp_max=10_000,
        burn_stacks=4, bleed_stacks=3, shock_stacks=2,
        phoenix_revive_pct=0.70,
    )
    p.effects["DebuffThieuDot"] = 5
    session = make_session(p, make_combatant("e"))
    session._try_phoenix_revive(p)
    assert p.burn_stacks == 0
    assert p.bleed_stacks == 0
    assert p.shock_stacks == 0
    assert p.effects == {}


# ── Integration with the death-check sites ──────────────────────────────────


def test_phoenix_intercepts_death_in_actor_phase():
    """End-to-end: when a hit would kill the target, the revive fires
    instead of ending the fight."""
    player = make_combatant("p", hp=10_000, hp_max=10_000)
    enemy = make_combatant(
        "e", hp=1, hp_max=10_000,
        phoenix_revive_pct=0.70, phoenix_revive_buff_pct=0.20,
    )
    session = make_session(player, enemy)
    # Manually drop enemy HP and call the actor-phase death check
    enemy.hp = 0
    result = session._actor_phase(player, enemy, actor_is_player=True)
    assert result is None  # fight continues
    assert enemy.is_alive()
    assert enemy.hp == 7_000
    assert enemy.phoenix_revive_used is True


def test_phoenix_does_not_save_a_second_death():
    """After the revive is used, the next lethal damage ends the fight."""
    player = make_combatant("p", hp=10_000, hp_max=10_000)
    enemy = make_combatant(
        "e", hp=10_000, hp_max=10_000,
        phoenix_revive_pct=0.70, phoenix_revive_used=True,  # already used
    )
    session = make_session(player, enemy)
    enemy.hp = 0
    result = session._actor_phase(player, enemy, actor_is_player=True)
    assert result is not None
    assert not enemy.is_alive()


# ── Registry: HoaPhung_Leg now ships the revive ────────────────────────────


def test_hoa_phung_leg_has_phoenix_revive():
    c = registry.get_constitution("ConstitutionHoaPhung_Leg")
    assert c is not None
    bonuses = c["stat_bonuses"]
    assert bonuses.get("phoenix_revive_pct") == pytest.approx(0.70)
    assert bonuses.get("phoenix_revive_buff_pct") == pytest.approx(0.20)
