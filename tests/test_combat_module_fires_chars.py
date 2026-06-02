"""Fires/skips characterization tests for the remaining new combat modules.

One "fires when condition met" + one "skips when condition absent" pair
for each of:

  * evade_reactive  — apply_evade_reactives
  * cast_consumers  — apply_post_cast_consumers
  * stack_stampers  — dispatch_stack_stamp
  * inflict_interceptors — dispatch_pre_stamp_interceptors

The point is not exhaustive coverage — it's a *minimal* contract pin so
the upcoming refactor cannot silently drop a dispatcher entry.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS
from src.game.systems.combat import (
    cast_consumers, evade_reactive, inflict_interceptors, stack_stampers,
)

from tests.conftest import SeedRng, aegis_buff, make_combatant, make_session


# ─── evade_reactive ─────────────────────────────────────────────────────


def test_evade_reactive_counter_fires_when_defender_has_block():
    """A buff carrying ``evade_react.counter`` damages the attacker on evade."""
    attacker = make_combatant("a", hp=10_000, hp_max=10_000)
    defender = make_combatant("d", matk=200)
    # Re-use the aegis_buff helper, then poke the _evade_react override on
    # top — same buff-override pattern, different schema key.
    aegis_buff(defender, "BuffEvadeTest")
    defender.effect_overrides["BuffEvadeTest"]["_evade_react"] = {
        "counter": {"element": "loi", "base_dmg": 500, "matk_scale": 0.0},
    }
    session = make_session(attacker, defender)

    evade_reactive.apply_evade_reactives(session, attacker, defender)

    assert attacker.hp == 9_500  # 500 base, no matk_scale, attacker has 0 shield


def test_evade_reactive_skips_when_defender_has_no_block():
    """No ``_evade_react`` block + no legacy proc_cast → attacker untouched."""
    attacker = make_combatant("a", hp=10_000, hp_max=10_000)
    defender = make_combatant("d")
    aegis_buff(defender, "BuffNoEvadeBlock")  # only _aegis block, no _evade_react
    session = make_session(attacker, defender)
    starting_hp = attacker.hp

    evade_reactive.apply_evade_reactives(session, attacker, defender)

    assert attacker.hp == starting_hp


# ─── cast_consumers ─────────────────────────────────────────────────────


def test_cast_consumer_consume_target_debuffs_strips_n_debuffs():
    """``consume_target_debuffs: 2`` removes two DEBUFF-kind entries off the target."""
    actor = make_combatant("a")
    target = make_combatant("t")
    # Three debuffs on the target; consumer should pop the first two.
    for key in (
        EffectKey.DEBUFF_THIEU_DOT.value,
        EffectKey.DEBUFF_CHAY_MAU.value,
        EffectKey.DEBUFF_SOC_DIEN.value,
    ):
        target.effects[key] = 5
    session = make_session(actor, target)

    cast_consumers.apply_post_cast_consumers(
        {"consume_target_debuffs": 2}, actor, target, session,
    )

    assert len(target.effects) == 1
    assert EffectKey.DEBUFF_SOC_DIEN.value in target.effects  # last one kept


def test_cast_consumer_skips_when_skill_data_field_absent():
    """Empty skill_data → no consumers fire, target effects untouched."""
    actor = make_combatant("a")
    target = make_combatant("t")
    target.effects[EffectKey.DEBUFF_THIEU_DOT.value] = 5
    session = make_session(actor, target)

    cast_consumers.apply_post_cast_consumers({}, actor, target, session)

    assert target.effects == {EffectKey.DEBUFF_THIEU_DOT.value: 5}


# ─── stack_stampers ─────────────────────────────────────────────────────


def test_stack_stamper_fires_for_registered_effect_key():
    """A registered key (e.g. DebuffThieuDot/burn) returns True and stamps a stack."""
    actor = make_combatant("a", burn_per_stack_pct=0.02)
    target = make_combatant("t")
    session = make_session(actor, target)
    meta = EFFECTS[EffectKey.DEBUFF_THIEU_DOT.value]

    fired = stack_stampers.dispatch_stack_stamp(
        session, EffectKey.DEBUFF_THIEU_DOT.value, actor, target,
        meta, dur=5, overrides=None,
    )

    assert fired is True
    assert target.burn_stacks == 1


def test_stack_stamper_returns_false_for_unregistered_effect_key():
    """An unknown effect key returns False so the caller falls through to the generic tail."""
    actor = make_combatant("a")
    target = make_combatant("t")
    session = make_session(actor, target)
    # Pick a known meta to satisfy the type, but use a sentinel key absent
    # from _STACK_STAMPERS — the dispatcher must short-circuit on miss.
    meta = EFFECTS[EffectKey.DEBUFF_THIEU_DOT.value]

    fired = stack_stampers.dispatch_stack_stamp(
        session, "UnknownNotRegisteredKey", actor, target,
        meta, dur=5, overrides=None,
    )

    assert fired is False
    assert target.burn_stacks == 0  # no stack landed


# ─── inflict_interceptors ───────────────────────────────────────────────


def test_inflict_interceptor_poison_immunity_aborts_stamp():
    """Target with ``poison_immunity=True`` shrugs off DebuffDocTo before the stamp lands."""
    actor = make_combatant("a")
    target = make_combatant("t", poison_immunity=True)
    session = make_session(actor, target)
    meta = EFFECTS[EffectKey.DEBUFF_DOC_TO.value]

    aborted = inflict_interceptors.dispatch_pre_stamp_interceptors(
        session, EffectKey.DEBUFF_DOC_TO.value, meta, target, actor, overrides=None,
    )

    assert aborted is True
    assert EffectKey.DEBUFF_DOC_TO.value not in target.effects


def test_inflict_interceptor_does_not_abort_when_no_immunity():
    """Without the immunity flag, the interceptor chain falls through (returns False)."""
    actor = make_combatant("a")
    target = make_combatant("t", poison_immunity=False)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)  # keep the effect_resist roll above any chance
    meta = EFFECTS[EffectKey.DEBUFF_DOC_TO.value]

    aborted = inflict_interceptors.dispatch_pre_stamp_interceptors(
        session, EffectKey.DEBUFF_DOC_TO.value, meta, target, actor, overrides=None,
    )

    assert aborted is False
