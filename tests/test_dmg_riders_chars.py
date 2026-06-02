"""Characterization tests for combat/dmg_riders.py.

The dmg_riders module replaced inline ``if skill_data.get(...):`` branches
in ``cast_skill`` with a decorator-registered dispatcher. These tests
pin:

  1. The registry size — guards against a rider being silently dropped
     or duplicated during the upcoming refactor.
  2. The skip path — when ``skill_data`` doesn't carry a rider's field,
     ``actor_mods`` is left untouched.
  3. The fire path — when a rider's field is present, ``actor_mods
     ["final_dmg_bonus"]`` grows by the rider's contribution.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import default_duration
from src.game.systems.combat.dmg_riders import (
    _DMG_BONUS_RIDERS,
    apply_dmg_bonus_riders,
)

from tests.conftest import make_combatant


def test_dmg_bonus_rider_registry_snapshot():
    """Lock the count and field names of registered _DMG_BONUS_RIDERS.

    Any rider added or removed during the refactor will trip this — at
    which point the snapshot needs to be updated *deliberately*.
    """
    fields = sorted(r.skill_field for r in _DMG_BONUS_RIDERS)
    assert fields == sorted([
        "dmg_per_target_buff_pct",
        "final_dmg_bonus_per_target_hp_lost_bucket",
        "final_dmg_bonus_per_target_debuff_count",
        # Truy Kích Liên Vũ (Phong B1) self-combo final-dmg slice — registered
        # by ``_combo_counter_fdb_rider`` on the same skill field its companion
        # base-dmg rider reads.
        "combo_counter_scaling",
        "seal_refresh_dmg_bonus",
    ])


def test_dispatcher_skips_when_no_rider_field_present():
    """Empty skill_data → dispatcher walks every rider, none fire, mods unchanged."""
    actor = make_combatant("a")
    target = make_combatant("t")
    actor_mods: dict = {}
    log: list[str] = []

    apply_dmg_bonus_riders({}, actor, target, actor_mods, log)

    assert actor_mods == {}
    assert log == []


def test_buff_count_rider_fires_and_folds_into_actor_mods():
    """``dmg_per_target_buff_pct`` × buff_count is added to final_dmg_bonus."""
    actor = make_combatant("a")
    target = make_combatant("t")
    # Stamp two BUFFs on the target (Vô Nga Kiếm Tâm, Hộ Thể Kiếm Cương).
    # The rider counts EFFECTS entries whose kind is BUFF, so we need real
    # EFFECTS keys (not made-up ones).
    for key in (EffectKey.BUFF_VO_NGA_KIEM_TAM, EffectKey.BUFF_HO_THE_KIEM_CUONG):
        target.effects[key.value] = default_duration(key.value)

    actor_mods: dict = {}
    log: list[str] = []

    # 0.10 per buff, 2 buffs → +0.20 final_dmg_bonus.
    apply_dmg_bonus_riders(
        {"dmg_per_target_buff_pct": 0.10},
        actor, target, actor_mods, log,
    )

    assert actor_mods["final_dmg_bonus"] == 0.20
    assert len(log) == 1  # rider logged one line
