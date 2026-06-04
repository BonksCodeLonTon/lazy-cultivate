"""v12 constitution — Thái Bạch Canh Kim Thể (Taiyi White Metal Body, Kim).

The second body on the Constitution-Process engine: a fast evasive crit-bleeder.
These tests pin every mechanic on the flag-ON path and the flag-OFF inert seam,
plus a regression that ``apply_critical``'s default params stay byte-identical
(no existing caller perturbed). Phase-0 byte-identity is guarded separately by
``test_constitution_process_guard.py``.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.damage.critical import apply_critical
from src.game.engine.effects import get_combat_modifiers
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.combat.procs import run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_ThaiBachCanhKim"
_ATTACK_SKILL = "SkillAtkKim3"
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 5
_PLAYER_REALM_TOTAL = 15


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="ThaiBachTester",
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


# ── 1. Composition (pure seam) ──────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "kim"
    assert data["rarity"] == "legendary"
    assert data["roll_weight"] == 0
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffBachKimPhongVu"]


def test_composition_milestone_and_growth_math() -> None:
    data = _body_data()

    l3 = effective_stat_bonuses(data, 3)
    assert l3["spd_pct"] == pytest.approx(0.06 + 0.25 + 0.004 * 2)
    assert l3["evasion_rating"] == 120 + 200 + 22 * 2
    assert l3["atk_pct"] == pytest.approx(0.05 + 0.004 * 2)
    assert l3["crit_rating"] == 100 + 20 * 2
    assert "BuffNextSkillHitCount" in effective_effects(data, 3)

    l6 = effective_stat_bonuses(data, 6)
    assert l6["equip_stat_amp_pct"] == 0.25
    assert l6["crit_rating"] == 100 + 80 + 20 * 5

    l9 = effective_stat_bonuses(data, 9)
    assert l9["atk_pct"] == pytest.approx(0.05 + (0.05 + 0.07) + 0.004 * 8)
    assert l9["spd_pct"] == pytest.approx(0.06 + 0.25 + 0.004 * 8)
    assert l9["evasion_rating"] == 120 + 200 + 22 * 8        # 496
    assert l9["crit_rating"] == 100 + (80 + 120) + 20 * 8    # 460
    assert l9["kim_bleed_hunter_crit_chance_bonus"] == 0.40
    assert l9["kim_bleed_hunter_crit_dmg_bonus"] == 0.20
    assert l9["kim_periodic_crit_interval"] == 3
    assert effective_effects(data, 9) == [
        "BuffBachKimPhongVu", "BuffNextSkillHitCount",
        "BuffVanKhiGiaiKim", "BuffHuyetLapThaiBach",
    ]


# ── 2. L1 bleed proc (reuses the existing bleed_on_hit_pct row) ─────────────


def test_l1_bleed_proc_applies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])
    assert player.bleed_on_hit_pct == pytest.approx(0.45)
    assert player.has_effect("BuffBachKimPhongVu")

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # force the 45% bleed roll to land
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.has_effect("DebuffChayMau")
    assert enemy.bleed_stacks >= 1


# ── 3. Bạch Kim Phong Vũ stacking (PERIODIC) ────────────────────────────────


def test_bach_kim_accrues_while_target_bleeds_capped_at_5(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])
    enemy = _enemy()
    enemy.bleed_stacks = 2  # target is bleeding
    session = _session(player, enemy)

    seen = []
    for _ in range(8):
        session._process_periodic(player)
        seen.append(player.bach_kim_stacks)
    assert seen == [1, 2, 3, 4, 5, 5, 5, 5]


def test_bach_kim_does_not_accrue_when_target_not_bleeding(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])
    enemy = _enemy()
    enemy.bleed_stacks = 0  # not bleeding
    session = _session(player, enemy)
    for _ in range(5):
        session._process_periodic(player)
    assert player.bach_kim_stacks == 0


def test_bach_kim_scaling_rules_add_atk_and_bleed_dmg(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])

    base = get_combat_modifiers(player)
    assert base.get("atk_pct", 0.0) == pytest.approx(0.0)
    assert base.get("bleed_dmg_bonus", 0.0) == pytest.approx(0.0)

    player.bach_kim_stacks = 3
    mods3 = get_combat_modifiers(player)
    assert mods3["atk_pct"] == pytest.approx(0.04 * 3)
    assert mods3["bleed_dmg_bonus"] == pytest.approx(0.005 * 3)

    # At the cap the per-rule max_output clamps (0.20 atk, 0.025 bleed).
    player.bach_kim_stacks = 5
    mods5 = get_combat_modifiers(player)
    assert mods5["atk_pct"] == pytest.approx(0.20)
    assert mods5["bleed_dmg_bonus"] == pytest.approx(0.025)


# ── 4. L3 milestone stats + one-shot +3 hit-count ───────────────────────────


def test_l3_milestone_speed_and_evasion(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    l3 = build_player_combatant(_make_char(3), player_skill_keys=[_ATTACK_SKILL])
    l1 = build_player_combatant(_make_char(1), player_skill_keys=[_ATTACK_SKILL])
    # The L3 milestone adds +0.25 spd_pct (→ higher effective spd) and +200
    # evasion_rating (plus growth) over L1.
    assert l3.evasion_rating > l1.evasion_rating
    assert l3.has_effect("BuffNextSkillHitCount")


def test_l3_hit_count_armed_and_consumed_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(3), player_skill_keys=[_ATTACK_SKILL])
    # Armed at battle start.
    assert player.next_skill_hit_count_bonus == 3

    enemy = _enemy()
    session = _session(player, enemy)
    from src.game.systems.combat.casting import cast_skill

    # First top-level cast consumes the +3 and zeroes the arm.
    skill = registry.get_skill(_ATTACK_SKILL)
    base_hits = max(1, int(skill.get("hit_count", 1)))
    first_log_len = len(session.log)
    cast_skill(session, player, enemy, _ATTACK_SKILL, skill, 0)
    assert player.next_skill_hit_count_bonus == 0
    # The +3 produced ``base_hits + 3`` total hits → at least 3 extra
    # "liên kích" follow-up lines this cast (one per extra hit beyond the first).
    extra_hit_lines = sum(
        1 for line in session.log[first_log_len:] if "liên kích bồi thêm" in line
    )
    assert extra_hit_lines == (base_hits + 3) - 1

    # Second cast does NOT re-arm — no bonus left.
    second_log_len = len(session.log)
    enemy2 = _enemy()
    cast_skill(session, player, enemy2, _ATTACK_SKILL, skill, 0)
    extra_hit_lines_2 = sum(
        1 for line in session.log[second_log_len:] if "liên kích bồi thêm" in line
    )
    assert extra_hit_lines_2 == base_hits - 1
    assert player.next_skill_hit_count_bonus == 0


# ── 5. L6 equipment-stat amplifier (build-time pre-pass) ────────────────────


def test_l6_equip_amp_scales_only_equipment_stats(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    equip = {"atk": 1000, "crit_rating": 400, "evasion_rating": 200}

    with_equip = build_player_combatant(
        _make_char(6), player_skill_keys=[_ATTACK_SKILL], equip_stats=dict(equip)
    )
    no_equip = build_player_combatant(
        _make_char(6), player_skill_keys=[_ATTACK_SKILL]
    )
    # The equip-derived crit_rating delta must be the gear value × 1.25.
    assert with_equip.crit_rating - no_equip.crit_rating == int(400 * 1.25)
    assert with_equip.evasion_rating - no_equip.evasion_rating == int(200 * 1.25)


def test_l3_equip_not_amplified(monkeypatch) -> None:
    """At L3 (no equip_stat_amp_pct milestone) gear is NOT amplified — proves
    the amp is gated on the L6 milestone, not always-on for the body."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    equip = {"crit_rating": 400}
    with_equip = build_player_combatant(
        _make_char(3), player_skill_keys=[_ATTACK_SKILL], equip_stats=dict(equip)
    )
    no_equip = build_player_combatant(
        _make_char(3), player_skill_keys=[_ATTACK_SKILL]
    )
    assert with_equip.crit_rating - no_equip.crit_rating == 400  # unscaled


# ── 6. L9 anti-bleed crit hunt + periodic guaranteed crit ───────────────────


def test_l9_bleed_hunter_fields_read(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    assert p.bleed_hunter_crit_chance_bonus == pytest.approx(0.40)
    assert p.bleed_hunter_crit_dmg_bonus == pytest.approx(0.20)
    assert p.bleed_hunter_periodic_interval == 3
    assert p.has_effect("BuffHuyetLapThaiBach")


def _crit_rate(player, enemy, *, keep_bleeding: bool, n: int = 4000) -> float:
    """Empirical crit rate of player's attack vs ``enemy`` over ``n`` casts.

    ``keep_bleeding`` pins the target's bleed state each iteration: True keeps
    ≥1 bleed stack so the L9 vs-bleed amp arms; False scrubs the bleed the
    body's own ``bleed_on_hit_pct`` proc would otherwise apply, so the clean
    measurement genuinely never carries bleed.
    """
    from src.game.systems.combat.casting import cast_skill
    skill = registry.get_skill(_ATTACK_SKILL)
    crits = 0
    rng = random.Random(1234)
    for _ in range(n):
        session = CombatSession(
            player=player, enemy=enemy, player_skill_keys=[_ATTACK_SKILL],
            rng=rng, max_turns=50,
        )
        # Disarm the periodic guaranteed-crit so we measure only the
        # state-conditional chance amp, and pin the bleed state.
        player.bleed_hunter_crit_armed = False
        enemy.hp = enemy.hp_max
        if keep_bleeding:
            enemy.bleed_stacks = 3
        else:
            enemy.bleed_stacks = 0
            enemy.effects.pop("DebuffChayMau", None)
        before = len(session.log)
        cast_skill(session, player, enemy, _ATTACK_SKILL, skill, 0)
        if any("BẠO KÍCH" in line for line in session.log[before:]):
            crits += 1
    return crits / n


def test_l9_crit_bonus_only_vs_bleeding_target(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])

    bleeding = _enemy()
    clean = _enemy()

    rate_bleeding = _crit_rate(player, bleeding, keep_bleeding=True)
    rate_clean = _crit_rate(player, clean, keep_bleeding=False)

    # The +0.40 chance amp only arms vs a bleeding target → strictly higher
    # crit rate there. Clean target sees the base rating-derived rate (~0.20),
    # bleeding sees ~0.60 (base + 0.40, under the 0.75 cap).
    assert rate_bleeding > rate_clean + 0.20, (rate_bleeding, rate_clean)


def test_l9_periodic_guaranteed_crit_arms_every_third_turn(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    enemy = _enemy()
    session = _session(player, enemy)

    armed_on = []
    for t in range(1, 7):
        run_phase(TurnPhase.PRE_TURN, TurnContext(actor=player, target=enemy, session=session))
        armed_on.append(player.bleed_hunter_crit_armed)
        player.bleed_hunter_crit_armed = False  # simulate consume on the turn's cast
    # Armed exactly on the 3rd and 6th acted turns.
    assert armed_on == [False, False, True, False, False, True]


def test_l9_periodic_arm_forces_crit_then_consumes(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    enemy = _enemy()
    enemy.bleed_stacks = 0  # isolate the FORCE-crit from the vs-bleed chance amp
    session = _session(player, enemy)
    player.bleed_hunter_crit_armed = True

    from src.game.systems.combat.casting import cast_skill
    skill = registry.get_skill(_ATTACK_SKILL)
    before = len(session.log)
    cast_skill(session, player, enemy, _ATTACK_SKILL, skill, 0)
    # The armed turn's landed hit crit (force_crit) and the arm was consumed.
    assert any("BẠO KÍCH" in line for line in session.log[before:])
    assert player.bleed_hunter_crit_armed is False


# ── 7. apply_critical regression — default params byte-identical ────────────


def test_apply_critical_default_params_byte_identical() -> None:
    """Every existing caller omits the new params; the default-zero path must
    reproduce the original result for the same seed."""
    for cr, cdr in ((0, 0), (100, 200), (500, 400)):
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        for _ in range(50):
            a = apply_critical(1000, cr, 0, cdr, rng_a)
            b = apply_critical(
                1000, cr, 0, cdr, rng_b,
                bonus_crit_chance=0.0, bonus_crit_dmg_mult=0.0,
            )
            assert a == b


def test_apply_critical_bonus_chance_clamps_to_max() -> None:
    from src.game.constants.balance import MAX_CRIT_CHANCE
    # A huge bonus can't push crit chance past the cap.
    crits = 0
    n = 20000
    rng = random.Random(0)
    for _ in range(n):
        _, c = apply_critical(1000, 100, 0, 0, rng, bonus_crit_chance=5.0)
        crits += c
    rate = crits / n
    assert rate <= MAX_CRIT_CHANCE + 0.02
    assert rate >= MAX_CRIT_CHANCE - 0.03


# ── 8. Inertness — flag OFF leaves the body flat, no buffs, paths no-op ──────


def test_flag_off_no_effects_stamped() -> None:
    assert settings.constitution_process_enabled is False
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    for key in (
        "BuffBachKimPhongVu", "BuffNextSkillHitCount",
        "BuffVanKhiGiaiKim", "BuffHuyetLapThaiBach",
    ):
        assert not player.has_effect(key)
    assert player.next_skill_hit_count_bonus == 0
    assert player.bleed_hunter_crit_chance_bonus == 0.0
    assert player.bleed_hunter_periodic_interval == 0


def test_flag_off_resolves_to_flat_stat_bonuses() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "body", _BODY_REALM)
    data = _body_data()
    assert flat == data["stat_bonuses"]
    assert "equip_stat_amp_pct" not in flat
    assert "kim_bleed_hunter_crit_chance_bonus" not in flat


def test_flag_off_equip_not_amplified() -> None:
    assert settings.constitution_process_enabled is False
    equip = {"crit_rating": 400}
    with_equip = build_player_combatant(
        _make_char(9), player_skill_keys=[_ATTACK_SKILL], equip_stats=dict(equip)
    )
    no_equip = build_player_combatant(
        _make_char(9), player_skill_keys=[_ATTACK_SKILL]
    )
    # Flag off → L6 amp never read → gear unscaled.
    assert with_equip.crit_rating - no_equip.crit_rating == 400


def test_flag_off_periodic_and_proc_paths_noop() -> None:
    assert settings.constitution_process_enabled is False
    player = build_player_combatant(_make_char(9), player_skill_keys=[_ATTACK_SKILL])
    enemy = _enemy()
    enemy.bleed_stacks = 3
    session = _session(player, enemy)
    # PERIODIC bach_kim hook: no buff → no stacks.
    for _ in range(5):
        session._process_periodic(player)
    assert player.bach_kim_stacks == 0
    # PRE_TURN crit-arm hook: no buff / zero interval → never arms.
    for _ in range(6):
        run_phase(TurnPhase.PRE_TURN, TurnContext(actor=player, target=enemy, session=session))
    assert player.bleed_hunter_crit_armed is False
    assert player.bleed_hunter_turn_counter == 0
