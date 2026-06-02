"""Characterization tests for the general-pool skill ``SkillThienMenhQuyNhat``.

Pins the contract of:

  * ``SkillThienMenhQuyNhat`` (src/data/skills/player/general.json) — a
    grade-3, element-neutral **defense** active (``base_dmg`` 0, so it
    routes through ``apply_support_skill``). It is a *defense aegis*: its
    ``effect_overrides`` carry an ``_aegis`` block declaring all three
    damage-banking capabilities (``shield_grant`` on cast,
    ``store_charge`` while held, ``discharge`` on natural expiry).
  * ``BuffThienMenhQuyNhat`` (src/data/effects/buffs.json) — a 3-turn buff
    whose mitigation is NOT flat: ``scaling_rules`` derive
    ``final_dmg_reduce`` and ``res_all`` from ``hp_missing_pct`` (the
    closer to death, the stronger the ward). +4% DR per 10% HP lost
    (cap 30%) and +2% res per 10% HP lost (cap 15%). Kind-based
    ``cleansable`` default (False for buffs).
  * Casting through ``apply_support_skill`` stamps the buff + ``_aegis``
    on the *actor* (self) and grants the on-cast shield.
  * The real ``_aegis`` config wired in JSON drives the existing generic
    aegis hooks (``accumulate_stored_charge`` / ``emit_discharge_on_expire``)
    to the documented numbers.

The generic aegis hook mechanics themselves are characterized in
``tests/test_defense_aegis_chars.py``; this suite locks the *Thiên Mệnh
Quy Nhất* schema and one end-to-end cast→store→discharge loop so a tuning
change to the JSON is caught here.

Distinct lane vs the Loi aegis skills (Lôi Thần Khải etc.): this is a
general (element None) ward and carries no on-hit CC — pure
ward → bank → return.
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

_SKILL_KEY = "SkillThienMenhQuyNhat"
_BUFF_KEY = "BuffThienMenhQuyNhat"

# Canonical magnitudes — single source of truth for the spec.
# Mitigation is scaled off hp_missing_pct (per 10% HP lost), not flat.
_DR_PER_10PCT = 0.04
_DR_CAP = 0.3
_RES_PER_10PCT = 0.02
_RES_CAP = 0.15
_DURATION = 3
_MP_COST = 47
_COOLDOWN = 6
_SCROLL_GRADE = 3

# Aegis tunables (mirror the JSON effect_overrides._aegis block).
_SHIELD_FLAT = 600
_SHIELD_MATK_SCALE = 3.0
_STORE_PCT = 0.25
_STORE_CAP_MATK_SCALE = 4.0
_DISCHARGE_BASE = 500
_DISCHARGE_MATK_SCALE = 0.4
_DISCHARGE_STORED_MULT = 1.5


# ── Schema load ─────────────────────────────────────────────────────────


def test_skill_schema_load():
    """Registry exposes the skill with the documented shape."""
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


def test_skill_aegis_config():
    """The skill JSON wires a full three-capability ``_aegis`` block."""
    skill = registry.get_skill(_SKILL_KEY)
    aegis = skill["effect_overrides"][_BUFF_KEY]["_aegis"]

    assert aegis["shield_grant"] == {
        "flat": _SHIELD_FLAT,
        "matk_scale": _SHIELD_MATK_SCALE,
    }
    assert aegis["store_charge"]["pct"] == _STORE_PCT
    assert aegis["store_charge"]["cap_matk_scale"] == _STORE_CAP_MATK_SCALE
    discharge = aegis["discharge"]
    assert discharge["element"] == "quang"
    assert discharge["base_dmg"] == _DISCHARGE_BASE
    assert discharge["matk_scale"] == _DISCHARGE_MATK_SCALE
    assert discharge["stored_mult"] == _DISCHARGE_STORED_MULT
    # General ward — no on-hit CC lane (that belongs to the Loi aegis).
    assert "cc" not in discharge
    assert "on_hit_inflict" not in aegis


# ── Buff meta load ──────────────────────────────────────────────────────


def test_buff_meta_load():
    """The buff carries scaling-rule placeholders (not flat mitigation) and
    two ``hp_missing_pct`` → mitigation rules with the documented caps."""
    meta = EFFECTS.get(_BUFF_KEY)

    assert meta is not None, f"{_BUFF_KEY} missing from EFFECTS"
    assert meta.kind == EffectKind.BUFF
    # No flat mitigation — it is derived via scaling_rules.
    assert "final_dmg_reduce" not in meta.stat_bonus
    assert "res_all" not in meta.stat_bonus
    assert meta.stat_bonus.get("tmqn_per_10pct_hp_lost_dr") == _DR_PER_10PCT
    assert meta.stat_bonus.get("tmqn_per_10pct_hp_lost_res") == _RES_PER_10PCT

    rules = {r["output"]: r for r in meta.scaling_rules}
    dr_rule = rules["final_dmg_reduce"]
    assert dr_rule["key"] == "tmqn_per_10pct_hp_lost_dr"
    assert dr_rule["source"] == "hp_missing_pct"
    assert dr_rule["bucket"] == 0.1
    assert dr_rule["max_output"] == _DR_CAP
    res_rule = rules["res_all"]
    assert res_rule["source"] == "hp_missing_pct"
    assert res_rule["bucket"] == 0.1
    assert res_rule["max_output"] == _RES_CAP

    assert default_duration(_BUFF_KEY) == _DURATION


def test_buff_follows_kind_based_cleansable_default():
    """No explicit ``cleansable`` field → kind default (False for BUFF)."""
    meta = EFFECTS[_BUFF_KEY]
    assert meta.kind == EffectKind.BUFF
    assert meta.cleansable is False


# ── Cast routing: buff + aegis + shield land on ACTOR ───────────────────


def test_cast_applies_buff_aegis_and_shield_to_actor():
    """Driving the skill through ``apply_support_skill`` stamps the buff
    (duration 3) and the ``_aegis`` block on the caster, grants the on-cast
    shield, and leaves the enemy untouched."""
    actor = make_combatant("warden", matk=200, shield_max_base=10_000)
    enemy = make_combatant("enemy")
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    assert actor.has_effect(_BUFF_KEY)
    assert actor.effects[_BUFF_KEY] == _DURATION
    # _aegis block stamped onto the actor's effect_overrides.
    assert "_aegis" in actor.effect_overrides[_BUFF_KEY]
    # Shield = flat + matk_scale × matk = 600 + 3.0 × 200 = 1200 (under cap).
    assert actor.shield == _SHIELD_FLAT + int(_SHIELD_MATK_SCALE * 200)
    assert not enemy.has_effect(_BUFF_KEY)


def test_mitigation_scales_with_hp_missing():
    """``get_combat_modifiers`` derives DR + resist from ``hp_missing_pct``:
    zero at full HP, scaling per 10% lost, clamped at the caps. Also
    confirms the placeholder keys never leak as real stats."""
    holder = make_combatant("warden", hp=10_000, hp_max=10_000)
    holder.apply_effect(_BUFF_KEY, _DURATION)

    # Full HP — no HP missing, so no mitigation, and no placeholder leak.
    mods = get_combat_modifiers(holder)
    assert not mods.get("final_dmg_reduce")
    assert not mods.get("res_all")
    assert "tmqn_per_10pct_hp_lost_dr" not in mods
    assert "tmqn_per_10pct_hp_lost_res" not in mods

    # Half HP — 5 buckets of 10% → +20% DR, +10% res.
    holder.hp = 5_000
    mods = get_combat_modifiers(holder)
    assert mods.get("final_dmg_reduce") == _DR_PER_10PCT * 5
    assert mods.get("res_all") == _RES_PER_10PCT * 5

    # 10% HP — 9 buckets would give 36%/18%, clamped to the caps.
    holder.hp = 1_000
    mods = get_combat_modifiers(holder)
    assert mods.get("final_dmg_reduce") == _DR_CAP
    assert mods.get("res_all") == _RES_CAP


# ── End-to-end: cast → bank incoming → discharge on expiry ──────────────


def test_full_loop_store_then_discharge():
    """Cast the real skill, take a hit (banks 25% capped at 4×MATK), then
    expiry discharges ``base + matk_scale·matk + stored × stored_mult``."""
    actor = make_combatant("warden", matk=100, shield_max_base=10_000)
    enemy = make_combatant("enemy", hp=10_000, hp_max=10_000)
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    # Take a 1,000 hit. Bank = min(0.25 × 1000, 4.0 × matk=100→400) = 250.
    actor.take_damage(1_000)
    stored = int(actor.effect_overrides[_BUFF_KEY].get("_stored_charge", 0))
    assert stored == 250

    # Discharge reads from the pre-tick snapshot (tick_effects pops the live
    # override), mirroring the periodic-expiry call site.
    snapshot = {_BUFF_KEY: dict(actor.effect_overrides[_BUFF_KEY])}
    enemy_hp_before = enemy.hp

    defense_aegis.emit_discharge_on_expire(
        session, actor, enemy, _BUFF_KEY, snapshot,
    )

    # 500 + 0.4×100 + 250×1.5 = 500 + 40 + 375 = 915.
    expected = (
        _DISCHARGE_BASE
        + int(_DISCHARGE_MATK_SCALE * 100)
        + int(stored * _DISCHARGE_STORED_MULT)
    )
    assert enemy_hp_before - enemy.hp == expected
