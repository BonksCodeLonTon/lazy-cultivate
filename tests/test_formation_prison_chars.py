"""Characterization tests for combat/formation_prison.py.

The prison formation tax has two trigger points keyed off
``target.loi_kiep_an_stacks`` and the spec's ``milestone_n`` /
``consume_threshold``:

  * Milestone bolt — fires every time a multiple of ``milestone_n`` is
    just crossed by the latest tick.
  * Capstone Vạn Kiếp Phán — fires when the new stack count is at or
    above ``consume_threshold``; clears all stacks afterward.

Capstone wins over the milestone bolt at the same tick (no double-dip).
These tests lock that contract so the refactor moving prison hooks
behind ``HookRegistry`` can be verified end-to-end.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.systems.combat.formation_prison import process_loi_kiep_an_tick

from tests.conftest import make_combatant, make_session


def _make_prison_owner(**overrides) -> "Combatant":  # type: ignore[name-defined]
    actor = make_combatant("a", matk=100, **overrides)
    # Owner-side tunables for the bolt + capstone formulas. Values are
    # arbitrary but chosen so emitted damage is observable on the target.
    actor.loi_kiep_an = {
        "bolt_base_dmg": 500,
        "bolt_matk_scale": 0.0,
        "bolt_te_liet_chance": 0.0,
        "capstone_base_floor": 1_000,
        "capstone_base_per_stack": 100,
        "capstone_matk_floor": 0.0,
        "capstone_matk_per_stack": 0.0,
        "capstone_te_liet_chance": 0.0,
        "capstone_refund_mp_pct": 0.0,
    }
    return actor


def _spec(milestone_n: int = 5, consume_threshold: int = 10) -> dict:
    return {"milestone_n": milestone_n, "consume_threshold": consume_threshold}


def test_milestone_bolt_fires_when_stack_count_crosses_multiple_of_n():
    """4 → 5 stacks crosses one milestone → one bolt → observable HP drop."""
    actor = _make_prison_owner()
    target = make_combatant("t", hp=10_000, hp_max=10_000)
    target.loi_kiep_an_stacks = 5
    session = make_session(actor, target)

    process_loi_kiep_an_tick(session, actor, target, _spec(), prev_stacks=4)

    # bolt_base_dmg = 500 → at least 500 HP gone (apply_elemental clamp may
    # widen with amps, but our target has zero res/amp so it's stable).
    assert target.hp == 9_500
    # Stacks unchanged (capstone didn't fire — we're at 5, not threshold=10).
    assert target.loi_kiep_an_stacks == 5


def test_capstone_fires_at_threshold_and_clears_all_stacks():
    """9 → 10 stacks triggers capstone; debuff entry + counter both wipe.

    The exact damage number isn't pinned because the prison's own
    DebuffLoiKiepAn (installed below to verify cleanup) carries scaling
    rules that add per-stack ``dmg_taken_bonus_loi`` — at 10 stacks the
    amp is ~20%, so 2,000 base becomes ~2,400 after amp. The point of
    this test is the *control flow*: capstone fires (HP drops),
    debuff is popped, counter zeroed.
    """
    actor = _make_prison_owner()
    target = make_combatant("t", hp=20_000, hp_max=20_000)
    target.loi_kiep_an_stacks = 10
    target.effects[EffectKey.DEBUFF_LOI_KIEP_AN.value] = 5
    session = make_session(actor, target)
    starting_hp = target.hp

    process_loi_kiep_an_tick(session, actor, target, _spec(), prev_stacks=9)

    # Capstone fired — at least the base capstone damage (1000 + 100*10 = 2000) hit.
    assert starting_hp - target.hp >= 2_000
    # Stack counter zeroed via consume_stacks(); debuff entry popped.
    assert target.loi_kiep_an_stacks == 0
    assert EffectKey.DEBUFF_LOI_KIEP_AN.value not in target.effects


def test_no_bolt_fires_when_new_count_does_not_cross_a_multiple():
    """5 → 6 stacks: 5 already crossed last tick, 10 is the next multiple."""
    actor = _make_prison_owner()
    target = make_combatant("t", hp=10_000, hp_max=10_000)
    target.loi_kiep_an_stacks = 6
    session = make_session(actor, target)
    starting_hp = target.hp

    process_loi_kiep_an_tick(session, actor, target, _spec(), prev_stacks=5)

    # No milestone crossed, no capstone — target HP must be untouched.
    assert target.hp == starting_hp
    assert target.loi_kiep_an_stacks == 6
