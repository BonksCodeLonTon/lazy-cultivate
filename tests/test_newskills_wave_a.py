"""Characterization tests for the three Wave-A skills.

Covers:
  * **Kim Phệ Giáp** (``SkillKimPheGiap``) — the ``_kim_devour_armor_rider``
    base-dmg rider in ``dmg_riders.py`` + the uncleansable ``DebuffKimPheGiap``
    def-shred debuff (``def_pct: -0.25``).
  * **Lôi Tù Cấm Ngục** (``SkillLoiTuCamNguc``) — the
    ``_same_element_streak_consumer`` two-phase consumer in
    ``two_phase_consumers.py`` (streak-keyed base_dmg + element-pen +
    forced Sốc Điện), plus the ``cast_skill`` streak-update rule.
  * **Thổ Độn Tàng Hình** (``SkillThoDonTangHinh``) — data-only burrow buff
    ``BuffThoDon`` (70% final_dmg_reduce) wired to the defense-aegis
    store-charge / discharge cycle.

Each mechanic is driven at the lowest deterministic level: rider/consumer
functions get called directly where the contract is pure math, and the
full ``cast_skill`` path is used only for the streak-update rule (which
lives inside ``cast_skill`` itself) and the burrow-buff installation.
"""
from __future__ import annotations

import random

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, get_combat_modifiers
from src.game.systems.combat import casting, defense_aegis
from src.game.systems.combat.dmg_riders import _kim_devour_armor_rider
from src.game.systems.combat.two_phase_consumers import (
    CastContext, _same_element_streak_consumer,
)

from tests.conftest import SeedRng, make_combatant, make_session

_KIM_SKILL_KEY = "SkillKimPheGiap"
_LOI_SKILL_KEY = "SkillLoiTuCamNguc"
_THO_SKILL_KEY = "SkillThoDonTangHinh"


# ════════════════════════════════════════════════════════════════════════
# 1. Kim Phệ Giáp — base-dmg rider math
# ════════════════════════════════════════════════════════════════════════


def _kim_skill_data() -> dict:
    """The three rider fields exactly as declared on ``SkillKimPheGiap``."""
    return {
        "bonus_dmg_per_target_def": 4.0,
        "bonus_dmg_per_target_shield_pct": 0.20,
        "bonus_dmg_cap": 4500,
    }


def test_kim_rider_bonus_is_def_plus_shield_under_cap():
    """def_stat=800, shield=5000 → 800×4 + 5000×0.2 = 4200 (under the 4500 cap)."""
    actor = make_combatant("a")
    target = make_combatant("e", def_stat=800, shield=5_000)

    result = _kim_devour_armor_rider(
        _kim_skill_data(), actor, target, base_dmg=300, actor_mods={},
    )

    assert result is not None
    bonus, _log = result
    assert bonus == min(4500, int(800 * 4 + 5_000 * 0.20))
    assert bonus == 4200


def test_kim_rider_clamps_to_cap():
    """def_stat=2000, shield=0 → 2000×4 = 8000, clamped to the 4500 cap."""
    actor = make_combatant("a")
    target = make_combatant("e", def_stat=2_000, shield=0)

    result = _kim_devour_armor_rider(
        _kim_skill_data(), actor, target, base_dmg=300, actor_mods={},
    )

    assert result is not None
    bonus, _log = result
    assert bonus == 4500  # min(4500, 8000)


def test_kim_rider_noop_when_fields_absent():
    """A skill_data without the devour fields → rider returns None (no bonus)."""
    actor = make_combatant("a")
    target = make_combatant("e", def_stat=2_000, shield=5_000)

    # No bonus_dmg_per_target_def / bonus_dmg_per_target_shield_pct present.
    result = _kim_devour_armor_rider(
        {"base_dmg": 300}, actor, target, base_dmg=300, actor_mods={},
    )

    assert result is None


# ════════════════════════════════════════════════════════════════════════
# 2. Kim Phệ Giáp — DebuffKimPheGiap def shred (non-compounding + uncleansable)
# ════════════════════════════════════════════════════════════════════════


def test_kim_def_shred_does_not_compound_on_reapply():
    """Applying DebuffKimPheGiap twice still yields def_pct == -0.25 (override,
    not a stacked sum) — the debuff is a single dict-keyed entry."""
    target = make_combatant("e")

    target.apply_effect("DebuffKimPheGiap", 99)
    target.apply_effect("DebuffKimPheGiap", 99)  # re-cast / refresh

    mods = get_combat_modifiers(target)
    assert mods["def_pct"] == -0.25


def test_kim_def_shred_is_uncleansable():
    """DebuffKimPheGiap is registered uncleansable so it can't be dispelled."""
    assert EFFECTS["DebuffKimPheGiap"].cleansable is False


# ════════════════════════════════════════════════════════════════════════
# 3. Lôi Tù Cấm Ngục — same-element streak consumer
# ════════════════════════════════════════════════════════════════════════


def _loi_streak_spec() -> dict:
    """The streak-scaling block exactly as declared on ``SkillLoiTuCamNguc``."""
    return {
        "per_streak_base_dmg": 240,
        "per_streak_element_pen_pct": 0.04,
        "streak_cap": 5,
        "guaranteed_shock_at": 3,
    }


def _loi_ctx(actor, target, session, *, base_dmg: int = 320) -> CastContext:
    return CastContext(
        actor=actor, target=target, session=session,
        skill_element="loi",
        skill_data={
            "element": "loi",
            "base_dmg": base_dmg,
            "effect_chances": {"DebuffSocDien": 0.6},
            "same_element_streak_scaling": _loi_streak_spec(),
        },
        actor_mods={},
        base_dmg=base_dmg,
    )


def test_loi_streak_adds_base_dmg_and_pen_at_streak_five():
    """streak=5 → tiers=4 → +240×4=960 base_dmg and +0.04×4=0.16 Lôi pen."""
    actor = make_combatant("a")
    actor.same_element_streak = 5
    target = make_combatant("e")
    session = make_session(actor, target)
    ctx = _loi_ctx(actor, target, session)

    after = _same_element_streak_consumer(_loi_streak_spec(), ctx)

    assert after is None  # pre-damage-only consumer, no deferred work
    assert ctx.base_dmg == 320 + 240 * 4  # 1280
    assert ctx.skill_data["base_dmg"] == ctx.base_dmg
    # Pen is folded into the actor's element_pen for the roll via the shared
    # mutate helper (the cast's ``finally`` restores it afterward).
    assert actor.element_pen.get("loi") == 0.04 * 4
    assert ctx.prev_pen == 0.0  # first-writer snapshot of the original pen


def test_loi_streak_no_bonus_at_streak_one():
    """streak=1 → tiers=0 → no base_dmg growth and no pen bump."""
    actor = make_combatant("a")
    actor.same_element_streak = 1
    target = make_combatant("e")
    session = make_session(actor, target)
    ctx = _loi_ctx(actor, target, session)

    _same_element_streak_consumer(_loi_streak_spec(), ctx)

    assert ctx.base_dmg == 320  # unchanged
    assert actor.element_pen.get("loi", 0.0) == 0.0
    assert ctx.prev_pen is None  # nothing mutated the pen


def test_loi_forced_shock_at_streak_three():
    """streak>=3 forces the DebuffSocDien chance to 1.0 on the cast's skill_data."""
    actor = make_combatant("a")
    actor.same_element_streak = 3
    target = make_combatant("e")
    session = make_session(actor, target)
    ctx = _loi_ctx(actor, target, session)

    _same_element_streak_consumer(_loi_streak_spec(), ctx)

    assert ctx.skill_data["effect_chances"]["DebuffSocDien"] == 1.0


def test_loi_no_forced_shock_below_streak_three():
    """streak<3 leaves the base 0.6 Sốc Điện chance untouched."""
    actor = make_combatant("a")
    actor.same_element_streak = 2
    target = make_combatant("e")
    session = make_session(actor, target)
    ctx = _loi_ctx(actor, target, session)

    _same_element_streak_consumer(_loi_streak_spec(), ctx)

    assert ctx.skill_data["effect_chances"]["DebuffSocDien"] == 0.6


# ════════════════════════════════════════════════════════════════════════
# 4. Lôi Tù Cấm Ngục — cast_skill streak-update rule
# ════════════════════════════════════════════════════════════════════════


def _cast(session, actor, target, skill_key):
    """Drive a single real top-level cast via ``cast_skill``."""
    data = registry.get_skill(skill_key)
    assert data is not None, f"skill {skill_key} missing from registry"
    casting.cast_skill(
        session, actor, target, skill_key, data, mp_cost=data.get("mp_cost", 0),
    )


def test_streak_increments_on_consecutive_loi_casts():
    """Two consecutive Lôi casts bump same_element_streak 1 → 2."""
    actor = make_combatant("a", mp=10_000, mp_max=10_000)
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)  # skip crit / proc rolls for a clean cast

    _cast(session, actor, target, _LOI_SKILL_KEY)
    assert actor.same_element_streak == 1
    assert actor.last_cast_element == "loi"

    _cast(session, actor, target, _LOI_SKILL_KEY)
    assert actor.same_element_streak == 2
    assert actor.last_cast_element == "loi"


def test_streak_resets_on_cross_element_cast():
    """A non-Lôi cast after a Lôi streak resets the streak back to 1."""
    actor = make_combatant("a", mp=10_000, mp_max=10_000)
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)

    _cast(session, actor, target, _LOI_SKILL_KEY)
    _cast(session, actor, target, _LOI_SKILL_KEY)
    assert actor.same_element_streak == 2

    # Cross-element cast (Kim) breaks the chain → streak resets to 1.
    _cast(session, actor, target, _KIM_SKILL_KEY)
    assert actor.last_cast_element == "kim"
    assert actor.same_element_streak == 1


# ════════════════════════════════════════════════════════════════════════
# 5. Thổ Độn Tàng Hình — burrow store-charge / discharge cycle
# ════════════════════════════════════════════════════════════════════════


def _tho_aegis_override() -> dict:
    """The ``_aegis`` override block exactly as declared on ``SkillThoDonTangHinh``."""
    return registry.get_skill(_THO_SKILL_KEY)["effect_overrides"]["BuffThoDon"]


def test_tho_burrow_buff_active_with_70pct_dr_after_cast():
    """Casting the skill installs BuffThoDon (final_dmg_reduce 0.70) on the player."""
    player = make_combatant("p", mp=10_000, mp_max=10_000)
    enemy = make_combatant("e")
    session = make_session(player, enemy)
    session.rng = SeedRng(0.99)

    data = registry.get_skill(_THO_SKILL_KEY)
    casting.cast_skill(
        session, player, enemy, _THO_SKILL_KEY, data, mp_cost=data.get("mp_cost", 0),
    )

    assert player.has_effect("BuffThoDon")
    mods = get_combat_modifiers(player)
    assert mods["final_dmg_reduce"] == 0.70
    # The aegis block travelled onto the holder's per-instance override.
    assert "_aegis" in player.effect_overrides["BuffThoDon"]


def test_tho_store_charge_banks_absorbed_damage():
    """Damage taken while burrowed banks 100% of it into _stored_charge."""
    player = make_combatant("p", matk=200)
    player.effects["BuffThoDon"] = 1
    player.effect_overrides["BuffThoDon"] = _tho_aegis_override()

    # store_charge.pct = 1.0, cap = max(cap_flat=0, cap_matk_scale 4.0 × matk 200)
    # = 800. Two hits under the cap accumulate fully.
    player.take_damage(300)
    assert player.effect_overrides["BuffThoDon"]["_stored_charge"] == 300

    player.take_damage(400)
    assert player.effect_overrides["BuffThoDon"]["_stored_charge"] == 700

    # Cap clamps further banking at cap_matk_scale × matk = 4.0 × 200 = 800.
    player.take_damage(10_000)
    assert player.effect_overrides["BuffThoDon"]["_stored_charge"] == 800


def test_tho_discharge_on_expire_strikes_enemy_scaled_by_stored():
    """On expiry the buff discharges a Thổ strike = base + matk_scale·matk +
    stored × stored_mult onto the enemy."""
    player = make_combatant("p", matk=200)
    enemy = make_combatant("e", hp=100_000, hp_max=100_000)
    session = make_session(player, enemy)

    override = _tho_aegis_override()
    override["_stored_charge"] = 800  # banked from prior burrow hits
    player.effects["BuffThoDon"] = 1
    player.effect_overrides["BuffThoDon"] = override

    # tick_effects builds this snapshot before popping the live override;
    # emit_discharge_on_expire reads exclusively from the snapshot.
    snapshot = {"BuffThoDon": dict(player.effect_overrides["BuffThoDon"])}

    hp_before = enemy.hp
    defense_aegis.emit_discharge_on_expire(
        session, player, enemy, "BuffThoDon", snapshot,
    )

    # discharge: base_dmg=400, matk_scale=0.3 → 0.3×200=60, stored_mult=0.6 →
    # 800×0.6=480. Total = 400 + 60 + 480 = 940.
    expected = int(400 + 0.3 * 200 + 800 * 0.6)
    assert enemy.hp == hp_before - expected
    assert expected == 940


def test_tho_discharge_noop_without_stored_charge_block():
    """A plain buff with no _aegis.discharge block fires nothing on expiry."""
    player = make_combatant("p", matk=200)
    enemy = make_combatant("e", hp=100_000, hp_max=100_000)
    session = make_session(player, enemy)

    snapshot = {"BuffPlain": {}}  # no _aegis block at all
    hp_before = enemy.hp

    defense_aegis.emit_discharge_on_expire(
        session, player, enemy, "BuffPlain", snapshot,
    )

    assert enemy.hp == hp_before  # untouched
