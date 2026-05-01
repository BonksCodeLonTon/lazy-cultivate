"""Per-instance effect override tests.

Covers the new ``effect_overrides`` skill-JSON field that lets a skill
stamp custom magnitudes onto an effect when applied (heavier debuffs
than the meta default, longer durations, stronger DoT ticks).
"""
from __future__ import annotations

import random

import pytest

from src.game.engine.effects import (
    EFFECTS, get_combat_modifiers, get_periodic_damage,
)
from src.game.systems.combatant import Combatant


def _make_target() -> Combatant:
    return Combatant(
        key="dummy", name="Dummy", hp=10000, hp_max=10000,
        mp=100, mp_max=100, spd=10, element=None,
    )


# ── stat_bonus override ────────────────────────────────────────────────────

def test_default_stat_bonus_used_when_no_override():
    target = _make_target()
    # Meta default for DebuffXeRach: res_all = -0.08
    target.apply_effect("DebuffXeRach", 3)
    mods = get_combat_modifiers(target)
    assert mods["res_all"] == pytest.approx(-0.08)


def test_override_replaces_meta_stat_bonus_per_key():
    target = _make_target()
    target.apply_effect(
        "DebuffXeRach", 3,
        overrides={"stat_bonus": {"res_all": -0.20}},
    )
    mods = get_combat_modifiers(target)
    assert mods["res_all"] == pytest.approx(-0.20)


def test_override_can_add_stats_not_in_meta():
    target = _make_target()
    # DebuffXeRach's meta only carries res_all; an override that adds a
    # *new* key (final_dmg_reduce) should still apply on top.
    target.apply_effect(
        "DebuffXeRach", 3,
        overrides={"stat_bonus": {"res_all": -0.15, "final_dmg_reduce": -0.10}},
    )
    mods = get_combat_modifiers(target)
    assert mods["res_all"] == pytest.approx(-0.15)
    assert mods["final_dmg_reduce"] == pytest.approx(-0.10)


def test_partial_override_keeps_other_meta_stats():
    """An override that only specifies some stats must leave the rest of
    the meta's stat_bonus dict alone — not nuke them.
    """
    target = _make_target()
    # BuffHoangKim has both final_dmg_reduce: 0.15 and res_all: 0.10.
    target.apply_effect(
        "BuffHoangKim", 3,
        overrides={"stat_bonus": {"final_dmg_reduce": 0.30}},
    )
    mods = get_combat_modifiers(target)
    assert mods["final_dmg_reduce"] == pytest.approx(0.30)   # overridden
    assert mods["res_all"] == pytest.approx(0.10)            # untouched


# ── DoT override ───────────────────────────────────────────────────────────

def test_dot_pct_override_increases_tick_damage():
    target = _make_target()
    rng = random.Random(0)
    target.apply_effect("DebuffDocTo", 3)
    base_ticks = sum(d for _, d, _ in get_periodic_damage(target, rng))

    boosted = _make_target()
    boosted.apply_effect(
        "DebuffDocTo", 3,
        overrides={"dot_pct": EFFECTS["DebuffDocTo"].dot_pct * 3.0},
    )
    boosted_ticks = sum(d for _, d, _ in get_periodic_damage(boosted, random.Random(0)))
    # 3× dot_pct should yield strictly more tick damage.
    assert boosted_ticks > base_ticks


def test_dot_element_override_changes_resistance_target():
    """Stamp a non-fire element onto a fire DoT and the holder's fire
    resistance no longer reduces it (a Thủy resistance would, if set).
    """
    target = _make_target()
    target.resistances["hoa"] = 0.50   # 50% fire res
    target.apply_effect("DebuffThieuDot", 3)
    target.burn_stacks = 1
    fire_ticks = sum(d for _, d, _ in get_periodic_damage(target, random.Random(0)))

    overridden = _make_target()
    overridden.resistances["hoa"] = 0.50
    overridden.apply_effect(
        "DebuffThieuDot", 3,
        overrides={"dot_element": "kim"},  # bypass the fire resistance
    )
    overridden.burn_stacks = 1
    kim_ticks = sum(d for _, d, _ in get_periodic_damage(overridden, random.Random(0)))
    assert kim_ticks > fire_ticks


# ── Refresh / merge semantics ──────────────────────────────────────────────

def test_reapplying_with_weaker_override_does_not_overwrite_stronger():
    """Stronger magnitude wins on refresh — a heavy skill's -0.20 res
    shred shouldn't get downgraded by a follow-up light skill's -0.05.
    """
    target = _make_target()
    target.apply_effect(
        "DebuffXeRach", 3, overrides={"stat_bonus": {"res_all": -0.20}},
    )
    target.apply_effect(
        "DebuffXeRach", 3, overrides={"stat_bonus": {"res_all": -0.05}},
    )
    mods = get_combat_modifiers(target)
    assert mods["res_all"] == pytest.approx(-0.20)


def test_overrides_clear_when_effect_expires():
    target = _make_target()
    target.apply_effect(
        "DebuffXeRach", 1, overrides={"stat_bonus": {"res_all": -0.20}},
    )
    assert "DebuffXeRach" in target.effect_overrides
    target.tick_effects()   # 1-turn effect expires
    assert "DebuffXeRach" not in target.effect_overrides
    assert get_combat_modifiers(target).get("res_all", 0.0) == 0.0


# ── Skill JSON wiring ──────────────────────────────────────────────────────

def test_skill_effect_overrides_field_is_optional():
    """A skill that doesn't specify effect_overrides still works exactly
    like before — meta defaults apply.
    """
    from src.game.systems.combat.casting import inflict_debuff

    target = _make_target()
    # Build a minimal session-like object with what inflict_debuff touches.
    class _S:
        log = []
    inflict_debuff(_S(), "DebuffXeRach", EFFECTS["DebuffXeRach"], target)
    assert get_combat_modifiers(target)["res_all"] == pytest.approx(-0.08)


def test_inflict_debuff_passes_overrides_to_combatant():
    from src.game.systems.combat.casting import inflict_debuff

    target = _make_target()
    class _S:
        log = []
    inflict_debuff(
        _S(), "DebuffXeRach", EFFECTS["DebuffXeRach"], target,
        overrides={"stat_bonus": {"res_all": -0.25}, "duration": 5},
    )
    assert target.effects["DebuffXeRach"] == 5
    assert get_combat_modifiers(target)["res_all"] == pytest.approx(-0.25)
