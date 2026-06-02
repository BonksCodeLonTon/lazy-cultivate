"""Characterization tests for the general-pool skill ``SkillMovQuanTuChiPhong``.

Pins the contract of:

  * ``SkillMovQuanTuChiPhong`` (src/data/skills/player/general.json) — a
    grade-3, element-neutral **movement** active (``base_dmg`` 0, so it
    routes through ``apply_support_skill``). Self-buff, no damage.
  * ``BuffQuanTuChiPhong`` (src/data/effects/buffs.json) — a 3-turn
    composure stance: +20% ``spd_pct``, +350 ``evasion_rating``, +250
    ``crit_res_rating``. Kind-based ``cleansable`` default (False).
  * Casting through ``apply_support_skill`` stamps the buff on the *actor*.
  * ``get_combat_modifiers`` surfaces every stat, and the live ``spd_pct``
    flows through ``effective_spd`` (the movement payoff).
  * Natural expiry after 3 ``tick_effects`` calls removes the buff.

Pattern mirrors ``tests/test_chinh_khi_ca.py`` and
``tests/test_thien_menh_quy_nhat.py``.
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.engine.effects import (
    EFFECTS,
    EffectKind,
    default_duration,
    get_combat_modifiers,
)
from src.game.systems.combat.casting import apply_support_skill
from src.game.systems.combat.helpers import effective_spd

from tests.conftest import make_combatant, make_session

_SKILL_KEY = "SkillMovQuanTuChiPhong"
_BUFF_KEY = "BuffQuanTuChiPhong"

# Canonical magnitudes — single source of truth for the spec.
_SPD_PCT = 0.2
_EVASION_RATING = 350
_CRIT_RES_RATING = 250
_DURATION = 3
_MP_COST = 37
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
    assert skill["category"] == "movement"
    assert skill["element"] is None
    assert skill["base_dmg"] == 0
    assert list(skill["effects"]) == [_BUFF_KEY]
    assert skill.get("effect_chances", {}).get(_BUFF_KEY) == 1.0


# ── Buff meta load ──────────────────────────────────────────────────────


def test_buff_meta_load():
    """The buff exists in EFFECTS with the documented stat_bonus + duration."""
    meta = EFFECTS.get(_BUFF_KEY)

    assert meta is not None, f"{_BUFF_KEY} missing from EFFECTS"
    assert meta.kind == EffectKind.BUFF
    assert meta.stat_bonus.get("spd_pct") == _SPD_PCT
    assert meta.stat_bonus.get("evasion_rating") == _EVASION_RATING
    assert meta.stat_bonus.get("crit_res_rating") == _CRIT_RES_RATING
    assert default_duration(_BUFF_KEY) == _DURATION


def test_buff_follows_kind_based_cleansable_default():
    """No explicit ``cleansable`` field → kind default (False for BUFF)."""
    meta = EFFECTS[_BUFF_KEY]
    assert meta.kind == EffectKind.BUFF
    assert meta.cleansable is False


# ── Cast routing: buff lands on ACTOR ───────────────────────────────────


def test_cast_applies_buff_to_actor_not_target():
    """Driving the skill through ``apply_support_skill`` stamps the buff on
    the caster (duration 3); the enemy does not receive it."""
    actor = make_combatant("junzi")
    enemy = make_combatant("enemy")
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    assert actor.has_effect(_BUFF_KEY)
    assert actor.effects[_BUFF_KEY] == _DURATION
    assert not enemy.has_effect(_BUFF_KEY)


# ── Stat propagation + movement payoff ──────────────────────────────────


def test_all_stats_propagate_to_modifiers():
    """While held, ``get_combat_modifiers`` returns every declared stat."""
    holder = make_combatant("junzi")
    holder.apply_effect(_BUFF_KEY, _DURATION)

    mods = get_combat_modifiers(holder)

    assert mods.get("spd_pct") == _SPD_PCT
    assert mods.get("evasion_rating") == _EVASION_RATING
    assert mods.get("crit_res_rating") == _CRIT_RES_RATING


def test_spd_pct_flows_through_effective_spd():
    """The movement payoff: the +20% ``spd_pct`` raises live SPD."""
    holder = make_combatant("junzi", spd=100)
    base = effective_spd(holder)
    assert base == 100

    holder.apply_effect(_BUFF_KEY, _DURATION)
    # round(100 × 1.20) = 120
    assert effective_spd(holder) == 120


# ── Duration / expiry ───────────────────────────────────────────────────


def test_duration_ticks_down_to_expiry():
    """Three ``tick_effects`` calls — the buff's lifespan — remove it."""
    holder = make_combatant("junzi")
    holder.apply_effect(_BUFF_KEY, _DURATION)

    for _ in range(_DURATION):
        holder.tick_effects()

    assert not holder.has_effect(_BUFF_KEY)
    assert _BUFF_KEY not in holder.effects
