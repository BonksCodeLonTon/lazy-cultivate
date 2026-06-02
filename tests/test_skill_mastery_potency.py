"""Phase 3b — Skill Mastery effect-potency scaling (Strategy B).

These tests lock the *potency scaling* behaviour added in Phase 3b: a
mastered PLAYER cast stamps ``_mastery_mult`` (= ``power_mult(level)``)
onto every effect override it applies, and each magnitude read site
multiplies the allowlisted contributions by that stamp. POWER_MULT[20]
= 1.62 is the source of truth for the level-20 multiplier.

Layering strategy (per the brief — prefer the LOWEST-level call that
exercises each behaviour deterministically):

  * Read sites (get_combat_modifiers / get_periodic_damage / aegis hooks)
    are driven directly by marking an effect active and stamping
    ``_mastery_mult`` onto its override — no full cast needed.
  * The STAMP path (cast threads mastery_mult into apply_*_skill) is
    driven by a real player ``cast_skill`` of a real registry skill.

Flag hygiene: ``skill_mastery_enabled`` is flipped ON only via
``monkeypatch.setattr`` so it auto-reverts after each test. The flag is
NEVER left set.
"""
from __future__ import annotations

import copy

import pytest

from src.data.registry import registry
from src.game.constants.skill_mastery import POWER_MULT
from src.game.engine.effects import get_combat_modifiers, get_periodic_damage
from src.game.systems.combat.casting import cast_skill
from src.utils.config import settings

from tests.conftest import SeedRng, make_combatant, make_session

# POWER_MULT[20] is the locked level-20 multiplier. Pinned as the single
# source of truth so a future table edit makes the intent (and the math)
# obvious rather than silently shifting every assertion.
_MULT_L20 = POWER_MULT[20]
assert _MULT_L20 == 1.62  # guard: the brief's expected value

# An active-effect buff whose meta stat_bonus carries TWO allowlisted
# magnitudes (final_dmg_bonus + crit_rating). Lets the read-site tests
# observe scaling without a full cast.
_BUFF_KIEM_KHI = "BuffKiemKhi"  # stat_bonus: {final_dmg_bonus: 0.15, crit_rating: 150}
_KIEM_KHI_FINAL_DMG = 0.15
_KIEM_KHI_CRIT_RATING = 150

# Classic %HP DoT (dot_pct > 0) — mastery scales this lane.
_DOT_KEY = "DebuffDotChay"  # dot_pct: 0.08, dot_element: hoa

# Real registry skills used for the STAMP-path tests.
_HEAL_SKILL = "SkillDefPhatQuangPhoChieu_R7"   # base_dmg=0, HpRegen override instant_heal_pct=0.3
_AEGIS_SKILL = "SkillDefLoiThanKhai"           # base_dmg=0, BuffLoiThanKhai _aegis.shield_grant
_AEGIS_BUFF = "BuffLoiThanKhai"


def _mark_active(combatant, key: str, override: dict, duration: int = 5) -> None:
    """Mark ``key`` active on ``combatant`` and stamp ``override`` directly.

    Bypasses ``apply_effect`` (and its magnitude-merge) so the test owns
    the exact override contents — including the top-level ``_mastery_mult``
    the read sites look for.
    """
    combatant.effects[key] = duration
    combatant.effect_overrides[key] = override


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_combat_modifiers scales an allowlisted magnitude
# ─────────────────────────────────────────────────────────────────────────────


def test_get_combat_modifiers_scales_allowlisted_stat():
    """With ``_mastery_mult=1.62`` stamped, the aggregated allowlisted
    ``final_dmg_bonus`` is exactly 1.62× the unscaled value.
    """
    scaled = make_combatant("s")
    _mark_active(scaled, _BUFF_KIEM_KHI, {"_mastery_mult": _MULT_L20})

    unscaled = make_combatant("u")
    # No _mastery_mult key at all → the 1.0 early-out path.
    unscaled.effects[_BUFF_KIEM_KHI] = 5

    mods_scaled = get_combat_modifiers(scaled)
    mods_unscaled = get_combat_modifiers(unscaled)

    assert mods_unscaled["final_dmg_bonus"] == _KIEM_KHI_FINAL_DMG
    assert mods_scaled["final_dmg_bonus"] == pytest.approx(
        _KIEM_KHI_FINAL_DMG * _MULT_L20
    )
    # And the scaled value is exactly 1.62× the unscaled aggregate.
    assert mods_scaled["final_dmg_bonus"] == pytest.approx(
        mods_unscaled["final_dmg_bonus"] * _MULT_L20
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. allowlist boundary — allowlisted key scaled, non-allowlisted key untouched
# ─────────────────────────────────────────────────────────────────────────────


def test_allowlist_boundary_nonmagnitude_unscaled():
    """Under the SAME mult, an allowlisted key (crit_rating) is scaled but a
    non-allowlisted key (life_steal_pct, in the override only) is NOT.
    """
    c = make_combatant("c")
    # crit_rating comes from BuffKiemKhi's META (allowlisted → scaled).
    # life_steal_pct is injected via the per-instance override and is NOT in
    # the allowlist → it must pass through unscaled.
    _mark_active(
        c,
        _BUFF_KIEM_KHI,
        {
            "_mastery_mult": _MULT_L20,
            "stat_bonus": {"life_steal_pct": 0.20},
        },
    )

    mods = get_combat_modifiers(c)

    # Allowlisted META key IS scaled: 150 × 1.62 = 243.0
    assert mods["crit_rating"] == pytest.approx(_KIEM_KHI_CRIT_RATING * _MULT_L20)
    # Non-allowlisted override key is left at its raw value.
    assert mods["life_steal_pct"] == pytest.approx(0.20)


# ─────────────────────────────────────────────────────────────────────────────
# 3. _mastery_mult == 1.0 (and absent) is byte-identical to no mastery
# ─────────────────────────────────────────────────────────────────────────────


def test_mastery_mult_one_is_identical():
    """``_mastery_mult=1.0`` and a missing key both reproduce the plain
    aggregate — proves the early-out leaves the byte-identical path.
    """
    absent = make_combatant("absent")
    absent.effects[_BUFF_KIEM_KHI] = 5  # no override at all

    explicit_one = make_combatant("one")
    _mark_active(explicit_one, _BUFF_KIEM_KHI, {"_mastery_mult": 1.0})

    mods_absent = get_combat_modifiers(absent)
    mods_one = get_combat_modifiers(explicit_one)

    assert mods_absent == mods_one
    # And both equal the raw meta magnitudes (no scaling whatsoever).
    assert mods_absent["final_dmg_bonus"] == _KIEM_KHI_FINAL_DMG
    assert mods_absent["crit_rating"] == _KIEM_KHI_CRIT_RATING


# ─────────────────────────────────────────────────────────────────────────────
# 4. classic dot_pct DoT scales 1.62× under the mult
# ─────────────────────────────────────────────────────────────────────────────


def test_classic_dot_scales():
    """A classic ``dot_pct`` DoT with ``_mastery_mult=1.62`` ticks 1.62× the
    unscaled tick (driven straight through ``get_periodic_damage``).
    """
    # SeedRng(0.99) skips the 25% DoT crit roll regardless of dot_can_crit.
    rng = SeedRng(0.99)

    scaled = make_combatant("s")  # hp_max 10_000, no dot_bonus_sources → hp_max fallback path
    _mark_active(scaled, _DOT_KEY, {"_mastery_mult": _MULT_L20})

    unscaled = make_combatant("u")
    unscaled.effects[_DOT_KEY] = 5  # no mult → 1.0 early-out

    ticks_scaled = get_periodic_damage(scaled, rng)
    ticks_unscaled = get_periodic_damage(unscaled, rng)

    # Exactly one DoT effect active on each.
    dmg_scaled = next(d for k, d, _ in ticks_scaled if k == _DOT_KEY)
    dmg_unscaled = next(d for k, d, _ in ticks_unscaled if k == _DOT_KEY)

    assert dmg_unscaled > 0
    # int truncation is benign here: 10_000×0.08×0.5 = 400, ×1.62 = 648.
    assert dmg_scaled == pytest.approx(dmg_unscaled * _MULT_L20, rel=0.02)


# ─────────────────────────────────────────────────────────────────────────────
# 5. STAMP path — support heal scales via a real player cast
# ─────────────────────────────────────────────────────────────────────────────


def _cast_heal(monkeypatch, *, level: int | None, flag: bool) -> int:
    """Cast the heal skill as a player and return HP restored.

    ``level`` None → empty skill_mastery; otherwise the heal skill is
    mastered at ``level``. ``flag`` toggles ``skill_mastery_enabled``.
    """
    if flag:
        monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    actor = make_combatant("player", hp=1, hp_max=20_000, matk=100)
    enemy = make_combatant("enemy")
    if level is not None:
        actor.skill_mastery[_HEAL_SKILL] = level
    session = make_session(actor, enemy)
    skill = registry.get_skill(_HEAL_SKILL)
    hp_before = actor.hp
    cast_skill(session, actor, enemy, _HEAL_SKILL, skill, mp_cost=skill["mp_cost"])
    return actor.hp - hp_before


def test_support_heal_scales_via_cast(monkeypatch):
    """A lvl-20 mastered heal restores ~1.62× the lvl-1 heal.

    The skill's accompanying buff carries ``heal_taken_bonus`` (NOT
    ``hp_regen_pct``), which the heal pulse does not read — so the pulse
    scales cleanly off ``mastery_mult`` alone.
    """
    healed_l1 = _cast_heal(monkeypatch, level=1, flag=True)
    healed_l20 = _cast_heal(monkeypatch, level=20, flag=True)

    assert healed_l1 > 0
    assert healed_l20 == pytest.approx(healed_l1 * _MULT_L20, rel=0.02)


# ─────────────────────────────────────────────────────────────────────────────
# 6. STAMP path — aegis shield grant scales via a real player cast
# ─────────────────────────────────────────────────────────────────────────────


def _cast_aegis_shield(monkeypatch, *, level: int) -> int:
    """Cast the aegis shield skill as a player at ``level`` and return the
    shield granted. Flag ON. A huge shield cap keeps the grant unclipped.
    """
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    actor = make_combatant("player", matk=200, shield_max_base=1_000_000)
    enemy = make_combatant("enemy")
    actor.skill_mastery[_AEGIS_SKILL] = level
    session = make_session(actor, enemy)
    skill = registry.get_skill(_AEGIS_SKILL)
    cast_skill(session, actor, enemy, _AEGIS_SKILL, skill, mp_cost=skill["mp_cost"])
    return actor.shield


def test_aegis_shield_scales(monkeypatch):
    """A lvl-20 mastered aegis cast grants ~1.62× the lvl-1 shield.

    shield_grant = 500 + 4.0×200 = 1300 (lvl1, mult 1.0); ×1.62 = 2106 (lvl20).
    """
    shield_l1 = _cast_aegis_shield(monkeypatch, level=1)
    shield_l20 = _cast_aegis_shield(monkeypatch, level=20)

    assert shield_l1 == 1300  # 500 + 4.0 × 200, mult 1.0 early-out
    assert shield_l20 == pytest.approx(shield_l1 * _MULT_L20, rel=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 7. enemy cast is inert even with the flag ON
# ─────────────────────────────────────────────────────────────────────────────


def test_enemy_cast_inert_with_flag_on(monkeypatch):
    """Flag ON, an enemy (key != "player", empty skill_mastery) casting the
    aegis skill stamps ``_mastery_mult=1.0`` → no potency change.
    """
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    enemy = make_combatant("enemy", matk=200, shield_max_base=1_000_000)
    player = make_combatant("player")
    session = make_session(player, enemy)
    skill = registry.get_skill(_AEGIS_SKILL)

    cast_skill(session, enemy, player, _AEGIS_SKILL, skill, mp_cost=skill["mp_cost"])

    # Shield matches the UNSCALED grant (mult stayed 1.0).
    assert enemy.shield == 1300
    # The stamped mult on the buff override is exactly 1.0.
    assert enemy.effect_overrides[_AEGIS_BUFF]["_mastery_mult"] == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 8. player with empty skill_mastery + flag ON behaves like flag OFF
# ─────────────────────────────────────────────────────────────────────────────


def test_flag_on_empty_mastery_is_inert(monkeypatch):
    """Flag ON, PLAYER with empty skill_mastery → identical to flag off:
    the heal restores the same amount and the buff stamp is 1.0.
    """
    # Flag OFF reference (no monkeypatch → default False).
    healed_off = _cast_heal(monkeypatch, level=None, flag=False)

    # Flag ON but skill not mastered.
    healed_on_empty = _cast_heal(monkeypatch, level=None, flag=True)

    assert healed_off > 0
    assert healed_on_empty == healed_off

    # Verify the stamp itself is 1.0 (no elevated mult leaked).
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    actor = make_combatant("player", hp=1, hp_max=20_000, matk=100)
    enemy = make_combatant("enemy")
    session = make_session(actor, enemy)
    skill = registry.get_skill(_HEAL_SKILL)
    cast_skill(session, actor, enemy, _HEAL_SKILL, skill, mp_cost=skill["mp_cost"])
    # The buff applied by the heal skill carries a 1.0 stamp.
    assert actor.effect_overrides["BuffPhatQuangPhoChieu"]["_mastery_mult"] == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 9. copy-on-write — the shared registry skill dict is never mutated
# ─────────────────────────────────────────────────────────────────────────────


def test_copy_on_write_registry_unmutated(monkeypatch):
    """A mastered player cast must NOT leak ``_mastery_mult`` into the shared
    registry ``effect_overrides`` (or the ``_aegis`` block) of the skill.
    """
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    skill = registry.get_skill(_AEGIS_SKILL)
    # Deep snapshot of the shared override block BEFORE the cast.
    before = copy.deepcopy(skill["effect_overrides"])

    actor = make_combatant("player", matk=200, shield_max_base=1_000_000)
    enemy = make_combatant("enemy")
    actor.skill_mastery[_AEGIS_SKILL] = 20
    session = make_session(actor, enemy)
    cast_skill(session, actor, enemy, _AEGIS_SKILL, skill, mp_cost=skill["mp_cost"])

    # The shared registry override is byte-identical to the pre-cast snapshot.
    assert skill["effect_overrides"] == before
    # No _mastery_mult anywhere in the shared override or its nested _aegis.
    assert "_mastery_mult" not in skill["effect_overrides"][_AEGIS_BUFF]
    assert "_mastery_mult" not in skill["effect_overrides"][_AEGIS_BUFF]["_aegis"]
    # Sanity: the cast actually scaled (so the COW guard is meaningful, not
    # vacuously true because nothing happened).
    assert actor.effect_overrides[_AEGIS_BUFF]["_mastery_mult"] == _MULT_L20
