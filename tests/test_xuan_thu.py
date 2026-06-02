"""Characterization tests for the Xuân Thu Nhất Bút skill + season-rotation aura.

Pins the contract of:

  * ``SkillXuanThuNhatBut`` (src/data/skills/player/general.json) — its
    public schema fields and the buff it stamps.
  * ``BuffXuanThuLuanChuyen`` (src/data/effects/buffs.json) — the six
    ``_xt_*`` config keys the aura reads (3 Spring, 3 Autumn).
  * ``auras.xuan_thu._refresh_xuan_thu_rotation`` — the PRE_TURN hook
    (priority 35) that rewrites the buff's ``stat_bonus`` each tick:
    even ``remaining`` → Spring (hp_regen_pct + final_dmg_reduce +
    shield_regen_pct), odd ``remaining`` → Autumn (final_dmg_bonus +
    crit_dmg_rating + crit_rating).
  * ``get_combat_modifiers`` strip — all six ``_xt_*`` keys are popped
    from the aggregated stat dict and never leak as real stats.
  * Cross-season leak guard — the off-season's keys (incl. the newly
    added ``shield_regen_pct`` and ``crit_rating``) MUST be popped on
    each rotation, otherwise the buff would carry both seasons
    simultaneously and roughly double its design budget.

Tests intentionally bypass ``cast_skill`` (which routes through
``apply_support_skill``) only where the registry-driven cast adds noise.
For the "cast applies buff" pin we drive the real ``apply_support_skill``
so the schema-to-state plumbing is locked too.
"""
from __future__ import annotations

import math

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS, get_combat_modifiers
from src.game.systems.combat.auras.xuan_thu import _refresh_xuan_thu_rotation
from src.game.systems.combat.casting import apply_support_skill
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase
from src.game.systems.combat.hooks import registry as hook_registry

from tests.conftest import make_combatant, make_session

_BUFF_KEY = EffectKey.BUFF_XUAN_THU_LUAN_CHUYEN.value
_SKILL_KEY = "SkillXuanThuNhatBut"

# Canonical rebalanced magnitudes — keep one source of truth in this module
# so any future spec tweak only needs to touch this block.
_SPRING_HP_REGEN_PCT = 0.06
_SPRING_FINAL_DMG_REDUCE = 0.10
_SPRING_SHIELD_REGEN_PCT = 0.015
_AUTUMN_FINAL_DMG_BONUS = 0.12
_AUTUMN_CRIT_DMG_RATING = 200
_AUTUMN_CRIT_RATING = 150


# ─── Schema load ────────────────────────────────────────────────────────


def test_skill_schema_load():
    """Registry exposes the skill with the documented shape."""
    skill = registry.get_skill(_SKILL_KEY)

    assert skill is not None, f"{_SKILL_KEY} missing from registry"
    assert skill["mp_cost"] == 21
    assert skill["cooldown"] == 6
    assert skill["scroll_grade"] == 2
    assert skill["category"] == "defense"
    assert skill["element"] is None
    assert "BuffXuanThuLuanChuyen" in skill["effects"]


def test_buff_meta_load():
    """The buff exists in EFFECTS and carries all six ``_xt_*`` config keys
    with the rebalanced magnitudes (Spring DR 0.18→0.10, Autumn dmg 0.20→0.12,
    plus the two new keys shield_regen_pct 0.015 and crit_rating 150)."""
    meta = EFFECTS.get(_BUFF_KEY)

    assert meta is not None, f"{_BUFF_KEY} missing from EFFECTS"
    assert meta.kind.value == "buff"
    # Spring magnitudes (3 keys — added shield_regen_pct).
    assert meta.stat_bonus["_xt_spring_hp_regen_pct"] == _SPRING_HP_REGEN_PCT
    assert meta.stat_bonus["_xt_spring_final_dmg_reduce"] == _SPRING_FINAL_DMG_REDUCE
    assert meta.stat_bonus["_xt_spring_shield_regen_pct"] == _SPRING_SHIELD_REGEN_PCT
    # Autumn magnitudes (3 keys — added crit_rating).
    assert meta.stat_bonus["_xt_autumn_final_dmg_bonus"] == _AUTUMN_FINAL_DMG_BONUS
    assert meta.stat_bonus["_xt_autumn_crit_dmg_rating"] == _AUTUMN_CRIT_DMG_RATING
    assert meta.stat_bonus["_xt_autumn_crit_rating"] == _AUTUMN_CRIT_RATING


# ─── Cast applies buff ──────────────────────────────────────────────────


def test_cast_applies_buff_with_xt_config_keys():
    """Driving the skill through ``apply_support_skill`` stamps the buff
    with duration=4 and all six ``_xt_*`` keys present in stat_bonus."""
    actor = make_combatant("caster")
    enemy = make_combatant("enemy")
    session = make_session(actor, enemy)
    skill_data = registry.get_skill(_SKILL_KEY)
    assert skill_data is not None

    apply_support_skill(session, skill_data, actor, enemy)

    assert actor.effects.get(_BUFF_KEY) == 4
    ovr = actor.effect_overrides.get(_BUFF_KEY) or {}
    sb = ovr.get("stat_bonus") or {}
    # Spring config keys present with rebalanced numbers.
    assert sb.get("_xt_spring_hp_regen_pct") == _SPRING_HP_REGEN_PCT
    assert sb.get("_xt_spring_final_dmg_reduce") == _SPRING_FINAL_DMG_REDUCE
    assert sb.get("_xt_spring_shield_regen_pct") == _SPRING_SHIELD_REGEN_PCT
    # Autumn config keys present with rebalanced numbers.
    assert sb.get("_xt_autumn_final_dmg_bonus") == _AUTUMN_FINAL_DMG_BONUS
    assert sb.get("_xt_autumn_crit_dmg_rating") == _AUTUMN_CRIT_DMG_RATING
    assert sb.get("_xt_autumn_crit_rating") == _AUTUMN_CRIT_RATING


# ─── Helpers ────────────────────────────────────────────────────────────


def _stamp_buff(actor, *, remaining: int = 4):
    """Install BuffXuanThuLuanChuyen on ``actor`` with the canonical override
    block — mirrors what ``apply_support_skill`` produces from the skill JSON.
    """
    actor.effects[_BUFF_KEY] = remaining
    actor.effect_overrides[_BUFF_KEY] = {
        "stat_bonus": {
            "_xt_spring_hp_regen_pct": _SPRING_HP_REGEN_PCT,
            "_xt_spring_final_dmg_reduce": _SPRING_FINAL_DMG_REDUCE,
            "_xt_spring_shield_regen_pct": _SPRING_SHIELD_REGEN_PCT,
            "_xt_autumn_final_dmg_bonus": _AUTUMN_FINAL_DMG_BONUS,
            "_xt_autumn_crit_dmg_rating": _AUTUMN_CRIT_DMG_RATING,
            "_xt_autumn_crit_rating": _AUTUMN_CRIT_RATING,
        }
    }


def _tick(actor, enemy, session):
    """Run one PRE_TURN tick through the hook directly."""
    ctx = TurnContext(actor=actor, target=enemy, session=session)
    _refresh_xuan_thu_rotation(ctx)


# ─── Season payloads ────────────────────────────────────────────────────


def test_spring_payload_when_remaining_is_four():
    """remaining=4 (even) → Spring: hp_regen_pct + final_dmg_reduce +
    shield_regen_pct land; autumn keys are absent from the live stat_bonus."""
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    _stamp_buff(actor, remaining=4)

    _tick(actor, enemy, session)

    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert math.isclose(sb["hp_regen_pct"], _SPRING_HP_REGEN_PCT, rel_tol=1e-9)
    assert math.isclose(sb["final_dmg_reduce"], _SPRING_FINAL_DMG_REDUCE, rel_tol=1e-9)
    assert math.isclose(sb["shield_regen_pct"], _SPRING_SHIELD_REGEN_PCT, rel_tol=1e-9)
    assert "final_dmg_bonus" not in sb
    assert "crit_dmg_rating" not in sb
    assert "crit_rating" not in sb


def test_autumn_payload_when_remaining_is_three():
    """After a tick takes us to remaining=3 (odd) → Autumn:
    final_dmg_bonus + crit_dmg_rating + crit_rating land; spring keys
    (incl. the new ``shield_regen_pct``) are absent."""
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    _stamp_buff(actor, remaining=3)

    _tick(actor, enemy, session)

    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert math.isclose(sb["final_dmg_bonus"], _AUTUMN_FINAL_DMG_BONUS, rel_tol=1e-9)
    assert math.isclose(sb["crit_dmg_rating"], float(_AUTUMN_CRIT_DMG_RATING), rel_tol=1e-9)
    assert math.isclose(sb["crit_rating"], float(_AUTUMN_CRIT_RATING), rel_tol=1e-9)
    assert "hp_regen_pct" not in sb
    assert "final_dmg_reduce" not in sb
    assert "shield_regen_pct" not in sb


def test_full_cycle_alternation_holds():
    """Walk the buff through all four ticks (remaining 4 → 3 → 2 → 1) and
    confirm the Spring/Autumn alternation matches the documented schedule.

    All three keys per season must be set, and the off-season keys
    (including the newly added ``shield_regen_pct`` / ``crit_rating``)
    must be absent.
    """
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)

    # remaining=4 → Spring (turn 1)
    _stamp_buff(actor, remaining=4)
    _tick(actor, enemy, session)
    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert "hp_regen_pct" in sb
    assert "final_dmg_reduce" in sb
    assert "shield_regen_pct" in sb
    assert "final_dmg_bonus" not in sb
    assert "crit_dmg_rating" not in sb
    assert "crit_rating" not in sb

    # remaining=3 → Autumn (turn 2)
    actor.effects[_BUFF_KEY] = 3
    _tick(actor, enemy, session)
    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert "final_dmg_bonus" in sb
    assert "crit_dmg_rating" in sb
    assert "crit_rating" in sb
    assert "hp_regen_pct" not in sb
    assert "final_dmg_reduce" not in sb
    assert "shield_regen_pct" not in sb

    # remaining=2 → Spring (turn 3)
    actor.effects[_BUFF_KEY] = 2
    _tick(actor, enemy, session)
    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert "hp_regen_pct" in sb
    assert "final_dmg_reduce" in sb
    assert "shield_regen_pct" in sb
    assert "final_dmg_bonus" not in sb
    assert "crit_dmg_rating" not in sb
    assert "crit_rating" not in sb

    # remaining=1 → Autumn (turn 4)
    actor.effects[_BUFF_KEY] = 1
    _tick(actor, enemy, session)
    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert "final_dmg_bonus" in sb
    assert "crit_dmg_rating" in sb
    assert "crit_rating" in sb
    assert "hp_regen_pct" not in sb
    assert "final_dmg_reduce" not in sb
    assert "shield_regen_pct" not in sb


# ─── New-key coverage: explicit pop semantics ───────────────────────────


def test_spring_tick_writes_shield_regen_and_strips_crit_rating():
    """Spring writes ``shield_regen_pct`` and MUST pop any stale
    ``crit_rating`` left over from a prior Autumn tick — otherwise both
    seasons leak at once."""
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    _stamp_buff(actor, remaining=4)
    # Pre-seed a stale Autumn key as if we'd just left Autumn.
    actor.effect_overrides[_BUFF_KEY]["stat_bonus"]["crit_rating"] = 999.0

    _tick(actor, enemy, session)

    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert math.isclose(sb["shield_regen_pct"], _SPRING_SHIELD_REGEN_PCT, rel_tol=1e-9)
    assert "crit_rating" not in sb, "stale Autumn crit_rating leaked into Spring tick"


def test_autumn_tick_writes_crit_rating_and_strips_shield_regen():
    """Autumn writes ``crit_rating`` and MUST pop any stale
    ``shield_regen_pct`` left over from a prior Spring tick."""
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    _stamp_buff(actor, remaining=3)
    # Pre-seed a stale Spring key as if we'd just left Spring.
    actor.effect_overrides[_BUFF_KEY]["stat_bonus"]["shield_regen_pct"] = 0.999

    _tick(actor, enemy, session)

    sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
    assert math.isclose(sb["crit_rating"], float(_AUTUMN_CRIT_RATING), rel_tol=1e-9)
    assert "shield_regen_pct" not in sb, "stale Spring shield_regen_pct leaked into Autumn tick"


def test_cross_season_leak_guard_over_full_cycle():
    """Critical guard flagged by the designer: walk Spring→Autumn→Spring→
    Autumn and assert that on every tick the OFF-season's NEW key
    (``shield_regen_pct`` during Autumn, ``crit_rating`` during Spring)
    is absent from the live stat_bonus.

    If either pop is dropped, both season payloads ride the same actor
    and the buff goes ~2× over its balance budget.
    """
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    _stamp_buff(actor, remaining=4)

    # turn_number → (remaining, expected_season)
    schedule = [
        (4, "spring"),
        (3, "autumn"),
        (2, "spring"),
        (1, "autumn"),
    ]
    for turn_idx, (remaining, season) in enumerate(schedule, start=1):
        actor.effects[_BUFF_KEY] = remaining
        _tick(actor, enemy, session)
        sb = actor.effect_overrides[_BUFF_KEY]["stat_bonus"]
        if season == "spring":
            assert "shield_regen_pct" in sb, (
                f"turn {turn_idx} (spring): shield_regen_pct missing"
            )
            assert "crit_rating" not in sb, (
                f"turn {turn_idx} (spring): off-season crit_rating leaked"
            )
        else:
            assert "crit_rating" in sb, (
                f"turn {turn_idx} (autumn): crit_rating missing"
            )
            assert "shield_regen_pct" not in sb, (
                f"turn {turn_idx} (autumn): off-season shield_regen_pct leaked"
            )


# ─── Expiry ─────────────────────────────────────────────────────────────


def test_buff_expires_after_four_ticks():
    """After the buff's natural lifespan ends, it is gone from the actor.

    Uses the combatant's own ``tick_effects`` (the production duration
    decrement) — four ticks should reduce duration=4 down to expiry, and
    the override entry should be cleaned up too.
    """
    actor = make_combatant("p")
    _stamp_buff(actor, remaining=4)

    for _ in range(4):
        actor.tick_effects()

    assert _BUFF_KEY not in actor.effects
    assert _BUFF_KEY not in actor.effect_overrides


# ─── Modifier strip ─────────────────────────────────────────────────────


def test_private_xt_keys_stripped_from_combat_modifiers():
    """While the buff is active, ``get_combat_modifiers`` must not return
    any ``_xt_*`` key — those are config-only, never real stats.

    Locks the strip filter coverage for ALL SIX rebalanced config keys
    (Spring: hp_regen_pct / final_dmg_reduce / shield_regen_pct;
    Autumn: final_dmg_bonus / crit_dmg_rating / crit_rating).
    """
    actor = make_combatant("p")
    _stamp_buff(actor, remaining=4)

    mods = get_combat_modifiers(actor)

    leaked = [k for k in mods if k.startswith("_xt_")]
    assert leaked == [], f"private _xt_* keys leaked into stats: {leaked}"
    # Explicitly assert every one of the six keys is stripped, so a
    # regression that adds a new private key without strip coverage
    # (or removes strip coverage for one of these) is caught here.
    for cfg_key in (
        "_xt_spring_hp_regen_pct",
        "_xt_spring_final_dmg_reduce",
        "_xt_spring_shield_regen_pct",
        "_xt_autumn_final_dmg_bonus",
        "_xt_autumn_crit_dmg_rating",
        "_xt_autumn_crit_rating",
    ):
        assert cfg_key not in mods, f"{cfg_key} leaked through get_combat_modifiers"


def test_active_layer_stats_show_up_in_combat_modifiers():
    """After a Spring tick, the public stat keys it wrote are visible in
    ``get_combat_modifiers`` — confirms the override path actually feeds
    the aggregation downstream. Also pins the rebalanced Spring numbers
    (final_dmg_reduce 0.18→0.10) and the newly added shield_regen_pct."""
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    _stamp_buff(actor, remaining=4)
    _tick(actor, enemy, session)  # Spring layer written

    mods = get_combat_modifiers(actor)

    assert math.isclose(mods.get("hp_regen_pct", 0.0), _SPRING_HP_REGEN_PCT, rel_tol=1e-9)
    assert math.isclose(
        mods.get("final_dmg_reduce", 0.0), _SPRING_FINAL_DMG_REDUCE, rel_tol=1e-9
    )
    assert math.isclose(
        mods.get("shield_regen_pct", 0.0), _SPRING_SHIELD_REGEN_PCT, rel_tol=1e-9
    )


# ─── No-op when buff absent ─────────────────────────────────────────────


def test_hook_is_no_op_when_buff_absent():
    """No buff → no exception, no override created, no effect added."""
    actor = make_combatant("p")
    enemy = make_combatant("e")
    session = make_session(actor, enemy)
    pre_effects = dict(actor.effects)
    pre_overrides = dict(actor.effect_overrides)

    _tick(actor, enemy, session)

    assert actor.effects == pre_effects
    assert actor.effect_overrides == pre_overrides
    assert _BUFF_KEY not in actor.effects
    assert _BUFF_KEY not in actor.effect_overrides


# ─── Hook registration ──────────────────────────────────────────────────


def test_xuan_thu_hook_registered_at_priority_35():
    """The aura registers a PRE_TURN hook named ``xuan_thu_rotation`` at
    priority 35 — pinned because order matters between luu_tinh (30) and
    lieu_nhu (40)."""
    pre_turn = hook_registry.hooks_for(TurnPhase.PRE_TURN)
    matching = [h for h in pre_turn if h.name == "xuan_thu_rotation"]

    assert len(matching) == 1, "xuan_thu_rotation hook not registered exactly once"
    assert matching[0].priority == 35
