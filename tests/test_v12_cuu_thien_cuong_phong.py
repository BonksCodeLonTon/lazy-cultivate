"""v12 constitution — Cửu Thiên Cương Phong Thể (Nine Heavens Steel Wind Body, Phong).

The 3rd Phong body: an ANTI-EVASION WIND-BLADE SHREDDER (ATK-based; vs Phi
Thiên's dodge-counter and Tiêu Dao's movement-dancer) — every strike marks,
bleeds and shreds; bonus blades proc mid-combo; a steel-wind storm cycles on
cadence; a per-target Phong Nhận Tích counter ramps to an armor-shattering
capped true-damage pierce.

  L1 Phong Nhận      — per hit: auto Ấn Phong (evasion −150) + 50% Chảy Máu
                       + 1 Phong Nhận Tích ON THE TARGET (cap 8).
  L3 Xuyên Tâm Phong — per hit: NEW DebuffPhongXuyenThau (res_phong −20%, 4t);
                       sustained 25% armor pierce → 40% at ≥4 Tích (replaces
                       the sheet's degenerate 0-evasion jackpot).
  L6 Phong Bạo       — +220 crit_dmg_rating; 35%/hit bonus wind blade at 60%
                       ATK + Ấn Phong (chain_only SkillPhongBonusStrike,
                       recursion-guarded like the Lôi reflex).
  L9 Cương Phong     — every 7 acted turns → 3-turn storm: unevadable +
                       force-crit + +1 hit + 35% Cuốn Bay/hit (sheet's
                       on-activate evasion strip dropped as redundant). At 8
                       Tích: Cương Phong Xuyên — 150% ATK through the shared
                       12%-cap true-dmg rider + guaranteed Cuốn Bay, Tích reset.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr``
so it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, get_combat_modifiers
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.combat.procs import run_cuong_phong_procs
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_CuuThienCuongPhong"
_ATTACK = "SkillKimTripleStrike"    # physical 3-hit — good proc driver
_ENEMY = "TinhKimTho"


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _body_data():
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.shield = 0
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.spd = 0
    return e


def _player(level: int):
    char = Character(
        player_id=1, discord_id=1, name="CuongPhongTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["phong"], linh_can_levels={"phong": 5},
        constitution_levels={_BODY: level}, stats=CharacterStats(),
    )
    return build_player_combatant(char, player_skill_keys=[_ATTACK])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[_ATTACK],
        rng=random.Random(seed), max_turns=200,
    )


def _tick_pre_turn(session, combatant, turns: int) -> None:
    other = session.enemy if combatant is session.player else session.player
    for _ in range(turns):
        ctx = TurnContext(actor=combatant, target=other, session=session)
        run_phase(TurnPhase.PRE_TURN, ctx)


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "phong"
    assert data["rarity"] == "legendary"
    assert data["cost"]["merit"] == 60000
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    flat = data["stat_bonuses"]
    for cfg in ("cp_an_phong_on_hit", "cp_pierce_def_pct", "cp_storm_interval"):
        assert cfg not in flat
    assert flat["atk_pct"] == 0.06


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffPhongNhan"]
    assert effective_effects(data, 9) == [
        "BuffPhongNhan", "BuffXuyenTamPhong", "BuffPhongBao", "BuffCuongPhong",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffPhongNhan", "BuffXuyenTamPhong", "BuffPhongBao",
                "BuffCuongPhong", "BuffCuongPhongBao", "DebuffPhongXuyenThau"):
        assert EFFECTS.get(key) is not None
    assert EFFECTS["DebuffPhongXuyenThau"].stat_bonus["res_phong"] == pytest.approx(-0.20)
    assert registry.get_skill("SkillPhongBonusStrike") is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    # L1's mark + bleed ride the GENERIC on-hit lanes (real stats, additive
    # with the phong linh-căn's own mark contribution).
    assert p1.mark_on_hit_pct >= 1.0
    assert p1.bleed_on_hit_pct >= 0.50
    assert p1.cp_tich_cap == 8
    p3 = _player(3)
    assert p3.phong_shred_on_hit_pct >= 1.0   # generic shred lane
    assert p3.cp_pierce_def_pct == pytest.approx(0.25)
    assert p3.cp_pierce_def_pct_high == pytest.approx(0.40)
    assert p3.cp_pierce_tich_gate == 4
    p6 = _player(6)
    assert p6.cp_bonus_strike_chance == pytest.approx(0.35)
    assert p6.crit_dmg_rating >= 220
    p9 = _player(9)
    assert p9.cp_storm_interval == 7
    assert p9.cp_storm_duration == 3
    assert p9.cp_storm_cuon_bay_chance == pytest.approx(0.35)
    assert p9.cp_storm_extra_hits == 1
    assert p9.cp_tich_execute_atk_scale == pytest.approx(1.50)


# ── 2. L1 Phong Nhận — marks, bleed, Tích ───────────────────────────────────


def test_l1_marks_bleed_and_tich(monkeypatch) -> None:
    """Ấn Phong + Chảy Máu fire through the GENERIC _ON_HIT_PROCS lanes
    (mark_on_hit_pct 1.0 / bleed_on_hit_pct 0.50) on a real cast; the Tích
    bank is the only body-specific rider."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0            # every proc roll lands
    skill = registry.get_skill(_ATTACK)
    cast_skill(session, player, enemy, _ATTACK, dict(skill), 0)
    assert enemy.has_effect("DebuffAnPhong")
    assert enemy.has_effect("DebuffChayMau")
    assert enemy.bleed_stacks >= 1
    assert enemy.cp_tich_stacks == 3            # one per landed hit of the 3-hit cast


def test_l1_tich_caps_and_negated_hit_inert(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    for _ in range(12):
        run_cuong_phong_procs(session, player, enemy, dmg=1_000)
    assert enemy.cp_tich_stacks == 8            # capped (no L9 → no spend)
    before = enemy.cp_tich_stacks
    run_cuong_phong_procs(session, player, enemy, dmg=0)
    assert enemy.cp_tich_stacks == before       # dmg<=0 → nothing


# ── 3. L3 Xuyên Tâm Phong — shred + ramping pierce ──────────────────────────


def test_l3_shred_applied(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    player.crit_rating = 0
    session = _session(player, enemy)
    session.rng.random = lambda: 0.5              # shred lane at 1.0 still lands
    skill = registry.get_skill(_ATTACK)
    cast_skill(session, player, enemy, _ATTACK, dict(skill), 0)
    assert enemy.has_effect("DebuffPhongXuyenThau")
    mods = get_combat_modifiers(enemy)
    assert mods.get("res_phong", 0.0) == pytest.approx(-0.20)


def test_l3_pierce_ramps_with_tich(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.crit_rating = 0

    def loss(tich: int) -> int:
        enemy = _enemy()
        # Moderate def — huge values saturate MAX_PHYS_REDUCTION and mask the
        # pierce difference (reduction = min(cap, def/(def+K))).
        enemy.def_stat = 2_000
        enemy.cp_tich_stacks = tich
        session = _session(player, enemy)
        session.rng.random = lambda: 0.99
        skill = registry.get_skill(_ATTACK)
        before = enemy.hp
        cast_skill(session, player, enemy, _ATTACK, dict(skill), 0)
        return before - enemy.hp

    base = loss(0)          # 25% pierce
    ramped = loss(4)        # 40% pierce at the gate
    assert ramped > base


# ── 4. L6 Phong Bạo — bonus wind blade ──────────────────────────────────────


def test_l6_bonus_strike_fires_and_marks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0            # every roll lands
    skill = registry.get_skill(_ATTACK)
    cast_skill(session, player, enemy, _ATTACK, dict(skill), 0)
    blades = [l for l in session.log if "lưỡi gió phụ" in l]
    assert blades                                # at least one bonus strike
    assert enemy.has_effect("DebuffAnPhong")


def test_l6_bonus_strike_no_unbounded_recursion(monkeypatch) -> None:
    """rng pinned to 0.0 → every proc roll succeeds; the skill_key guard must
    still terminate the storm (bonus strikes never re-proc themselves)."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    skill = registry.get_skill(_ATTACK)
    cast_skill(session, player, enemy, _ATTACK, dict(skill), 0)  # returns = no infinite loop
    blades = [l for l in session.log if "lưỡi gió phụ" in l]
    assert len(blades) <= 3                      # one per landed hit of the 3-hit cast


# ── 5. L9 Cương Phong — storm window + full-Tích pierce ─────────────────────


def test_l9_storm_cadence_and_window_kit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    _tick_pre_turn(session, player, 6)
    assert not player.has_effect("BuffCuongPhongBao")
    _tick_pre_turn(session, player, 1)          # 7th acted turn
    assert player.has_effect("BuffCuongPhongBao")
    assert player.effects["BuffCuongPhongBao"] == 3
    # Window kit: force-crit + extra hit + unevadable.
    from src.game.engine.damage.combat_hit import build_attack_stats
    stats = build_attack_stats(player, enemy, get_combat_modifiers(player), "phong")
    assert stats.force_crit is True
    player.crit_rating = 0
    enemy.evasion_rating = 10**6
    session.rng.random = lambda: 0.0            # dodge rolls would succeed
    skill = registry.get_skill(_ATTACK)
    before = enemy.hp
    session.log.clear()
    cast_skill(session, player, enemy, _ATTACK, dict(skill), 0)
    assert enemy.hp < before                     # unevadable through max evasion
    follow_ups = [l for l in session.log if "liên kích" in l]
    assert len(follow_ups) == 3                  # 3-hit skill + 1 storm hit


def test_l9_storm_cuon_bay_rider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.apply_effect("BuffCuongPhongBao", 3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_cuong_phong_procs(session, player, enemy, dmg=1_000)
    assert enemy.has_effect("DebuffCuonBay")


def test_l9_full_tich_execute_capped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.atk = 100_000                         # 150% ATK = 150k raw
    enemy = _enemy(hp=1_000_000)
    enemy.cp_tich_stacks = 7                     # this hit banks the 8th
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99            # bleed/CuonBay window rolls fail
    before = enemy.hp
    run_cuong_phong_procs(session, player, enemy, dmg=1_000)
    assert enemy.cp_tich_stacks == 0             # spent
    expected = min(int(100_000 * 1.5), int(enemy.hp_max * 0.12))  # cap binds: 120k
    assert before - enemy.hp == expected
    assert enemy.has_effect("DebuffCuonBay")     # guaranteed rider


def test_l9_no_execute_below_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy(hp=1_000_000)
    enemy.cp_tich_stacks = 5
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    before = enemy.hp
    run_cuong_phong_procs(session, player, enemy, dmg=1_000)
    assert enemy.cp_tich_stacks == 6             # banked, not spent
    assert enemy.hp == before                    # no true damage


# ── 6. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.mark_on_hit_pct < 1.0   # only the linh-căn baseline remains
    assert player.cp_tich_cap == 0
    assert player.cp_pierce_def_pct == 0.0
    assert player.cp_bonus_strike_chance == 0.0
    assert player.cp_storm_interval == 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_cuong_phong_procs(session, player, enemy, dmg=1_000)
    assert not enemy.has_effect("DebuffAnPhong")
    assert not enemy.has_effect("DebuffPhongXuyenThau")
    assert enemy.cp_tich_stacks == 0
    _tick_pre_turn(session, player, 10)
    assert not player.has_effect("BuffCuongPhongBao")
