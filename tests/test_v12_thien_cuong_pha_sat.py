"""v12 constitution — Thiên Cương Phá Sát Thể (Heaven Steel Killing Body, Kim).

The first body added back to the cleared v12 roster. These tests pin every
mechanic on the flag-ON Constitution-Process engine and the flag-OFF inert
seam (Phase-0 byte-identity is guarded separately by
``test_constitution_process_guard.py`` — here we only assert this body resolves
to its flat stat_bonuses and stamps no effects / fires no procs while dormant).

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr``
so it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import get_combat_modifiers
from src.game.engine.rating import crit_chance
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combat.procs import run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_ThienCuongPhaSat"
# A registered low-cost attack skill so the on-hit sweep runs against the full
# pipeline; not the splash skill (that's auto-fired by the L9 proc).
_ATTACK_SKILL = "SkillAtkKim3"
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 5
_PLAYER_REALM_TOTAL = 15


def _make_char(level: int | None = None) -> Character:
    """A fixed Kim-body Character carrying the killing body at ``level``."""
    return Character(
        player_id=1,
        discord_id=1,
        name="KimTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=5, qi_level=1,
        formation_realm=5, formation_level=1,
        active_axis="body",
        constitution_type=_BODY,
        linh_can=["kim"],
        linh_can_levels={"kim": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None, f"{_BODY!r} must be registered"
    assert data.get("process"), f"{_BODY!r} must carry a process block"
    return data


def _enemy(hp: int = 10**9) -> "object":
    """A registered enemy with a huge HP pool and a clean defensive profile so
    a single splash never kills it and damage routes cleanly."""
    e = build_enemy_combatant(_ENEMY_KEY, player_realm_total=_PLAYER_REALM_TOTAL)
    assert e is not None
    e.hp = e.hp_max = hp
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.shield = 0
    return e


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(seed), max_turns=50,
    )


# ── 1. Body loads + composition (pure seam, flag-independent) ───────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "kim"
    assert data["rarity"] == "legendary"
    assert data["roll_weight"] == 0
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    # Milestone-1 stat_bonuses MUST be empty so L1 == flat identity.
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    # L1: no milestone deltas, growth term is 0 → flat stat_bonuses verbatim.
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffSatKhi"]


def test_composition_milestone_and_growth_math() -> None:
    data = _body_data()

    # L3: flat + milestone-3 + growth×2.
    l3 = effective_stat_bonuses(data, 3)
    assert l3["atk_pct"] == pytest.approx(0.06 + 0.05 + 0.005 * 2)
    assert l3["crit_rating"] == 120 + 0 + 25 * 2
    assert l3["crit_dmg_rating"] == 150 + 0 + 35 * 2
    assert l3["kim_pha_giap_on_hit_chance"] == 0.50
    assert l3["kim_pha_giap_high_sat_khi_chance"] == 0.80

    # L6: adds Thiên Cương Chiến Ý effect + crit_dmg milestone.
    assert "BuffThienCuongChienY" in effective_effects(data, 6)

    # L9: full milestone sum + growth×8.
    l9 = effective_stat_bonuses(data, 9)
    assert l9["atk_pct"] == pytest.approx(0.06 + (0.05 + 0.06 + 0.08) + 0.005 * 8)
    assert l9["crit_rating"] == 120 + 150 + 25 * 8          # 470
    assert l9["crit_dmg_rating"] == 150 + 120 + 35 * 8      # 550
    assert l9["kim_sword_splash_at_max_sat_khi"] is True
    assert l9["kim_sword_splash_crit_coeff"] == 0.4
    assert l9["kim_sword_splash_chance_cap"] == 0.4
    assert effective_effects(data, 9) == [
        "BuffSatKhi", "BuffThienCuongChienY", "BuffSatKhiDaiThanh",
    ]


# ── 2. Sát Khí — accrual, cap, and per-stack scaling (flag ON) ──────────────


def test_sat_khi_accrues_one_per_hit_capped_at_5(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])
    assert player.has_effect("BuffSatKhi")
    assert player.sat_khi_stacks == 0

    enemy = _enemy()
    session = _session(player, enemy)
    seen = []
    for _ in range(8):
        run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
        seen.append(player.sat_khi_stacks)
    # +1 per hit until the cap of 5, then held.
    assert seen == [1, 2, 3, 4, 5, 5, 5, 5]


def test_sat_khi_scaling_rules_add_crit_and_crit_dmg(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])

    base_crit = player.crit_rating
    base_crit_dmg = player.crit_dmg_rating
    # No stacks → no scaling contribution.
    mods0 = get_combat_modifiers(player)
    assert mods0.get("crit_rating", 0) == 0
    assert mods0.get("crit_dmg_rating", 0) == 0

    player.sat_khi_stacks = 3
    mods3 = get_combat_modifiers(player)
    assert mods3["crit_rating"] == 15 * 3
    assert mods3["crit_dmg_rating"] == 25 * 3

    # At the cap the per-rule max_output clamps (75 crit, 125 crit_dmg).
    player.sat_khi_stacks = 5
    mods5 = get_combat_modifiers(player)
    assert mods5["crit_rating"] == 75
    assert mods5["crit_dmg_rating"] == 125

    # The base stat is unchanged — the bonus is a live modifier, not baked in.
    assert player.crit_rating == base_crit
    assert player.crit_dmg_rating == base_crit_dmg


# ── 3. L3 Kim Phá Ngọc Toái — Phá Giáp on-hit, high chance at 3+ stacks ─────


def test_l3_pha_giap_applies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(3), player_skill_keys=[_ATTACK_SKILL])
    assert player.kim_pha_giap_on_hit_chance == 0.50
    assert player.kim_pha_giap_high_sat_khi_chance == 0.80

    enemy = _enemy()
    # SeedRng-style: random() == 0.0 always < any positive chance → always lands.
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # force the proc roll to land
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.has_effect("DebuffPhaGiap")


def test_l3_high_chance_gate_at_three_stacks(monkeypatch) -> None:
    """A roll of 0.65 lands only when the 0.80 high chance is active (3+ stacks);
    at low stacks (0.50 chance) the same roll denies.

    NOTE the load-bearing ordering: block (a) stamps a Sát Khí stack BEFORE
    block (b) reads the count, so the pre-hit stack count is one less than what
    the Phá Giáp gate sees. We pre-set stacks so the POST-stamp count straddles
    the 3-stack threshold.
    """
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    # Pre-stamp 1 → post-stamp 2 (< 3) → base 0.50 chance: roll 0.65 ≥ 0.50 → DENIED.
    p_low = build_player_combatant(_make_char(3), player_skill_keys=[_ATTACK_SKILL])
    p_low.sat_khi_stacks = 1
    e_low = _enemy()
    s_low = _session(p_low, e_low)
    s_low.rng.random = lambda: 0.65
    run_on_hit_procs(s_low, p_low, e_low, is_crit=False, skill_key=_ATTACK_SKILL)
    assert p_low.sat_khi_stacks == 2  # stamp ran
    assert not e_low.has_effect("DebuffPhaGiap")

    # Pre-stamp 3 → post-stamp 4 (≥ 3) → high 0.80 chance: roll 0.65 < 0.80 → LANDS.
    p_hi = build_player_combatant(_make_char(3), player_skill_keys=[_ATTACK_SKILL])
    p_hi.sat_khi_stacks = 3
    e_hi = _enemy()
    s_hi = _session(p_hi, e_hi)
    s_hi.rng.random = lambda: 0.65
    run_on_hit_procs(s_hi, p_hi, e_hi, is_crit=False, skill_key=_ATTACK_SKILL)
    assert p_hi.sat_khi_stacks == 4
    assert e_hi.has_effect("DebuffPhaGiap")


# ── 4. L6 Thiên Cương Chiến Ý — missing-HP final_dmg_bonus ──────────────────


def test_l6_final_dmg_bonus_scales_with_missing_hp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(6), player_skill_keys=[_ATTACK_SKILL])
    assert player.has_effect("BuffThienCuongChienY")

    # Full HP → no missing-HP bonus.
    assert get_combat_modifiers(player).get("final_dmg_bonus", 0.0) == pytest.approx(0.0)

    import math

    # Mid-bucket fractions (away from int-truncation boundaries) so the
    # ``floor(actual_missing / 0.10) × 0.05`` math is unambiguous. We compute
    # ``expected`` from the combatant's ACTUAL post-clamp missing%, not the
    # nominal target, so HP int-truncation never desyncs the assertion.
    for missing in (0.15, 0.27, 0.55, 0.84):
        player.hp = max(1, int(player.hp_max * (1.0 - missing)))
        actual_missing = 1.0 - player.hp / player.hp_max
        expected = min(0.50, math.floor(actual_missing / 0.10) * 0.05)
        got = float(get_combat_modifiers(player).get("final_dmg_bonus", 0.0))
        assert got == pytest.approx(expected), f"missing={missing} actual={actual_missing}"

    # The 0.50 cap clamps the contribution: at 1 HP (~100% missing → 9 full
    # 10%-buckets) the bonus reaches 0.45; the cap only matters as an upper
    # bound, so assert it never exceeds 0.50.
    player.hp = 1
    assert float(get_combat_modifiers(player).get("final_dmg_bonus", 0.0)) <= 0.50


# ── 5. L9 Sát Khí Đại Thành — suppression + Kiếm Lãng splash ─────────────────


def test_l9_suppression_lands_at_five_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    player.sat_khi_stacks = 5
    enemy = _enemy()
    session = _session(player, enemy)
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.has_effect("DebuffKiemLangApChe")
    mods = get_combat_modifiers(enemy)
    assert mods["atk_pct"] == pytest.approx(-0.10)
    assert mods["matk_pct"] == pytest.approx(-0.10)


def test_l9_suppression_does_not_fire_below_five_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    # Pre-stamp 3 → post-stamp 4 (one short of the 5-stack gate). Block (a)'s
    # stamp runs first, so the L9 ``== 5`` check sees 4 → no payoff.
    player.sat_khi_stacks = 3
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would force any proc that gets to roll
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert player.sat_khi_stacks == 4
    assert not enemy.has_effect("DebuffKiemLangApChe")


def _splash_chance(player) -> float:
    eff_crit = crit_chance(
        player.crit_rating + int(get_combat_modifiers(player).get("crit_rating", 0)), 0
    )
    return min(
        player.kim_sword_splash_chance_cap,
        player.kim_sword_splash_base_chance
        + player.kim_sword_splash_crit_coeff * eff_crit,
    )


def test_l9_splash_chance_is_crit_scaled_and_capped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    player.sat_khi_stacks = 5
    chance = _splash_chance(player)
    # base 0.0 + 0.4 × eff_crit, clamped to the 0.4 cap.
    assert 0.0 < chance <= 0.4


def test_l9_splash_fires_below_chance_no_fire_above(monkeypatch) -> None:
    """Force a fire (roll just under chance) and a no-fire (roll just over)."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    # FIRE: roll strictly below the splash chance.
    p_fire = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    p_fire.sat_khi_stacks = 5
    e_fire = _enemy()
    s_fire = _session(p_fire, e_fire)
    chance = _splash_chance(p_fire)
    # The splash roll is the only rng.random() the L9 block consumes before the
    # cast (suppression inflict + crit_chance are rng-free), and it's the first
    # rng.random() any earlier proc reaches with these flags — pin it deterministically.
    s_fire.rng.random = lambda: chance - 0.001
    hp0 = e_fire.hp
    run_on_hit_procs(s_fire, p_fire, e_fire, is_crit=False, skill_key=_ATTACK_SKILL)
    assert any("bùng nổ" in line for line in s_fire.log), "splash should have fired"
    assert e_fire.hp < hp0, "fired splash must deal damage"

    # NO FIRE: roll at/above the chance.
    p_no = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    p_no.sat_khi_stacks = 5
    e_no = _enemy()
    s_no = _session(p_no, e_no)
    s_no.rng.random = lambda: _splash_chance(p_no) + 0.001
    hp0_no = e_no.hp
    run_on_hit_procs(s_no, p_no, e_no, is_crit=False, skill_key=_ATTACK_SKILL)
    assert not any("bùng nổ" in line for line in s_no.log)
    # Suppression still lands (it's unconditional at 5 stacks), but no splash dmg.
    assert e_no.hp == hp0_no


def test_l9_splash_damage_magnitude_and_no_restamp(monkeypatch) -> None:
    """A fired splash deals real damage scaled by ~2×atk + 2×matk and does NOT
    re-stamp Sát Khí (the skill_key guard) nor recurse.

    The splash routes the FULL pipeline (it can be evaded and can crit), so a
    pinned ``rng.random()=0.0`` would force an evade. We instead scan seeds for
    a fight where the splash both fires and lands, then assert the magnitude
    band and the no-re-stamp / single-cast invariants.
    """
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    landed = None
    for seed in range(200):
        player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
        player.sat_khi_stacks = 5
        enemy = _enemy()
        session = _session(player, enemy, seed=seed)
        hp0 = enemy.hp
        run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
        splash_lines = [line for line in session.log if "bùng nổ" in line]
        if splash_lines and enemy.hp < hp0:
            landed = (player, enemy, hp0 - enemy.hp, len(splash_lines))
            break

    assert landed is not None, "no seed produced a landed splash in 200 tries"
    player, enemy, dealt, splash_count = landed
    expected_base = 2 * player.atk + 2 * player.matk

    assert dealt > 0
    # Lower bound: at least the non-crit ±15% floor of the 2atk+2matk scale
    # (the SKILL_STAT_SCALE_MULT is 1.0; a crit or final_dmg_bonus only pushes
    # it higher). Allow for the enemy's physical-defense mitigation by using a
    # generous halving floor.
    assert dealt >= int(expected_base * 0.85 * 0.5), (dealt, expected_base)
    # No re-stamp: the splash's own on-hit sweep skipped the Sát Khí stamp via
    # the ``skill_key == "SkillKimKiemLang"`` guard — stacks stay pinned at 5.
    assert player.sat_khi_stacks == 5
    # No recursion: the splash fired EXACTLY once. The splash routes its own hit
    # back through run_on_hit_procs while still at 5 stacks + the marker buff;
    # without the skill_key guard on the L9 block it would re-fire and recurse,
    # producing many "bùng nổ" log lines.
    assert splash_count == 1


# ── 6. Inertness — flag OFF leaves the body flat, no effects, procs no-op ────


def test_flag_off_no_effects_stamped() -> None:
    assert settings.constitution_process_enabled is False  # default
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    # No process effects stamped while dormant.
    assert not player.has_effect("BuffSatKhi")
    assert not player.has_effect("BuffThienCuongChienY")
    assert not player.has_effect("BuffSatKhiDaiThanh")
    # Config-only flags resolve to their inert defaults (flat path ignores level).
    assert player.kim_pha_giap_on_hit_chance == 0.0
    assert player.kim_sword_splash_at_max_sat_khi is False


def test_flag_off_resolves_to_flat_stat_bonuses() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "body", _BODY_REALM)
    data = _body_data()
    # Flat path: only the top-level stat_bonuses, no milestone deltas, no
    # config-only Kim keys.
    assert flat == data["stat_bonuses"]
    assert "kim_pha_giap_on_hit_chance" not in flat


def test_flag_off_procs_are_noop() -> None:
    assert settings.constitution_process_enabled is False
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would force any proc that reaches a roll
    for _ in range(6):
        run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    # No Sát Khí buff → no stacks; no Kim flags → no Phá Giáp / suppression / splash.
    assert player.sat_khi_stacks == 0
    assert not enemy.has_effect("DebuffPhaGiap")
    assert not enemy.has_effect("DebuffKiemLangApChe")
    assert not any("bùng nổ" in line for line in session.log)
