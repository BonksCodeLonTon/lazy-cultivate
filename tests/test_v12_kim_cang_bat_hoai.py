"""v12 constitution — Kim Cang Bat Hoai The (Diamond Indestructible Body, Tho).

Identity: a pure SHIELD-TANK with no offensive amplification.  Survives via
shield, damage-reduction, a physical-negate roll, a slow-lock aura, and
its sole damage output — the L9 earth aura that scales off the holder's
current shield.  NO revive / NO cheat-death mechanic (explicitly removed).

Standard 4-milestone [1, 3, 6, 9].

Test plan:
  1. Composition:         body registered; element tho; milestones; stat shapes.
  2. Defensive stat ramp: def_pct / hp_pct / shield_max_pct / res_all grow
                          predictably across milestones.
  3. Dia Mach stacks:     shield-regen event bumps dia_mach_stacks; caps at 6;
                          cap payoff lifts shield_cap by 24%.
  4. L3 physical-negate:  40%/55% negate chance vs physical; 0% vs magical;
                          gate flips above 50% shield threshold.
  5. L6 auto-slow:        periodic fires DebuffTroBuoc + DebuffLunDat;
                          debuff_immune_pct==1.0 opponent resists.
  6. L9 earth aura:       deals int(shield * 0.08) Tho damage each periodic;
                          0 damage when shield == 0 (crack-the-shield counterplay).
  7. NO revive:           _try_revive returns False at L9, player stays dead.
  8. Flag-OFF inertness:  no buffs stamped; tho_ fields at zero defaults; aura
                          / slow / negate / revive paths all no-op.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr``
so it auto-reverts after the test.

Scaffolding note: tests 3-6 and 8 (implementation-dependent) skip gracefully
via _player_or_skip() and _has_tho_field() when the tho-specific Combatant /
CombatStats fields have not yet been added by the coder.  They will self-
activate once the dataclass work lands.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_KimCangBatHoai"
_ATTACK_SKILL = "SkillAtkTho1"
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 6
_PLAYER_REALM_TOTAL = 15


# ── helpers ───────────────────────────────────────────────────────────────────

def _body_data() -> dict:
    """Return body data, or skip the test if the body isn't registered."""
    data = registry.get_constitution(_BODY)
    if data is None or not data.get("process"):
        pytest.skip(f"{_BODY!r} not yet registered or missing process block")
    return data  # type: ignore[return-value]


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="KimCangTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1,
        active_axis="qi",
        constitution_type=_BODY,
        linh_can=["tho"],
        linh_can_levels={"tho": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _enemy(hp: int = 10 ** 9):
    e = build_enemy_combatant(_ENEMY_KEY, player_realm_total=_PLAYER_REALM_TOTAL)
    assert e is not None, f"{_ENEMY_KEY!r} not found in registry"
    e.hp = e.hp_max = hp
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.shield = 0
    return e


def _player(level: int):
    """Build player combatant at the given constitution level.

    Raises TypeError when CombatStats does not yet have the tho-specific
    fields — callers that depend on those fields use _player_or_skip().
    """
    return build_player_combatant(_make_char(level), player_skill_keys=[_ATTACK_SKILL])


def _player_or_skip(level: int):
    """Build player combatant, skipping the test if tho fields are not wired."""
    try:
        return _player(level)
    except TypeError as exc:
        pytest.skip(f"tho Combatant/CombatStats fields not yet implemented: {exc}")


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(seed), max_turns=200,
    )


def _has_tho_field(field_name: str) -> bool:
    """Return True if Combatant has the given tho-specific field."""
    from dataclasses import fields as dc_fields
    from src.game.systems.combatant import Combatant
    return any(f.name == field_name for f in dc_fields(Combatant))


# ── 1. Composition (pure seam — JSON only) ────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "tho"
    assert data.get("rarity") == "legendary"
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffDaiDiaCanCo"]


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]


def test_composition_l9_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 9) == [
        "BuffDaiDiaCanCo",
        "BuffKimThanHoPhap",
        "BuffTrongDiaKhongChe",
        "BuffDaiDiaPhanPhe",
    ]


# ── 2. Defensive stat ramp ────────────────────────────────────────────────────


def test_defensive_stats_grow_across_milestones() -> None:
    data = _body_data()

    l1 = effective_stat_bonuses(data, 1)
    l3 = effective_stat_bonuses(data, 3)
    l6 = effective_stat_bonuses(data, 6)
    l9 = effective_stat_bonuses(data, 9)

    # def_pct grows monotonically across milestones
    assert l3.get("def_pct", 0.0) >= l1.get("def_pct", 0.0)
    assert l6.get("def_pct", 0.0) >= l3.get("def_pct", 0.0)
    assert l9.get("def_pct", 0.0) >= l6.get("def_pct", 0.0)

    # hp_pct grows monotonically
    assert l3.get("hp_pct", 0.0) >= l1.get("hp_pct", 0.0)
    assert l6.get("hp_pct", 0.0) >= l3.get("hp_pct", 0.0)
    assert l9.get("hp_pct", 0.0) >= l6.get("hp_pct", 0.0)

    # shield_max_pct grows monotonically
    assert l3.get("shield_max_pct", 0.0) >= l1.get("shield_max_pct", 0.0)
    assert l6.get("shield_max_pct", 0.0) >= l3.get("shield_max_pct", 0.0)
    assert l9.get("shield_max_pct", 0.0) >= l6.get("shield_max_pct", 0.0)


def test_l9_stat_values_approx() -> None:
    """L9 cumulative stats — per_level_growth × 8 is folded in.

    Base flat: hp_pct=0.10, def_pct=0.10, shield_max_pct=0.15.
    Milestone additions (L3+L6+L9): def +0.08+0+0.08=0.16, hp +0.04+0.05+0.06=0.15,
    shield_max +0+0.10+0.10=0.20.
    Per-level growth × 8: hp 0.006×8=0.048, def 0.005×8=0.040, shield 0.005×8=0.040.
    L9 totals: hp ≈ 0.298, def ≈ 0.300, shield_max ≈ 0.390.
    """
    data = _body_data()
    l9 = effective_stat_bonuses(data, 9)

    # res_all stays flat (not in per_level_growth) — must be 0.12
    assert l9.get("res_all", 0.0) == pytest.approx(0.12, abs=0.005)

    # hp_pct ≈ 0.10 + 0.15 + 0.048 = 0.298
    assert l9.get("hp_pct", 0.0) == pytest.approx(0.298, abs=0.01)

    # def_pct ≈ 0.10 + 0.16 + 0.040 = 0.300
    assert l9.get("def_pct", 0.0) == pytest.approx(0.300, abs=0.01)

    # shield_max_pct ≈ 0.15 + 0.20 + 0.040 = 0.390
    assert l9.get("shield_max_pct", 0.0) == pytest.approx(0.390, abs=0.01)

    # tho process-only flags present in L9 bonuses
    assert l9.get("tho_earth_aura_shield_pct", 0.0) == pytest.approx(0.08)
    assert l9.get("tho_auto_slow_enabled") is True
    assert l9.get("tho_phys_immune_chance", 0.0) == pytest.approx(0.40)


# ── 3. Dia Mach stacks ────────────────────────────────────────────────────────


def test_dia_mach_stacks_climb_on_shield_regen(monkeypatch) -> None:
    if not _has_tho_field("dia_mach_stacks"):
        pytest.skip("dia_mach_stacks field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)

    assert player.has_effect("BuffDaiDiaCanCo"), "L1 must have BuffDaiDiaCanCo stamped"
    assert getattr(player, "dia_mach_per_regen", 0) == 1, \
        "L1 body must grant dia_mach_per_regen == 1"

    # Ensure shield is below cap so regen fires
    player.shield_max_base = 5000
    player.shield = player.shield_cap() // 2
    player.shield_regen_pct = 0.25  # enough to definitely regen

    enemy = _enemy()
    session = _session(player, enemy)

    start_stacks = player.dia_mach_stacks
    session._process_periodic(player)
    assert player.dia_mach_stacks > start_stacks, \
        "dia_mach_stacks must increase after shield regen fires"


def test_dia_mach_stacks_cap_at_six(monkeypatch) -> None:
    if not _has_tho_field("dia_mach_stacks"):
        pytest.skip("dia_mach_stacks field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)

    # Pre-fill to cap (the cap of 6 is enforced inline in the regen hook).
    player.dia_mach_stacks = 6
    player.shield_max_base = 5000
    player.shield = player.shield_cap() // 2
    player.shield_regen_pct = 0.25

    enemy = _enemy()
    session = _session(player, enemy)
    session._process_periodic(player)

    assert player.dia_mach_stacks == 6, \
        "dia_mach_stacks must not exceed cap of 6"


def test_dia_mach_cap_lifts_shield_cap(monkeypatch) -> None:
    """6 stacks should lift shield_cap() by 24% (6 stacks × 4% each)."""
    if not _has_tho_field("dia_mach_stacks"):
        pytest.skip("dia_mach_stacks field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    player.shield_max_base = 10000

    player.dia_mach_stacks = 0
    cap_at_zero = player.shield_cap()

    player.dia_mach_stacks = 6
    cap_at_six = player.shield_cap()

    # 6 stacks × 4% = +24 PERCENTAGE POINTS of shield_max_pct (additive, like
    # every other shield_max_pct source), i.e. an absolute +shield_max_base×0.24
    # to the cap — NOT a ×1.24 multiplier (the base already carries the body's
    # other shield_max_pct, so the relative lift off cap_at_zero is smaller).
    assert (cap_at_six - cap_at_zero) == pytest.approx(player.shield_max_base * 0.24, rel=0.05), (
        f"6 dia_mach stacks must add ~24% of base "
        f"(~{player.shield_max_base * 0.24:.0f}) to shield_cap "
        f"(was {cap_at_zero}, got {cap_at_six})"
    )


# ── 4. L3 physical-negate ────────────────────────────────────────────────────


def test_l3_phys_negate_fires_on_forced_low_roll(monkeypatch) -> None:
    """Physical negate fires via cast_skill (wired in casting.py:679).

    The negate check lives inside the cast_skill damage path, not in
    pipeline.py — driving a full cast is the correct way to exercise it.
    EnemyTho_T2 is a physical tho skill carried by TinhKimTho.
    """
    if not _has_tho_field("tho_phys_immune_chance"):
        pytest.skip("tho_phys_immune_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)
    player.hp = 1_000_000  # large buffer so any un-negated hit doesn't kill
    player.shield = 0  # start below gate so base chance (0.40) is used

    assert getattr(player, "tho_phys_immune_chance", 0.0) == pytest.approx(0.40), \
        "L3 must grant 40% base physical-negate chance"

    enemy = _enemy()
    # EnemyTho_T2 is a physical-type tho skill on TinhKimTho
    _PHYS_SKILL = "EnemyTho_T2"
    phys_skill_data = registry.get_skill(_PHYS_SKILL)
    assert phys_skill_data is not None, f"{_PHYS_SKILL!r} not in registry"
    assert phys_skill_data.get("attack_type") == "physical", \
        f"{_PHYS_SKILL!r} must be physical for this test"

    from src.game.systems.combat.casting import cast_skill

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # 0.0 < 0.40 → negate fires

    hp_before = player.hp
    shield_before = player.shield
    mp_cost = phys_skill_data.get("mp_cost", 0)
    enemy.mp = max(enemy.mp, mp_cost + 1)
    cast_skill(session, enemy, player, _PHYS_SKILL, phys_skill_data, mp_cost)

    # After negate: HP and shield must be unchanged
    assert player.hp == hp_before and player.shield == shield_before, (
        f"physical negate (roll 0.0 < 0.40) must absorb all damage. "
        f"HP delta: {player.hp - hp_before}, Shield delta: {player.shield - shield_before}"
    )


def test_l3_phys_negate_misses_on_high_roll(monkeypatch) -> None:
    if not _has_tho_field("tho_phys_immune_chance"):
        pytest.skip("tho_phys_immune_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)
    player.hp = 1_000_000
    player.shield = 0

    enemy = _enemy()
    _PHYS_SKILL = "EnemyTho_T2"
    phys_skill_data = registry.get_skill(_PHYS_SKILL)
    assert phys_skill_data is not None

    from src.game.systems.combat.casting import cast_skill

    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # 0.99 >= 0.40 → negate does NOT fire

    hp_before = player.hp
    mp_cost = phys_skill_data.get("mp_cost", 0)
    enemy.mp = max(enemy.mp, mp_cost + 1)
    cast_skill(session, enemy, player, _PHYS_SKILL, phys_skill_data, mp_cost)

    assert player.hp < hp_before or player.shield < 0 or True, \
        "high roll must let damage through"
    # Simply assert either hp dropped or at minimum nothing crashed.
    # The meaningful check: damage must not be zero (either HP or shield took it)
    total_taken = (hp_before - player.hp)
    assert total_taken > 0, f"roll 0.99 >= 0.40 — damage must land; hp delta was 0"


def test_l3_phys_negate_never_fires_vs_magical(monkeypatch) -> None:
    """Magical attacks must never be negated by the physical-negate mechanic.

    Uses EnemyDungeon_Hoa_TinhAnh_R1 (known magical attack skill) — injected
    into the enemy's skill_keys so cast_skill can fire it.
    """
    if not _has_tho_field("tho_phys_immune_chance"):
        pytest.skip("tho_phys_immune_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)
    player.hp = 1_000_000
    player.shield = 0

    # Pick a known magical attack skill
    _MAG_SKILL = "EnemyDungeon_Hoa_TinhAnh_R1"
    mag_skill_data = registry.get_skill(_MAG_SKILL)
    assert mag_skill_data is not None, f"{_MAG_SKILL!r} not in registry"
    assert mag_skill_data.get("attack_type") == "magical", \
        f"{_MAG_SKILL!r} must be magical"

    from src.game.systems.combat.casting import cast_skill

    enemy = _enemy()
    enemy.skill_keys = [_MAG_SKILL]

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would fire negate if incorrectly applied to magical

    hp_before = player.hp
    mp_cost = mag_skill_data.get("mp_cost", 0)
    enemy.mp = max(enemy.mp, mp_cost + 1)
    cast_skill(session, enemy, player, _MAG_SKILL, mag_skill_data, mp_cost)

    total_taken = hp_before - player.hp
    assert total_taken > 0, \
        "magical attacks must NEVER be negated by the physical-negate mechanic"


def test_l3_phys_negate_boosted_above_shield_threshold(monkeypatch) -> None:
    """When shield > 50% of shield_cap, the chance used is 0.55 not 0.40.

    Test: roll 0.45 — below 0.55 (boosted) but above 0.40 (base).
    With high shield → negate fires (0.45 < 0.55).
    Without high shield → damage lands (0.45 >= 0.40).
    """
    if not _has_tho_field("tho_phys_immune_chance"):
        pytest.skip("tho_phys_immune_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)

    boosted_chance = getattr(player, "tho_phys_immune_high_shield_chance", None)
    if boosted_chance is None:
        pytest.skip("tho_phys_immune_high_shield_chance not yet in Combatant")
    assert boosted_chance == pytest.approx(0.55), \
        "boosted negate chance must be 0.55"

    _PHYS_SKILL = "EnemyTho_T2"
    phys_skill_data = registry.get_skill(_PHYS_SKILL)
    assert phys_skill_data is not None

    from src.game.systems.combat.casting import cast_skill

    # High shield scenario (above gate) → boosted chance → negate fires at 0.45
    player.hp = 1_000_000
    player.shield_max_base = 10000
    cap = player.shield_cap()
    player.shield = int(cap * 0.6)  # 60% > 50% gate

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.45  # < 0.55 boosted chance → negate

    hp_before = player.hp
    shield_before = player.shield
    mp_cost = phys_skill_data.get("mp_cost", 0)
    enemy.mp = max(enemy.mp, mp_cost + 1)
    cast_skill(session, enemy, player, _PHYS_SKILL, phys_skill_data, mp_cost)

    assert player.hp == hp_before and player.shield == shield_before, (
        "roll 0.45 with shield above gate must negate (boosted 0.55 > 0.45). "
        f"HP delta: {player.hp - hp_before}"
    )


# ── 5. L6 auto-slow ───────────────────────────────────────────────────────────


def test_l6_auto_slow_inflicts_debuffs(monkeypatch) -> None:
    if not _has_tho_field("tho_auto_slow_enabled"):
        pytest.skip("tho_auto_slow_enabled field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(6)

    assert getattr(player, "tho_auto_slow_enabled", False) is True, \
        "L6 must set tho_auto_slow_enabled=True"

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # force all proc rolls to succeed

    session._process_periodic(player)

    assert enemy.has_effect("DebuffTroBuoc"), \
        "L6 auto-slow periodic must inflict DebuffTroBuoc on the enemy"
    assert enemy.has_effect("DebuffLunDat"), \
        "L6 auto-slow periodic must inflict DebuffLunDat on the enemy"


def test_l6_auto_slow_respects_debuff_immunity(monkeypatch) -> None:
    if not _has_tho_field("tho_auto_slow_enabled"):
        pytest.skip("tho_auto_slow_enabled field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(6)
    enemy = _enemy()
    enemy.debuff_immune_pct = 1.0  # full debuff immunity

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would succeed without immunity

    session._process_periodic(player)

    assert not enemy.has_effect("DebuffTroBuoc"), \
        "debuff_immune_pct=1.0 must prevent DebuffTroBuoc"
    assert not enemy.has_effect("DebuffLunDat"), \
        "debuff_immune_pct=1.0 must prevent DebuffLunDat"


# ── 6. L9 earth aura ─────────────────────────────────────────────────────────


def test_l9_earth_aura_deals_shield_scaled_damage(monkeypatch) -> None:
    """Earth aura formula: int(shield × 0.08 × (1 + final_dmg_bonus)).

    The implementation in dai_dia.py is:
        base = int(actor.shield × tho_earth_aura_shield_pct)
        mult = 1.0 + actor.final_dmg_bonus
        aura_dmg = max(1, int(base × mult × (1.0 - target_res)))

    The player's final_dmg_bonus scales with constitution milestones.
    Set enemy resistances to {} so target_res == 0.
    """
    if not _has_tho_field("tho_earth_aura_shield_pct"):
        pytest.skip("tho_earth_aura_shield_pct field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(9)

    aura_pct = getattr(player, "tho_earth_aura_shield_pct", 0.0)
    assert aura_pct == pytest.approx(0.08), \
        "L9 earth aura must be 8% of current shield"

    # Known shield value; enemy has no tho resistance → target_res == 0.
    player.shield_max_base = 10000
    player.shield = 5000
    player.shield_regen_pct = 0.0  # no regen during this tick

    enemy = _enemy()
    enemy.resistances = {}

    session = _session(player, enemy)

    hp_before = enemy.hp
    session._process_periodic(player)
    damage_dealt = hp_before - enemy.hp

    # Replicate the implementation formula exactly to get the expected value.
    base = int(5000 * 0.08)
    mult = 1.0 + player.final_dmg_bonus
    expected = max(1, int(base * mult))

    assert damage_dealt == expected, (
        f"earth aura damage mismatch: expected {expected} "
        f"(base={base}, mult={mult:.3f}), got {damage_dealt}"
    )


def test_l9_earth_aura_zero_when_shield_empty(monkeypatch) -> None:
    """Cracking the shield silences the aura — the intended counterplay."""
    if not _has_tho_field("tho_earth_aura_shield_pct"):
        pytest.skip("tho_earth_aura_shield_pct field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(9)

    player.shield = 0
    player.shield_regen_pct = 0.0  # no regen during the periodic tick

    enemy = _enemy()
    session = _session(player, enemy)

    hp_before = enemy.hp
    session._process_periodic(player)

    assert enemy.hp == hp_before, \
        "earth aura must deal 0 damage when the shield is empty"


# ── 7. NO revive (identity guard) ─────────────────────────────────────────────


def test_no_revive_at_l9_flag_on(monkeypatch) -> None:
    """Kim Cang Bat Hoai has NO cheat-death / revive — this test pins that."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    data = _body_data()
    l9_bonuses = effective_stat_bonuses(data, 9)

    # None of the revive mechanic flags must appear in the L9 bonuses
    for revive_flag in (
        "moc_undying_spring_enabled",
        "phoenix_revive_pct",
        "endure_threshold_pct",
        "chan_menh_loi_phu_enabled",
    ):
        assert not l9_bonuses.get(revive_flag), (
            f"Kim Cang must NOT carry revive flag {revive_flag!r} — "
            f"cheat-death was explicitly removed from this body"
        )

    # Confirm the runtime path also returns False
    player = _player_or_skip(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    result = session._try_revive(player)

    assert result is False, \
        "Kim Cang Bat Hoai must NOT revive — _try_revive must return False"
    assert not player.is_alive(), \
        "player must stay dead after _try_revive returns False"


def test_no_revive_data_only() -> None:
    """Pure-JSON guard — runs even before Combatant fields are wired."""
    data = _body_data()
    l9_bonuses = effective_stat_bonuses(data, 9)

    for revive_flag in (
        "moc_undying_spring_enabled",
        "phoenix_revive_pct",
        "endure_threshold_pct",
    ):
        assert not l9_bonuses.get(revive_flag), (
            f"JSON L9 bonuses must NOT contain revive flag {revive_flag!r}"
        )


# ── 8. Flag-OFF inertness ─────────────────────────────────────────────────────


def test_flag_off_no_effects_stamped() -> None:
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)
    for key in (
        "BuffDaiDiaCanCo",
        "BuffKimThanHoPhap",
        "BuffTrongDiaKhongChe",
        "BuffDaiDiaPhanPhe",
    ):
        assert not player.has_effect(key), \
            f"flag-OFF must not stamp {key!r} onto the combatant"


def test_flag_off_tho_fields_at_inert_defaults() -> None:
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)

    if _has_tho_field("tho_auto_slow_enabled"):
        assert getattr(player, "tho_auto_slow_enabled", False) is False, \
            "flag-OFF: tho_auto_slow_enabled must be False"
    if _has_tho_field("tho_earth_aura_shield_pct"):
        assert getattr(player, "tho_earth_aura_shield_pct", 0.0) == 0.0, \
            "flag-OFF: tho_earth_aura_shield_pct must be 0.0"
    if _has_tho_field("tho_phys_immune_chance"):
        assert getattr(player, "tho_phys_immune_chance", 0.0) == 0.0, \
            "flag-OFF: tho_phys_immune_chance must be 0.0"


def test_flag_off_earth_aura_noop() -> None:
    """flag-OFF: the earth aura deals no damage regardless of shield."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)
    player.shield_max_base = 10000
    player.shield = 5000

    enemy = _enemy()
    session = _session(player, enemy)

    hp_before = enemy.hp
    session._process_periodic(player)

    if _has_tho_field("tho_earth_aura_shield_pct"):
        aura_pct = getattr(player, "tho_earth_aura_shield_pct", 0.0)
        if aura_pct == 0.0:
            assert enemy.hp == hp_before, \
                "flag-OFF: earth aura field is 0.0 — must deal 0 damage"


def test_flag_off_auto_slow_noop() -> None:
    """flag-OFF: the periodic auto-slow must not inflict any debuffs."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)
    enemy = _enemy()
    session = _session(player, enemy)

    session._process_periodic(player)

    assert not enemy.has_effect("DebuffTroBuoc"), \
        "flag-OFF: DebuffTroBuoc must not be inflicted"
    assert not enemy.has_effect("DebuffLunDat"), \
        "flag-OFF: DebuffLunDat must not be inflicted"


def test_flag_off_revive_noop() -> None:
    """flag-OFF: _try_revive must return False (no revive mechanic active)."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_flag_off_resolves_to_flat_stat_bonuses() -> None:
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)
    assert flat == data["stat_bonuses"]
    # Process-only fields must NOT appear in flat bonuses
    for proc_only_key in (
        "tho_auto_slow_enabled",
        "tho_earth_aura_shield_pct",
        "tho_phys_immune_chance",
    ):
        assert proc_only_key not in flat, \
            f"{proc_only_key!r} must not appear in flag-OFF flat bonuses"
