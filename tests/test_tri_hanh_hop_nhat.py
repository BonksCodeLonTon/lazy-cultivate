"""Characterization tests for the general-pool skill ``SkillTriHanhHopNhat``.

Pins the contract of:

  * ``SkillTriHanhHopNhat`` (src/data/skills/player/general.json) — a
    grade-3, element-neutral **defense** active (``base_dmg`` 0 → routes
    through ``apply_support_skill``). Self-buff carrying an ``_aegis`` block
    with the ``adapt_elem_res`` capability.
  * ``BuffTriHanhHopNhat`` (src/data/effects/buffs.json) — a 5-turn buff
    that grants NO flat resist; instead it *adapts* per-element resistance to
    whatever element is striking the holder (+8% per same-element hit, capped
    at 40%) and **resets** when a different element lands.
  * The ``defense_aegis.adapt_elem_res`` hook, fired from
    ``procs.apply_reactive_damage`` (which carries the hit's element — unlike
    ``Combatant.take_damage``).
  * **Registry safety**: adapting mutates a per-combatant override dict, never
    the shared registry ``_aegis`` block.

Pattern mirrors ``tests/test_thien_menh_quy_nhat.py`` (aegis-on-cast) and
``tests/test_defense_aegis_chars.py`` (hook mechanics).
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.engine.effects import (
    EFFECTS,
    EffectKind,
    default_duration,
    get_combat_modifiers,
)
from src.game.systems.combat import defense_aegis
from src.game.systems.combat.casting import apply_support_skill

from tests.conftest import make_combatant, make_session

_SKILL_KEY = "SkillTriHanhHopNhat"
_BUFF_KEY = "BuffTriHanhHopNhat"

# Canonical magnitudes — single source of truth for the spec.
_STEP = 0.08
_CAP = 0.4
_DURATION = 5
_MP_COST = 40
_COOLDOWN = 6
_SCROLL_GRADE = 3


def _cast(actor, enemy):
    session = make_session(actor, enemy)
    apply_support_skill(session, registry.get_skill(_SKILL_KEY), actor, enemy)
    return session


# ── Schema load ─────────────────────────────────────────────────────────


def test_skill_schema_load():
    """Registry exposes the skill with the documented shape + adapt_elem_res."""
    skill = registry.get_skill(_SKILL_KEY)

    assert skill is not None, f"{_SKILL_KEY} missing from registry"
    assert skill["mp_cost"] == _MP_COST
    assert skill["cooldown"] == _COOLDOWN
    assert skill["scroll_grade"] == _SCROLL_GRADE
    assert skill["category"] == "defense"
    assert skill["element"] is None
    assert skill["base_dmg"] == 0
    assert list(skill["effects"]) == [_BUFF_KEY]
    assert skill.get("effect_chances", {}).get(_BUFF_KEY) == 1.0

    override = skill["effect_overrides"][_BUFF_KEY]
    assert override["duration"] == _DURATION
    # No flat mitigation seed — resist is purely adaptive + per-element.
    assert "stat_bonus" not in override
    assert override["_aegis"]["adapt_elem_res"] == {"step": _STEP, "cap": _CAP}


def test_buff_meta_load():
    """The buff is a 5-turn buff carrying no flat resist of its own."""
    meta = EFFECTS.get(_BUFF_KEY)

    assert meta is not None, f"{_BUFF_KEY} missing from EFFECTS"
    assert meta.kind == EffectKind.BUFF
    assert not any(k.startswith("res_") for k in meta.stat_bonus)
    assert "final_dmg_reduce" not in meta.stat_bonus
    assert default_duration(_BUFF_KEY) == _DURATION


def test_buff_follows_kind_based_cleansable_default():
    meta = EFFECTS[_BUFF_KEY]
    assert meta.kind == EffectKind.BUFF
    assert meta.cleansable is False


# ── Cast routing ────────────────────────────────────────────────────────


def test_cast_applies_buff_with_no_initial_resist():
    """Casting stamps the buff + _aegis on the actor but grants no resist
    until an elemental hit teaches one; the enemy is untouched."""
    actor = make_combatant("scholar")
    enemy = make_combatant("enemy")
    _cast(actor, enemy)

    assert actor.has_effect(_BUFF_KEY)
    assert actor.effects[_BUFF_KEY] == _DURATION
    assert "adapt_elem_res" in actor.effect_overrides[_BUFF_KEY]["_aegis"]
    mods = get_combat_modifiers(actor)
    assert not any(k.startswith("res_") for k in mods)
    assert not enemy.has_effect(_BUFF_KEY)


# ── Adaptive ramp (same element) ────────────────────────────────────────


def test_resist_adapts_upward_for_repeated_element_and_caps():
    """Repeated same-element hits ramp ``res_<elem>`` one step toward the cap."""
    actor = make_combatant("scholar")
    enemy = make_combatant("enemy")
    _cast(actor, enemy)

    expected = 0.0
    for _ in range(4):
        defense_aegis.adapt_elem_res(actor, "hoa", incoming=1_000)
        expected = min(_CAP, expected + _STEP)
        assert get_combat_modifiers(actor).get("res_hoa") == expected

    # Drive past the cap — never exceeds it.
    for _ in range(5):
        defense_aegis.adapt_elem_res(actor, "hoa", incoming=1_000)
    assert get_combat_modifiers(actor).get("res_hoa") == _CAP


# ── Reset on element change ─────────────────────────────────────────────


def test_switching_element_resets_prior_resist():
    """A different element drops the old element's accrued resist and starts
    the new element fresh at one step."""
    actor = make_combatant("scholar")
    enemy = make_combatant("enemy")
    _cast(actor, enemy)

    # Build up hoa resist over three hits → 24%.
    for _ in range(3):
        defense_aegis.adapt_elem_res(actor, "hoa", incoming=1_000)
    assert get_combat_modifiers(actor).get("res_hoa") == _STEP * 3

    # A kim hit resets: hoa gone, kim starts at one step.
    defense_aegis.adapt_elem_res(actor, "kim", incoming=1_000)
    mods = get_combat_modifiers(actor)
    assert not mods.get("res_hoa")
    assert mods.get("res_kim") == _STEP
    assert actor.effect_overrides[_BUFF_KEY]["_adapt_element"] == "kim"

    # Continuing on kim ramps from the reset baseline.
    defense_aegis.adapt_elem_res(actor, "kim", incoming=1_000)
    assert get_combat_modifiers(actor).get("res_kim") == _STEP * 2


# ── Guards ──────────────────────────────────────────────────────────────


def test_non_elemental_hit_does_not_adapt():
    """A None-element (physical) hit or a 0-damage tap teaches nothing."""
    actor = make_combatant("scholar")
    enemy = make_combatant("enemy")
    _cast(actor, enemy)

    defense_aegis.adapt_elem_res(actor, None, incoming=1_000)
    defense_aegis.adapt_elem_res(actor, "hoa", incoming=0)

    mods = get_combat_modifiers(actor)
    assert not any(k.startswith("res_") for k in mods)
    assert "_adapt_element" not in actor.effect_overrides[_BUFF_KEY]


def test_adapt_hook_noop_without_capability():
    """A buff that declares no ``adapt_elem_res`` is left untouched."""
    from tests.conftest import aegis_buff

    holder = make_combatant("h")
    aegis_buff(holder, "BuffTestAegis", store_charge={"pct": 0.5, "cap_flat": 100})

    defense_aegis.adapt_elem_res(holder, "hoa", incoming=1_000)

    stats = holder.effect_overrides["BuffTestAegis"].get("stat_bonus", {})
    assert "res_hoa" not in stats


# ── Integration through the on-hit path ─────────────────────────────────


def test_adapts_through_apply_reactive_damage():
    """The real defender on-hit path threads the hit's element into the hook."""
    from src.game.systems.combat.procs import apply_reactive_damage

    holder = make_combatant("scholar")
    attacker = make_combatant("attacker")
    session = _cast(holder, attacker)

    apply_reactive_damage(session, attacker, holder, dmg=1_000, skill_element="loi")

    assert get_combat_modifiers(holder).get("res_loi") == _STEP


# ── Registry safety ─────────────────────────────────────────────────────


def test_adapting_does_not_corrupt_shared_registry_skill_data():
    """Adapting one combatant must not bleed into the registry _aegis block,
    and an independent caster starts clean."""
    actor = make_combatant("a")
    enemy = make_combatant("e")
    _cast(actor, enemy)
    for _ in range(3):
        defense_aegis.adapt_elem_res(actor, "hoa", incoming=1_000)
    assert get_combat_modifiers(actor).get("res_hoa") == _STEP * 3

    # Registry _aegis config unchanged (no stat_bonus / state leaked into it).
    fresh = registry.get_skill(_SKILL_KEY)
    assert fresh["effect_overrides"][_BUFF_KEY]["_aegis"]["adapt_elem_res"] == {
        "step": _STEP, "cap": _CAP,
    }
    assert "stat_bonus" not in fresh["effect_overrides"][_BUFF_KEY]

    # A brand-new caster has no adapted resist.
    other = make_combatant("b")
    enemy2 = make_combatant("e2")
    _cast(other, enemy2)
    assert not any(k.startswith("res_") for k in get_combat_modifiers(other))
