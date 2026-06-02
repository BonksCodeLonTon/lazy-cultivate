"""Characterization tests for combat/defense_aegis.py.

The defense_aegis module exposes four hooks that read an ``_aegis`` block
off an active buff's ``effect_overrides`` entry. Each hook is exercised
once with the *capability sub-block* it watches, plus the negative case
where the sub-block is absent.

These tests are intentionally narrow — they characterize the schema
contract documented at the top of ``defense_aegis.py`` so any refactor
that moves these hooks behind ``HookRegistry`` will produce identical
observable side-effects.
"""
from __future__ import annotations

from src.game.systems.combat import defense_aegis

from tests.conftest import SeedRng, aegis_buff, make_combatant, make_session

_BUFF_KEY = "BuffTestAegis"  # not in EFFECTS — that's OK, the hooks fall back to the bare key


def test_grant_shield_on_cast_adds_flat_plus_matk_scaled_shield():
    """``shield_grant.flat + matk_scale × matk`` lands on the actor, capped by shield_cap."""
    actor = make_combatant("a", shield_max_base=1_000, matk=200)
    enemy = make_combatant("e")
    aegis_buff(actor, _BUFF_KEY, shield_grant={"flat": 100, "matk_scale": 0.5})
    session = make_session(actor, enemy)

    defense_aegis.grant_shield_on_cast(session, actor, _BUFF_KEY)

    # 100 + 0.5 × 200 = 200. Cap is 1,000 — under the cap so full amount lands.
    assert actor.shield == 200


def test_grant_shield_on_cast_skips_when_block_absent():
    """A buff with no ``shield_grant`` sub-block must leave shield untouched."""
    actor = make_combatant("a", shield_max_base=1_000, matk=200)
    enemy = make_combatant("e")
    # Only store_charge configured — shield_grant absent
    aegis_buff(actor, _BUFF_KEY, store_charge={"pct": 0.5, "cap_flat": 100})
    session = make_session(actor, enemy)

    defense_aegis.grant_shield_on_cast(session, actor, _BUFF_KEY)

    assert actor.shield == 0


def test_accumulate_stored_charge_banks_fraction_into_override():
    """50% of pre-absorb damage accumulates into ``_stored_charge``, capped at cap_flat."""
    holder = make_combatant("h", matk=100)
    override = aegis_buff(
        holder, _BUFF_KEY,
        store_charge={"pct": 0.5, "cap_flat": 1_000},
    )

    defense_aegis.accumulate_stored_charge(holder, pre_absorb_amount=200)
    assert override["_stored_charge"] == 100  # 200 × 0.5

    # Second hit accumulates on top of the existing balance.
    defense_aegis.accumulate_stored_charge(holder, pre_absorb_amount=400)
    assert override["_stored_charge"] == 300  # 100 + (400 × 0.5)

    # Cap clamps further accumulation.
    defense_aegis.accumulate_stored_charge(holder, pre_absorb_amount=10_000)
    assert override["_stored_charge"] == 1_000  # clamped to cap_flat


def test_apply_on_hit_inflicts_stamps_debuff_on_attacker_with_chance_one():
    """``on_hit_inflict`` with ``chance=1.0`` lands the debuff on the attacker."""
    attacker = make_combatant("a")
    defender = make_combatant("d")
    aegis_buff(
        defender, _BUFF_KEY,
        on_hit_inflict={"debuff": "DebuffThieuDot", "chance": 1.0, "duration": 3},
    )
    session = make_session(attacker, defender)
    session.rng = SeedRng(0.0)  # would pass any chance roll anyway, but be explicit

    defense_aegis.apply_on_hit_inflicts(session, attacker, defender, dmg=100)

    assert attacker.has_effect("DebuffThieuDot")


def test_apply_on_hit_inflicts_skips_when_dmg_is_zero():
    """0-damage taps (DoT ticks, etc.) must not trigger ``on_hit_inflict``."""
    attacker = make_combatant("a")
    defender = make_combatant("d")
    aegis_buff(
        defender, _BUFF_KEY,
        on_hit_inflict={"debuff": "DebuffThieuDot", "chance": 1.0},
    )
    session = make_session(attacker, defender)

    defense_aegis.apply_on_hit_inflicts(session, attacker, defender, dmg=0)

    assert not attacker.has_effect("DebuffThieuDot")


def test_emit_discharge_on_expire_damages_opponent_with_stored_multiplier():
    """Discharge damage = ``base + matk_scale·matk + stored × stored_mult``."""
    holder = make_combatant("h", matk=200)
    opponent = make_combatant("o", hp=10_000, hp_max=10_000)
    # Install the buff so iter still works, even though expire flow normally
    # operates against the snapshot only.
    aegis_buff(
        holder, _BUFF_KEY,
        discharge={
            "element": "loi",
            "base_dmg": 500,
            "matk_scale": 0.0,
            "stored_mult": 0.5,
        },
        stored_charge=1_000,
    )
    session = make_session(holder, opponent)
    # tick_effects normally builds this snapshot before popping the buff —
    # emit_discharge_on_expire reads exclusively from the snapshot.
    overrides_snapshot = {_BUFF_KEY: holder.effect_overrides[_BUFF_KEY]}

    defense_aegis.emit_discharge_on_expire(
        session, holder, opponent, _BUFF_KEY, overrides_snapshot,
    )

    # base=500 + matk_scale=0 + stored=1000 × stored_mult=0.5 = 1000 → opponent HP - 1000
    assert opponent.hp == 9_000
