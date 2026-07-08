"""v12 constitution — Cửu U Ma Đế Thể (Nine Abyss Demon Emperor Body, Ám).

The soul-drain summoner (sheet row 28, 2nd Ám body): L1 Cửu U Ma Khí — rides
the GENERIC ``soul_drain_on_hit_pct``/``stat_steal_on_hit_pct`` lanes + banks
Ma Khí per landed hit (cap 9; BuffCuuUMaKhi scales stacks → matk_pct, and each
stack amps ``apply_soul_drain``'s per-proc drain); L3 Vong Linh — proc-driven
permanent wraith (NOT a per-turn ``summons`` ticker): chance per landed hit to
follow up for % matk Ám damage + a soul drain; L6 Cửu U — 9-stack
final_dmg_bonus gate + U Minh Quỷ Hỏa cast synergy (immediate bonus drains;
the sheet's NghiepLuc ×2 maps to nothing on the real debuff-only skill —
dropped); L9 Ma Lâm Thiên Hạ — build-time evolution gated on the equipped
Chân Ma Chi Tâm passive: Vong Linh → Ma Đế Quỷ Vương (75% / 80% matk, strike
also stat-steals) + BuffMaDeQuyVuong; without the prereq only a partial
follow-up upgrade. NO revive.
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
from src.game.systems.combat.procs import apply_soul_drain, run_cuu_u_procs
from src.utils.config import settings

_BODY = "TheChat_CuuUMaDe"
_PREREQ = "SkillAmChanMaChiTam_R9"
_SYNERGY = "SkillAtkUMinhQuyHoa_R7"
_ENEMY = "DuocVienR6_02"


class _ZeroRng(random.Random):
    def random(self) -> float:  # noqa: D102
        return 0.0


class _OneRng(random.Random):
    def random(self) -> float:  # noqa: D102
        return 0.999999


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


@pytest.fixture()
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


def _make_char(level: int | None = 9) -> Character:
    return Character(
        player_id=1, discord_id=1, name="MaDeTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["am"], linh_can_levels={"am": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _holder(level: int = 9, skills: list[str] | None = None):
    p = build_player_combatant(_make_char(level), skills or ["SkillAtkKim1"])
    p.mp = p.mp_max = 99_999
    return p


def _session(player, rng=None):
    enemy = build_enemy_combatant(_ENEMY, player_realm_total=27)
    assert enemy is not None
    enemy.mp = enemy.mp_max = 99_999
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=list(player.skill_keys),
        rng=rng or random.Random(1), max_turns=10,
    ), enemy


# ── Data sanity ──────────────────────────────────────────────────────────────


def test_body_registered_legendary_am():
    data = registry.get_constitution(_BODY)
    assert data is not None
    assert data["rarity"] == "legendary"
    assert data["element"] == "am"
    process = data["process"]
    assert process["milestones"] == [1, 3, 6, 9]
    for lv in ("1", "3", "6", "9"):
        for eff in process["levels"][lv]["effects"]:
            assert eff in EFFECTS, eff
    assert registry.get_skill(_PREREQ) is not None
    assert registry.get_skill(_SYNERGY) is not None


def _no_root_holder(level: int):
    """Holder WITHOUT the Âm linh căn: the root grants +0.10/+0.08 on the
    same drain/steal lanes (correct additive stacking), which would mask the
    authored constitution values."""
    char = _make_char(level)
    char.linh_can = ["kim"]
    char.linh_can_levels = {"kim": 5}
    return build_player_combatant(char, ["SkillAtkKim1"])


def test_drain_ladder_rides_generic_lanes(_flag_on):
    """The drain/steal power CLIMBS the ladder (T1 nerf 2026-07-08): T1 25%
    drain only; T3 +15% (40% total); the steal lands at T6 — all on the
    EXISTING generic lanes, no cu_ dupes."""
    t1 = _no_root_holder(1)
    assert t1.soul_drain_on_hit_pct == pytest.approx(0.25)
    assert t1.stat_steal_on_hit_pct == pytest.approx(0.0)
    t3 = _no_root_holder(3)
    assert t3.soul_drain_on_hit_pct == pytest.approx(0.40)
    assert t3.stat_steal_on_hit_pct == pytest.approx(0.0)
    t9 = _no_root_holder(9)
    assert t9.soul_drain_on_hit_pct == pytest.approx(0.40)
    assert t9.stat_steal_on_hit_pct == pytest.approx(0.35)


# ── L1 Ma Khí banking + scaling ──────────────────────────────────────────────


def test_ma_khi_banks_per_landed_hit_and_caps(_flag_on):
    p = _holder(1)
    session, enemy = _session(p, rng=_OneRng())  # follow-up never fires
    for _ in range(12):
        run_cuu_u_procs(session, p, enemy, 100)
    assert p.ma_khi_stacks == 9


def test_negated_hit_banks_nothing(_flag_on):
    p = _holder(1)
    session, enemy = _session(p)
    run_cuu_u_procs(session, p, enemy, 0)
    assert p.ma_khi_stacks == 0


def test_ma_khi_scales_matk(_flag_on):
    p = _holder(1)
    base = float(get_combat_modifiers(p).get("matk_pct", 0.0))
    p.ma_khi_stacks = 9
    full = float(get_combat_modifiers(p).get("matk_pct", 0.0))
    assert full - base == pytest.approx(9 * 0.02)


def test_ma_khi_amps_soul_drain_at_l6(_flag_on):
    """The 5%/stack drain amp unlocks at T6 (moved out of T1)."""
    p = _holder(6)
    session, enemy = _session(p)
    apply_soul_drain(session, p, enemy)
    drained_at_0 = enemy.hp_max_drained
    p.ma_khi_stacks = 9
    apply_soul_drain(session, p, enemy)
    drained_at_9 = enemy.hp_max_drained - drained_at_0
    # 9 stacks × 5% → ×1.45 the per-proc base
    assert drained_at_9 == int(drained_at_0 * (1.0 + 9 * 0.05))


def test_no_drain_amp_before_l6(_flag_on):
    p = _holder(3)
    session, enemy = _session(p)
    apply_soul_drain(session, p, enemy)
    drained_at_0 = enemy.hp_max_drained
    p.ma_khi_stacks = 9
    apply_soul_drain(session, p, enemy)
    assert enemy.hp_max_drained - drained_at_0 == drained_at_0


# ── L3 Vong Linh follow-up ───────────────────────────────────────────────────


def test_follow_up_strikes_and_drains(_flag_on):
    p = _holder(3)
    session, enemy = _session(p, rng=_ZeroRng())
    p.ma_khi_stacks = 0
    hp0, drained0 = enemy.hp, enemy.hp_max_drained
    run_cuu_u_procs(session, p, enemy, 500)
    strike = int(p.matk * 0.45)
    assert hp0 - enemy.hp >= strike  # strike + parallel hp clamp from drain
    assert enemy.hp_max_drained > drained0  # the drain rode along
    # L3 is NOT evolved — the follow-up must not stat-steal
    assert not any("Đạo Pháp Thôn Phệ" in line for line in session.log)


def test_follow_up_respects_chance(_flag_on):
    p = _holder(3)
    session, enemy = _session(p, rng=_OneRng())
    hp0 = enemy.hp
    run_cuu_u_procs(session, p, enemy, 500)
    assert enemy.hp == hp0  # roll above 55% — no follow-up


# ── L6 Cửu U — 9-stack gate + U Minh synergy ─────────────────────────────────


def test_nine_stack_final_dmg_gate(_flag_on):
    p = _holder(6)
    p.ma_khi_stacks = 8
    below = float(get_combat_modifiers(p).get("final_dmg_bonus", 0.0))
    p.ma_khi_stacks = 9
    at_cap = float(get_combat_modifiers(p).get("final_dmg_bonus", 0.0))
    assert at_cap - below == pytest.approx(0.35)


def test_uminh_cast_triggers_bonus_drains(_flag_on):
    from src.game.systems.combat.casting import cast_skill

    p = _holder(6, skills=[_SYNERGY])
    session, enemy = _session(p, rng=_OneRng())  # no follow-up noise
    drained0 = enemy.hp_max_drained
    skill_data = registry.get_skill(_SYNERGY)
    cast_skill(session, p, enemy, _SYNERGY, skill_data, mp_cost=0)
    assert sum(1 for line in session.log if "phệ hồn" in line) == 3
    assert enemy.hp_max_drained > drained0


def test_no_synergy_below_l6(_flag_on):
    from src.game.systems.combat.casting import cast_skill

    p = _holder(3, skills=[_SYNERGY])
    session, enemy = _session(p, rng=_OneRng())
    skill_data = registry.get_skill(_SYNERGY)
    cast_skill(session, p, enemy, _SYNERGY, skill_data, mp_cost=0)
    assert not any("phệ hồn" in line for line in session.log)


# ── L9 Ma Lâm Thiên Hạ — prerequisite-gated evolution ────────────────────────


def test_evolution_with_prereq_equipped(_flag_on):
    p = _holder(9, skills=["SkillAtkKim1", _PREREQ])
    assert p.cu_ma_de_evolved
    assert p.cu_vl_follow_up_chance == pytest.approx(0.75)
    assert p.cu_vl_dmg_matk_pct == pytest.approx(0.80)
    assert p.has_effect("BuffMaDeQuyVuong")


def test_partial_upgrade_without_prereq(_flag_on):
    p = _holder(9, skills=["SkillAtkKim1"])
    assert not p.cu_ma_de_evolved
    assert p.cu_vl_follow_up_chance == pytest.approx(0.70)
    assert p.cu_vl_dmg_matk_pct == pytest.approx(0.45)  # L3 damage unchanged
    assert not p.has_effect("BuffMaDeQuyVuong")


def test_no_evolution_below_l9_even_with_prereq(_flag_on):
    p = _holder(6, skills=["SkillAtkKim1", _PREREQ])
    assert not p.cu_ma_de_evolved
    assert not p.has_effect("BuffMaDeQuyVuong")


def test_evolved_follow_up_also_stat_steals(_flag_on):
    p = _holder(9, skills=["SkillAtkKim1", _PREREQ])
    session, enemy = _session(p, rng=_ZeroRng())
    run_cuu_u_procs(session, p, enemy, 500)
    assert any("Đạo Pháp Thôn Phệ" in line for line in session.log)


# ── Dormancy ─────────────────────────────────────────────────────────────────


def test_dormant_without_levels():
    """Flag OFF / no stored levels — every cu_ flag stays zero."""
    p = build_player_combatant(_make_char(level=None), ["SkillAtkKim1", _PREREQ])
    assert p.cu_ma_khi_cap == 0
    assert p.cu_vl_follow_up_chance == 0.0
    assert not p.cu_ma_de_evolved
    session, enemy = _session(p, rng=_ZeroRng())
    run_cuu_u_procs(session, p, enemy, 500)
    assert p.ma_khi_stacks == 0
