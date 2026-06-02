"""Characterization tests for the general-pool skill ``SkillCongLuanThienHa``.

Pins the contract of:

  * ``SkillCongLuanThienHa`` (src/data/skills/player/general.json) — a
    grade-3 skill whose ``base_dmg`` is 0 (so it routes through
    ``apply_support_skill``) but whose ``category`` is ``"attack"``
    (matching the ``SkillAmLucHonChu`` precedent for skills that exist
    only to debuff). The skill carries exactly one effect entry,
    ``DebuffCongLuan``, with a guaranteed 1.0 apply chance.
  * ``DebuffCongLuan`` (src/data/effects/debuffs.json) — a 4-turn
    ``-15% final_dmg_bonus`` debuff that follows the kind-based
    cleansable default (True for debuffs).
  * ``apply_support_skill`` routes ``meta.kind == debuff`` to the
    *target* (not the actor), and respects ``debuff_immune_pct`` via
    ``_effective_debuff_chance``.
  * ``get_combat_modifiers`` aggregates the −0.15 magnitude on its
    holder and stacks additively with a positive ``final_dmg_bonus``
    buff (BuffNhietTinh).
  * Natural expiry after 4 ``tick_effects`` calls and Quang cleanse
    both remove the debuff.

Pattern mirrors ``tests/test_xuan_thu.py`` (cast through the real
``apply_support_skill`` so the schema-to-state plumbing is locked) and
``tests/test_cleanse.py`` (kind-based ``cleansable`` default + the
``try_cleanse`` pulse).
"""
from __future__ import annotations

import random

from src.data.registry import registry
from src.game.engine.effects import (
    EFFECTS,
    EffectKind,
    default_duration,
    get_combat_modifiers,
)
from src.game.engine.linh_can_effects.quang import try_cleanse
from src.game.systems.combat.casting import apply_support_skill

from tests.conftest import make_combatant, make_session

_SKILL_KEY = "SkillCongLuanThienHa"
_DEBUFF_KEY = "DebuffCongLuan"

# Canonical magnitudes — single source of truth for the spec.
_FINAL_DMG_BONUS = -0.15
_DURATION = 4
_MP_COST = 50
_COOLDOWN = 5
_SCROLL_GRADE = 3


# ── Schema load ─────────────────────────────────────────────────────────


def test_skill_schema_load():
    """Registry exposes the skill with the documented shape."""
    skill = registry.get_skill(_SKILL_KEY)

    assert skill is not None, f"{_SKILL_KEY} missing from registry"
    assert skill["mp_cost"] == _MP_COST
    assert skill["cooldown"] == _COOLDOWN
    assert skill["scroll_grade"] == _SCROLL_GRADE
    # Category is ``attack`` (not ``support``) because that's the only
    # category enum value compatible with the support-routing branch in
    # apply_support_skill — same precedent as SkillAmLucHonChu.
    assert skill["category"] == "attack"
    assert skill["element"] is None
    assert skill["base_dmg"] == 0
    assert list(skill["effects"]) == [_DEBUFF_KEY]
    # Guaranteed landing — the immunity gate is the only thing that can
    # short-circuit the cast.
    assert skill.get("effect_chances", {}).get(_DEBUFF_KEY) == 1.0


# ── Debuff meta load ────────────────────────────────────────────────────


def test_debuff_meta_load():
    """The debuff exists in EFFECTS with the documented stat_bonus,
    duration, and cleansable default."""
    meta = EFFECTS.get(_DEBUFF_KEY)

    assert meta is not None, f"{_DEBUFF_KEY} missing from EFFECTS"
    assert meta.kind == EffectKind.DEBUFF
    assert meta.kind.value == "debuff"
    assert meta.stat_bonus.get("final_dmg_bonus") == _FINAL_DMG_BONUS
    assert default_duration(_DEBUFF_KEY) == _DURATION


def test_debuff_follows_kind_based_cleansable_default():
    """``DebuffCongLuan`` has no explicit ``cleansable`` field in JSON,
    so it must inherit the kind-based default (True for DEBUFF). Mirrors
    the contract pinned in ``test_cleanse.py::test_every_effect_has_
    correct_default_for_its_kind``.
    """
    meta = EFFECTS[_DEBUFF_KEY]
    assert meta.kind == EffectKind.DEBUFF
    assert meta.cleansable is True, (
        "Debuff defaults to cleansable=True; if this becomes intentional, "
        "add it to test_cleanse._UNCLEANSABLE_DEBUFF_EXCEPTIONS."
    )


# ── Cast routing: debuff lands on TARGET, not actor ─────────────────────


def test_cast_applies_debuff_to_target_not_actor():
    """Driving the skill through ``apply_support_skill`` stamps
    ``DebuffCongLuan`` on the enemy with duration=4, and the caster
    does NOT receive the debuff. Confirms the kind=debuff routing in
    apply_support_skill sends to ``target``, not ``actor``."""
    actor = make_combatant("caster")
    enemy = make_combatant("enemy")
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    assert enemy.has_effect(_DEBUFF_KEY)
    assert enemy.effects[_DEBUFF_KEY] == _DURATION
    assert not actor.has_effect(_DEBUFF_KEY), (
        "Caster picked up the debuff — kind-routing in apply_support_skill broke"
    )


# ── debuff_immune_pct gate ──────────────────────────────────────────────


def test_full_immunity_blocks_landing():
    """A target with ``debuff_immune_pct == 1.0`` must not pick up the
    debuff even though the skill's apply chance is 1.0. Pins the
    ``_effective_debuff_chance`` gate that multiplies by ``1 - immune``.
    """
    actor = make_combatant("caster")
    enemy = make_combatant("enemy")
    enemy.debuff_immune_pct = 1.0
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    assert not enemy.has_effect(_DEBUFF_KEY), (
        "Full immunity failed to block a guaranteed-landing debuff"
    )


# ── Stat propagation through get_combat_modifiers ───────────────────────


def test_final_dmg_bonus_propagates_to_modifiers():
    """While the debuff is on the holder, ``get_combat_modifiers``
    returns the −0.15 ``final_dmg_bonus``. Locks the aggregation path
    (mirrors ``test_xuan_thu::test_active_layer_stats_show_up_in_combat
    _modifiers``).
    """
    target = make_combatant("victim")
    target.apply_effect(_DEBUFF_KEY, _DURATION)

    mods = get_combat_modifiers(target)

    assert mods.get("final_dmg_bonus") == _FINAL_DMG_BONUS


def test_stacks_additively_with_positive_buff():
    """``DebuffCongLuan`` (−0.15) + ``BuffNhietTinh`` (+0.20) → net
    +0.05 ``final_dmg_bonus``. Confirms additive aggregation across
    a positive buff and a negative debuff sharing the same stat key.
    """
    target = make_combatant("victim")
    target.apply_effect(_DEBUFF_KEY, _DURATION)
    target.apply_effect("BuffNhietTinh", default_duration("BuffNhietTinh"))

    mods = get_combat_modifiers(target)

    # −0.15 + 0.20 = +0.05 (float-safe; both magnitudes are exact in IEEE).
    assert mods.get("final_dmg_bonus") == _FINAL_DMG_BONUS + 0.20


# ── Duration / expiry ───────────────────────────────────────────────────


def test_duration_ticks_down_to_expiry():
    """Four ``tick_effects`` calls — matching the buff's natural
    lifespan — remove the debuff from the holder's effects dict.
    """
    target = make_combatant("victim")
    target.apply_effect(_DEBUFF_KEY, _DURATION)

    for _ in range(_DURATION):
        target.tick_effects()

    assert not target.has_effect(_DEBUFF_KEY)
    assert _DEBUFF_KEY not in target.effects


def test_debuff_still_active_before_final_tick():
    """Sanity guard for the expiry test — after 3 ticks of a duration-4
    debuff, it must still be active (1 turn remaining).
    """
    target = make_combatant("victim")
    target.apply_effect(_DEBUFF_KEY, _DURATION)

    for _ in range(_DURATION - 1):
        target.tick_effects()

    assert target.has_effect(_DEBUFF_KEY)
    assert target.effects[_DEBUFF_KEY] == 1


# ── Cleanse removes the debuff ──────────────────────────────────────────


def _make_quang_actor(*, force_chance: float = 0.95):
    """Build a maxed-Quang holder so ``try_cleanse`` always rolls
    successfully (mirrors ``tests/test_cleanse.py::_make_quang_actor``).
    """
    holder = make_combatant("quang_holder")
    holder.linh_can = ["quang"]
    holder.linh_can_levels = {"quang": 9}
    holder.cleanse_on_turn_pct = force_chance
    return holder


def test_cleanse_pulse_removes_the_debuff():
    """A Quang holder with the debuff stamped on them, running
    ``try_cleanse`` with a forced-success roll, drops the debuff (it's
    the only cleansable entry, so the pick is deterministic).
    """
    holder = _make_quang_actor()
    holder.effects = {_DEBUFF_KEY: _DURATION}

    try_cleanse(holder, random.Random(0), [], opponent=None)

    assert _DEBUFF_KEY not in holder.effects, (
        "Quang Thanh Tẩy failed to remove a cleansable debuff"
    )
