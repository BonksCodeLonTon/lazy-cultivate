"""Characterization tests for the general-pool skill ``SkillChinhKhiCa``.

Pins the contract of:

  * ``SkillChinhKhiCa`` (src/data/skills/player/general.json) — a
    grade-3 self-empower active whose ``base_dmg`` is 0 (so it routes
    through ``apply_support_skill``) but whose ``category`` is
    ``"attack"`` (the support-routing-compatible category, same
    precedent as ``SkillCongLuanThienHa``). Carries exactly one effect
    entry, ``BuffChinhKhiCa``, with a guaranteed 1.0 apply chance.
  * ``BuffChinhKhiCa`` (src/data/effects/buffs.json) — a 3-turn buff
    bundling +15% ``dmg_bonus_<elem>`` for every element (an
    all-elemental damage bonus, applied only to element-matching hits via
    ``combat_hit.py`` — narrower than the old flat ``final_dmg_bonus``),
    +180 ``crit_dmg_rating``, 4%/turn ``hp_regen_pct`` and 1%/turn
    ``shield_regen_pct``. It is
    ``stealable`` (unlike the permanent passive ``BuffHaoNhienChinhKhi``)
    and follows the kind-based ``cleansable`` default (False for buffs).
  * ``apply_support_skill`` routes ``meta.kind == buff`` to the *actor*
    (self), not the target — the mirror of the debuff routing pinned in
    ``test_cong_luan.py``.
  * ``get_combat_modifiers`` surfaces every stat the buff carries.
  * Natural expiry after 3 ``tick_effects`` calls removes the buff.

Distinct lane vs ``BuffHaoNhienChinhKhi``: that buff is a permanent
(duration 99), non-stealable purity aura (immunity + cleanse + Âm
resist); this one is a temporary, stealable offensive crescendo with
none of those stat keys. Tests assert the two do not overlap.

Pattern mirrors ``tests/test_cong_luan.py`` (cast through the real
``apply_support_skill`` so schema-to-state plumbing is locked) and
``tests/test_cleanse.py`` (kind-based ``cleansable`` default).
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

from tests.conftest import make_combatant, make_session

_SKILL_KEY = "SkillChinhKhiCa"
_BUFF_KEY = "BuffChinhKhiCa"

# Canonical magnitudes — single source of truth for the spec.
_ELEM_DMG_BONUS = 0.15
# Every element the all-elemental buff covers (lowercase combat keys).
_ELEMENTS = ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am")
_CRIT_DMG_RATING = 180
_HP_REGEN_PCT = 0.04
_SHIELD_REGEN_PCT = 0.01
_DURATION = 3
_MP_COST = 30
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
    # ``attack`` (not ``support``) — only category enum compatible with the
    # support-routing branch, same precedent as SkillCongLuanThienHa.
    assert skill["category"] == "attack"
    assert skill["element"] is None
    assert skill["base_dmg"] == 0
    assert list(skill["effects"]) == [_BUFF_KEY]
    assert skill.get("effect_chances", {}).get(_BUFF_KEY) == 1.0


# ── Buff meta load ──────────────────────────────────────────────────────


def test_buff_meta_load():
    """The buff exists in EFFECTS with the documented stat_bonus and
    duration."""
    meta = EFFECTS.get(_BUFF_KEY)

    assert meta is not None, f"{_BUFF_KEY} missing from EFFECTS"
    assert meta.kind == EffectKind.BUFF
    assert meta.kind.value == "buff"
    # All-elemental damage bonus: one ``dmg_bonus_<elem>`` per element.
    for elem in _ELEMENTS:
        assert meta.stat_bonus.get(f"dmg_bonus_{elem}") == _ELEM_DMG_BONUS
    # The old flat all-damage key must be gone (this was the nerf).
    assert "final_dmg_bonus" not in meta.stat_bonus
    assert meta.stat_bonus.get("crit_dmg_rating") == _CRIT_DMG_RATING
    assert meta.stat_bonus.get("hp_regen_pct") == _HP_REGEN_PCT
    assert meta.stat_bonus.get("shield_regen_pct") == _SHIELD_REGEN_PCT
    assert default_duration(_BUFF_KEY) == _DURATION


def test_buff_follows_kind_based_cleansable_default():
    """``BuffChinhKhiCa`` has no explicit ``cleansable`` field, so it
    inherits the kind-based default (False for BUFF) — buffs aren't
    removed by the Quang cleanse pulse."""
    meta = EFFECTS[_BUFF_KEY]
    assert meta.kind == EffectKind.BUFF
    assert meta.cleansable is False


def test_buff_is_stealable():
    """Unlike the permanent ``BuffHaoNhienChinhKhi`` aura, this buff is
    stealable — that is the deliberate counterplay that justifies its
    aggressive numbers."""
    meta = EFFECTS[_BUFF_KEY]
    assert getattr(meta, "stealable", False) is True


# ── Cast routing: buff lands on ACTOR, not target ───────────────────────


def test_cast_applies_buff_to_actor_not_target():
    """Driving the skill through ``apply_support_skill`` stamps
    ``BuffChinhKhiCa`` on the caster with duration=3, and the enemy does
    NOT receive it. Confirms kind=buff routing sends to ``actor``."""
    actor = make_combatant("caster")
    enemy = make_combatant("enemy")
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    assert actor.has_effect(_BUFF_KEY)
    assert actor.effects[_BUFF_KEY] == _DURATION
    assert not enemy.has_effect(_BUFF_KEY), (
        "Enemy picked up the self-buff — kind routing in apply_support_skill broke"
    )


# ── Stat propagation through get_combat_modifiers ───────────────────────


def test_all_stats_propagate_to_modifiers():
    """While the buff is on the holder, ``get_combat_modifiers`` returns
    every declared stat. Locks the aggregation path."""
    holder = make_combatant("singer")
    holder.apply_effect(_BUFF_KEY, _DURATION)

    mods = get_combat_modifiers(holder)

    for elem in _ELEMENTS:
        assert mods.get(f"dmg_bonus_{elem}") == _ELEM_DMG_BONUS
    # No flat all-damage bonus leaks through — elemental hits only.
    assert not mods.get("final_dmg_bonus")
    assert mods.get("crit_dmg_rating") == _CRIT_DMG_RATING
    assert mods.get("hp_regen_pct") == _HP_REGEN_PCT
    assert mods.get("shield_regen_pct") == _SHIELD_REGEN_PCT


def test_does_not_grant_hao_nhien_stats():
    """Lane separation: Chính Khí Ca must NOT carry the purity-aura stats
    that belong to BuffHaoNhienChinhKhi (immunity / cleanse / Âm resist).
    """
    holder = make_combatant("singer")
    holder.apply_effect(_BUFF_KEY, _DURATION)

    mods = get_combat_modifiers(holder)

    assert not mods.get("debuff_immune_pct")
    assert not mods.get("cleanse_on_turn_pct")
    assert not mods.get("res_am")


# ── Duration / expiry ───────────────────────────────────────────────────


def test_duration_ticks_down_to_expiry():
    """Three ``tick_effects`` calls — the buff's natural lifespan —
    remove it from the holder's effects dict."""
    holder = make_combatant("singer")
    holder.apply_effect(_BUFF_KEY, _DURATION)

    for _ in range(_DURATION):
        holder.tick_effects()

    assert not holder.has_effect(_BUFF_KEY)
    assert _BUFF_KEY not in holder.effects


def test_buff_still_active_before_final_tick():
    """Sanity guard for the expiry test — after 2 ticks of a duration-3
    buff, it must still be active (1 turn remaining)."""
    holder = make_combatant("singer")
    holder.apply_effect(_BUFF_KEY, _DURATION)

    for _ in range(_DURATION - 1):
        holder.tick_effects()

    assert holder.has_effect(_BUFF_KEY)
    assert holder.effects[_BUFF_KEY] == 1
