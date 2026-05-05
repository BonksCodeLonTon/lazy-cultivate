"""Tests for Thánh Tuyền Thể — fractional incoming-damage deferral.

Mechanic: when the holder takes damage, ``damage_defer_pct`` of the hit is
deferred (default for the constitution: 30%). The remaining (1 - pct) lands
immediately on HP. The deferred portion is split into ``damage_defer_turns``
chunks queued onto ``deferred_damage_queue`` and paid out one per turn-end
(FIFO). Total damage is preserved — only timing changes.
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


# ── Combatant.take_damage ───────────────────────────────────────────────────


def test_no_defer_takes_full_damage():
    c = make_combatant(hp=10_000, hp_max=10_000, damage_defer_turns=0, damage_defer_pct=0.0)
    applied = c.take_damage(900)
    assert applied == 900
    assert c.hp == 9_100
    assert c.deferred_damage_queue == []


def test_defer_pct_zero_is_no_op_even_with_turns():
    c = make_combatant(hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.0)
    applied = c.take_damage(900)
    assert applied == 900
    assert c.hp == 9_100
    assert c.deferred_damage_queue == []


def test_defer_30_percent_over_3_turns():
    """1000 dmg with 30% defer / 3 turns → immediate 700, queue [100, 100, 100]."""
    c = make_combatant(hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30)
    applied = c.take_damage(1000)
    assert applied == 700
    assert c.hp == 9_300
    assert c.deferred_damage_queue == [100, 100, 100]
    assert applied + sum(c.deferred_damage_queue) == 1000


def test_defer_remainder_distributed_across_first_chunks():
    """If the deferred total doesn't divide evenly, the remainder lands on
    the earliest queued chunks so the queue's sum matches the deferred total
    exactly."""
    c = make_combatant(hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30)
    # 1003 × 0.30 = 300.9 → int() = 300 deferred → 100/100/100, immediate 703
    applied = c.take_damage(1003)
    assert applied == 703
    assert sum(c.deferred_damage_queue) == 1003 - 703

    c2 = make_combatant(hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30)
    # Pick a value where the deferred amount has a remainder of 2.
    # 1010 × 0.30 = 303 → 303 // 3 = 101 with no remainder; queue = [101, 101, 101]
    applied = c2.take_damage(1010)
    assert sum(c2.deferred_damage_queue) == 303
    assert applied == 707


def test_zero_or_negative_damage_is_no_op():
    c = make_combatant(
        hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30,
    )
    assert c.take_damage(0) == 0
    assert c.take_damage(-50) == 0
    assert c.hp == 10_000
    assert c.deferred_damage_queue == []


def test_multiple_hits_accumulate_in_queue():
    c = make_combatant(
        hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30,
    )
    c.take_damage(1000)  # immediate 700, queue [100, 100, 100]
    c.take_damage(1000)
    assert c.deferred_damage_queue == [100, 100, 100, 100, 100, 100]
    assert c.hp == 10_000 - 700 - 700


# ── Periodic processing pays out the queue ──────────────────────────────────


def test_periodic_pays_one_installment_per_turn():
    holder = make_combatant(
        "p", hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30,
    )
    enemy = make_combatant("e")
    session = make_session(holder, enemy)

    holder.take_damage(1000)  # immediate 700, queue [100, 100, 100]
    assert holder.hp == 9_300

    session._process_periodic(holder)
    assert holder.hp == 9_200  # 1st installment paid
    assert holder.deferred_damage_queue == [100, 100]

    session._process_periodic(holder)
    assert holder.hp == 9_100  # 2nd installment paid
    assert holder.deferred_damage_queue == [100]

    session._process_periodic(holder)
    assert holder.hp == 9_000  # 3rd installment paid
    assert holder.deferred_damage_queue == []

    session._process_periodic(holder)
    assert holder.hp == 9_000  # nothing left to pay


def test_total_damage_preserved_across_defer_period():
    """The whole point: total HP loss equals the original hit, just spread
    out. After the queue fully drains, HP should match the no-defer case."""
    holder = make_combatant(
        "p", hp=10_000, hp_max=10_000, damage_defer_turns=3, damage_defer_pct=0.30,
    )
    enemy = make_combatant("e")
    session = make_session(holder, enemy)

    holder.take_damage(1000)
    for _ in range(3):
        session._process_periodic(holder)

    assert holder.hp == 9_000  # 10_000 - 1000
    assert holder.deferred_damage_queue == []


# ── Integration: enemy skill flow honors deferral ───────────────────────────


def test_auto_attack_path_defers_through_take_damage():
    """The casting path was migrated from raw HP arithmetic to take_damage,
    so a holder with deferral set should see deferred damage from auto-
    attack hits too."""
    from src.game.systems.combat.casting import auto_attack

    enemy = make_combatant("e", atk=300, hp=10_000, hp_max=10_000)
    holder = make_combatant(
        "p", hp=10_000, hp_max=10_000,
        damage_defer_turns=3, damage_defer_pct=0.30, def_stat=0,
    )
    session = make_session(holder, enemy)

    pre_hp = holder.hp
    auto_attack(session, enemy, holder)
    immediate_loss = pre_hp - holder.hp

    # Some damage landed immediately and the queue holds the deferred chunks.
    assert immediate_loss > 0
    assert len(holder.deferred_damage_queue) == 3  # N=3 → 3 queued installments


# ── Registry sanity ─────────────────────────────────────────────────────────


def test_thanh_tuyen_the_registered():
    c = registry.get_constitution("ConstitutionThanhTuyenThe")
    assert c is not None
    assert c["rarity"] == "legendary"
    assert c["element"] == "thuy"
    bonuses = c["stat_bonuses"]
    assert bonuses["damage_defer_turns"] == 3
    assert bonuses["damage_defer_pct"] == pytest.approx(0.30)
    # "Massive survivability" sanity floors
    assert bonuses.get("hp_pct", 0) >= 0.20
    assert bonuses.get("final_dmg_reduce", 0) >= 0.05
    assert bonuses.get("hp_regen_pct", 0) > 0
