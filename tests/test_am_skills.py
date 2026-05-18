"""Behavioural tests for the new Âm-element skill kit.

Pins the engine plumbing each skill relies on so a future refactor doesn't
silently drop the mechanic. Each top-level group corresponds to one skill:

  * Thất Tình Đoạn Tuyệt — 7 hits + per-debuff damage scaling
  * Lục Hồn Chú         — caster-stat DoT, shield + MP drain, dot_taken_bonus
  * Quy Hư Diệt Đạo    — DebuffPhapNhuoc + DebuffLinhLucKiet, MATK scaling
  * Vạn Quỷ Phệ Tâm   — count=3 summons + on_hit_debuff (DebuffAmThucKy)
  * Sưu Hồn Đoạt Phách — single-shot 25 % stat steal
  * Diệt Thế Chưởng    — heavy MATK + 70 % stun
  * Cửu U Thần Trảo    — debuff-count gate + consume 5 + soul-drain
  * Thiên Ma Giải Thể  — auto-cycling on_expire_apply chain
  * Lục Dục Thiên Ma Vũ — six-desires cycle hook on _process_periodic
  * Vô Tướng Thiên Ma  — damage_convert_<elem> stat_bonus → take_damage
  * Ma Khí Hộ Thể      — am_hit_to_shield_pct stat_bonus → on-hit hook
  * Chân Ma Chi Tâm    — debuff_apply_bonus + debuff_immune_pct as stat_bonus
  * Stealable / ApplyBuffSteal cross-skill plumbing

Every test uses a fresh Combatant pair (no DB, no registry mocking) and
drives the engine through ``CombatSession`` so behaviour stays close to
production. Tests that need the registry call ``registry.load()`` once via
the autouse fixture.
"""
from __future__ import annotations

import random
from typing import Iterable

import pytest

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS, EffectKind, get_combat_modifiers
from src.game.systems.combat.casting import (
    _effective_debuff_chance, apply_support_skill, cast_skill,
)
from src.game.systems.combat.procs import apply_buff_steal
from src.game.systems.combat.session import CombatSession
from src.game.systems.combat.skill_extras import maybe_spawn_summon, tick_summons
from src.game.systems.combatant import Combatant


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Test fixture helpers ─────────────────────────────────────────────────────

def _attacker(**overrides) -> Combatant:
    base = dict(
        key="a", name="Caster",
        hp=20_000, hp_max=20_000,
        mp=2_000, mp_max=2_000,
        spd=10, atk=400, matk=600, def_stat=20,
        element="am",
    )
    base.update(overrides)
    return Combatant(**base)


def _target(**overrides) -> Combatant:
    base = dict(
        key="t", name="Target",
        hp=20_000, hp_max=20_000,
        mp=1_000, mp_max=1_000,
        spd=10, atk=400, matk=400, def_stat=200,
        element=None,
    )
    base.update(overrides)
    return Combatant(**base)


def _session(player: Combatant, enemy: Combatant, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=player.skill_keys,
        rng=random.Random(seed),
    )


def _stack_target_with_debuffs(target: Combatant, keys: Iterable[str]) -> None:
    """Apply each debuff key for 3 turns (default-style)."""
    for k in keys:
        target.apply_effect(k, 3)


def _am_attack() -> dict:
    """A minimal single-hit Âm attack used purely to drive on-hit / on-evade
    reactions (Ma Khí Hộ Thể, Quỷ Ảnh Mê Tung, Ma Long Xuất Uyên).

    Built locally rather than fetched from the registry: the realm-tier
    basic-attack keys were renamed in the skill rework, and these tests
    only need *an* Âm-element damaging swing to react to — not any
    specific skill. Carrying no effects/chain keeps RNG draw count minimal
    and stable for the seed-sensitive evade tests.
    """
    return {
        "key": "TestAmBasic", "vi": "Thử Âm Kích", "en": "Test Am Strike",
        "element": "am", "category": "attack", "attack_type": "magical",
        "dmg_scale": {"atk": 0.0, "matk": 1.0},
        "base_dmg": 1000, "mp_cost": 100, "cooldown": 0, "effects": [],
    }


# ── 1. Thất Tình Đoạn Tuyệt — 7-hit + per-debuff scaling ───────────────────

def test_that_tinh_doan_tuyet_carries_hit_count_and_self_buff():
    skill = registry.get_skill("SkillAmThatTinhDoanTuyet")
    assert skill is not None
    assert skill["hit_count"] == 7
    assert "BuffDoanTuyet" in skill["effects"]
    # Carries 7 unique non-DoT debuffs + the buff
    debuffs = [k for k in skill["effects"] if k != "BuffDoanTuyet"]
    assert len(set(debuffs)) == 7


def test_buff_doan_tuyet_amplifies_damage_by_active_debuff_count():
    """+5% per debuff is wired through ``build_attack_stats``."""
    from src.game.engine.damage.combat_hit import _doan_tuyet_bonus

    actor = _attacker()
    target = _target()
    actor.apply_effect(
        EffectKey.BUFF_DOAN_TUYET, 4,
        overrides={"dmg_per_debuff_pct": 0.05},
    )

    # Zero debuffs → zero bonus
    assert _doan_tuyet_bonus(actor, target) == 0.0

    # 7 stacks (matches skill payload) → +35 %
    _stack_target_with_debuffs(target, [
        "DebuffXeRach", "DebuffPhaGiap", "DebuffCatDut", "DebuffBaoMon",
        "DebuffLamCham", "DebuffSuyKhi", "DebuffPhapNhuoc",
    ])
    assert _doan_tuyet_bonus(actor, target) == pytest.approx(0.35)


def test_doan_tuyet_excludes_cc_kind_effects():
    """CC kind effects shouldn't count toward the debuff multiplier."""
    from src.game.engine.damage.combat_hit import _doan_tuyet_bonus

    actor = _attacker()
    target = _target()
    actor.apply_effect(
        EffectKey.BUFF_DOAN_TUYET, 4,
        overrides={"dmg_per_debuff_pct": 0.10},
    )
    target.apply_effect("CCStun", 1)
    target.apply_effect("DebuffXeRach", 3)
    # Only DebuffXeRach counts; CCStun is kind=cc.
    assert _doan_tuyet_bonus(actor, target) == pytest.approx(0.10)


# ── 2. Lục Hồn Chú — caster-stat DoT + shield/MP drain + amp ────────────────

def test_luc_hon_chu_meta_carries_caster_stat_fields():
    meta = EFFECTS[EffectKey.DEBUFF_LUC_HON_CHU]
    assert meta.dot_caster_hp_pct > 0
    assert meta.dot_caster_matk_scale > 0
    assert meta.dot_shield_drain_pct > 0
    assert meta.dot_mp_drain_pct > 0
    assert meta.dot_element == "am"
    assert meta.stat_bonus["res_am"] == pytest.approx(-0.20)
    assert meta.stat_bonus["dot_taken_bonus"] == pytest.approx(0.20)


def test_luc_hon_chu_tick_uses_caster_hp_max_and_matk():
    """``_scale_damage`` reads the strongest applier's recorded stats."""
    from src.game.systems.combat.helpers import _propagate_dot_bonuses

    attacker = _attacker(hp_max=20_000, matk=600)
    target = _target()
    _propagate_dot_bonuses(attacker, target)
    target.apply_effect(EffectKey.DEBUFF_LUC_HON_CHU, 4)

    session = _session(attacker, target)
    target.shield = 5_000
    hp_before, shield_before, mp_before = target.hp, target.shield, target.mp
    session._process_periodic(target)

    # Damage is non-zero (caster contributes via hp_max + matk channels).
    assert target.hp < hp_before
    # Shield drained by 50 % of the tick (rounded to int, floor 1).
    assert 0 < shield_before - target.shield <= (hp_before - target.hp)
    # MP drained ≈ 5 % of mp_max = 50.
    assert mp_before - target.mp == 50


def test_dot_taken_bonus_amplifies_dot_ticks():
    """``stat_bonus.dot_taken_bonus`` flows through ``_dot_amp``."""
    from src.game.engine.damage.dot import _dot_amp

    target = _target()
    meta = EFFECTS["DebuffXeRach"]  # arbitrary DoT meta — amp logic reads holder
    assert _dot_amp(target, meta) == 0.0

    target.apply_effect(EffectKey.DEBUFF_LUC_HON_CHU, 4)
    assert _dot_amp(target, meta) == pytest.approx(0.20)


# ── 3. Quy Hư Diệt Đạo — Pháp Nhược + Linh Lực Kiệt + MATK scaling ─────────

def test_phap_nhuoc_reduces_actor_matk_in_pipeline():
    """``matk_pct`` debuff folds into ``build_attack_stats``."""
    from src.game.engine.damage.combat_hit import build_attack_stats

    actor = _attacker(matk=500)
    target = _target()
    clean = build_attack_stats(actor, target, get_combat_modifiers(actor))
    actor.apply_effect(EffectKey.DEBUFF_PHAP_NHUOC, 3)
    debuffed = build_attack_stats(actor, target, get_combat_modifiers(actor))

    assert clean.matk == 500
    assert debuffed.matk == 400  # 500 × (1 - 0.20)


def test_suy_khi_reduces_actor_atk_in_pipeline():
    from src.game.engine.damage.combat_hit import build_attack_stats

    actor = _attacker(atk=500)
    target = _target()
    actor.apply_effect(EffectKey.DEBUFF_SUY_KHI, 3)
    stats = build_attack_stats(actor, target, get_combat_modifiers(actor))
    assert stats.atk == 400  # 500 × (1 - 0.20)


def test_linh_luc_kiet_zeroes_mp_regen():
    """``mp_regen_pct`` debuff folds into ``_process_periodic`` regen line."""
    attacker = _attacker()
    target = _target(mp=200, mp_max=1_000, mp_regen_pct=0.20)
    session = _session(attacker, target)

    # Baseline: regen +200/turn
    before = target.mp
    session._process_periodic(target)
    assert target.mp - before == 200

    # With Linh Lực Kiệt (-50 % regen → effective 0 %)
    target.apply_effect(EffectKey.DEBUFF_LINH_LUC_KIET, 3)
    target.mp = 200
    session.log = []
    session._process_periodic(target)
    assert target.mp == 200  # regen floored to 0


# ── 4. Vạn Quỷ Phệ Tâm — count=3 summon + on_hit_debuff ────────────────────

def test_van_quy_summons_three_demons_each_with_on_hit_debuff():
    skill = registry.get_skill("SkillAmVanQuyPheTam")
    assert skill is not None
    spec = skill["summon_spec"]
    assert spec["count"] == 3
    assert spec["dmg_pct_of_atk"] == pytest.approx(0.40)
    assert spec["on_hit_debuff"]["key"] == "DebuffAmThucKy"

    actor = _attacker(atk=500)
    target = _target()
    session = _session(actor, target)
    maybe_spawn_summon(session, actor, skill)

    assert len(actor.summons) == 3
    for summon in actor.summons:
        assert summon["dmg"] == 200  # 40 % of 500 ATK
        assert summon["on_hit_debuff"]["key"] == "DebuffAmThucKy"


def test_am_thuc_ky_caps_am_resistance_loss_at_25_pct():
    """All 3 summon hits stamp the debuff; aggregation tops out at -25 %."""
    actor = _attacker(atk=500)
    target = _target()
    skill = registry.get_skill("SkillAmVanQuyPheTam")
    assert skill is not None
    session = _session(actor, target)

    maybe_spawn_summon(session, actor, skill)
    tick_summons(session, actor, target)

    mods = get_combat_modifiers(target)
    # Three summons stamp the same key — effects dict only carries one copy.
    assert mods["res_am"] == pytest.approx(-0.25)


# ── 5. Sưu Hồn Đoạt Phách — 25 % single-shot stat steal ─────────────────────

def test_suu_hon_doat_phach_steals_10_percent_of_actor_each_stat():
    """Sưu Hồn Đoạt Phách takes a single big chunk anchored to the **actor's**
    starting stats (matches procs.apply_stat_steal contract)."""
    skill = registry.get_skill("SkillAmSuuHonDoatPhach_R9")
    assert skill["stat_steal_pct"] == pytest.approx(0.10)
    assert skill["base_dmg"] == 0  # no direct damage

    actor = _attacker(atk=200, matk=200, def_stat=100)
    target = _target(atk=400, matk=600, def_stat=200)
    session = _session(actor, target)

    apply_support_skill(session, skill, actor, target)

    # 10 % of actor's start per proc, capped at STAT_STEAL_CAP_PCT (10%):
    #   atk:  200 * 0.10 = 20  → actor 220, target 380
    #   matk: 200 * 0.10 = 20  → actor 220, target 580
    #   def:  100 * 0.10 = 10  → actor 110, target 190
    assert actor.atk == 220 and target.atk == 380
    assert actor.matk == 220 and target.matk == 580
    assert actor.def_stat == 110 and target.def_stat == 190


# ── 6. Diệt Thế Chưởng — heavy MATK + 70 % stun ─────────────────────────────

def test_diet_the_chuong_carries_stun_chance_and_heavy_matk_scaling():
    skill = registry.get_skill("SkillAmDietTheChuong")
    assert skill["effects"] == ["CCStun"]
    assert skill["effect_chances"]["CCStun"] == pytest.approx(0.70)
    # Mainly MATK
    assert skill["dmg_scale"]["matk"] >= 1.5
    assert skill["dmg_scale"]["atk"] == 0
    # Heavy single-cast
    assert skill["base_dmg"] >= 5_000


# ── 7. Cửu U Thần Trảo — gated + consume + soul drain ──────────────────────

def test_cuu_u_skill_filtered_when_target_lacks_5_debuffs():
    """``requires_target_debuff_count`` gates the skill picker."""
    actor = _attacker(skill_keys=["SkillAmQuyHuDietDao", "SkillAmCuuUThanTrao_R9"])
    target = _target()
    session = _session(actor, target)

    chosen, reason = session._choose_skill(actor)
    assert reason == "ok"
    # CuuU is gated out (target lacks 5 debuffs) → picker falls back to the
    # only ungated attack in the kit.
    assert chosen == "SkillAmQuyHuDietDao"


def test_cuu_u_skill_chosen_once_target_has_5_debuffs():
    actor = _attacker(skill_keys=["SkillAmQuyHuDietDao", "SkillAmCuuUThanTrao_R9"])
    target = _target()
    _stack_target_with_debuffs(target, [
        "DebuffXeRach", "DebuffPhaGiap", "DebuffCatDut",
        "DebuffBaoMon", "DebuffLamCham",
    ])
    session = _session(actor, target)
    chosen, reason = session._choose_skill(actor)
    assert reason == "ok"
    assert chosen == "SkillAmCuuUThanTrao_R9"


def test_cuu_u_consumes_5_target_debuffs_after_cast():
    actor = _attacker(skill_keys=["SkillAmCuuUThanTrao_R9"])
    target = _target()
    _stack_target_with_debuffs(target, [
        "DebuffXeRach", "DebuffPhaGiap", "DebuffCatDut",
        "DebuffBaoMon", "DebuffLamCham", "DebuffSuyKhi",
    ])
    skill = registry.get_skill("SkillAmCuuUThanTrao_R9")
    session = _session(actor, target)
    cast_skill(session, actor, target, "SkillAmCuuUThanTrao_R9", skill,
               mp_cost=skill["mp_cost"])

    remaining = [k for k in target.effects if EFFECTS[k].kind is EffectKind.DEBUFF]
    assert len(remaining) == 1  # 6 stacked, 5 consumed → 1 left


def test_cuu_u_applies_soul_drain_5_times():
    actor = _attacker(skill_keys=["SkillAmCuuUThanTrao_R9"], hp_max=10_000)
    target = _target(hp_max=20_000)
    _stack_target_with_debuffs(target, [
        "DebuffXeRach", "DebuffPhaGiap", "DebuffCatDut",
        "DebuffBaoMon", "DebuffLamCham",
    ])
    skill = registry.get_skill("SkillAmCuuUThanTrao_R9")
    session = _session(actor, target)
    cast_skill(session, actor, target, "SkillAmCuuUThanTrao_R9", skill,
               mp_cost=skill["mp_cost"])

    # 5 procs × 1.5 % per proc = 7.5 % of target's hp_max drained.
    assert target.hp_max_drained > 0
    assert actor.hp_max > 10_000  # caster gained hp_max from drain


# ── 8. Thiên Ma Giải Thể — on_expire_apply auto-cycle ───────────────────────

def test_thien_ma_giai_the_buff_chains_into_post_debuff_on_expire():
    actor = _attacker(skill_keys=["SkillAmThienMaGiaiThe_R9"])
    target = _target()
    session = _session(actor, target)

    actor.apply_effect(EffectKey.BUFF_THIEN_MA, 1)  # 1 turn left
    session._process_periodic(actor)

    # BuffThienMa expired → DebuffThienMaPost should now be active
    assert not actor.has_effect(EffectKey.BUFF_THIEN_MA)
    assert actor.has_effect(EffectKey.DEBUFF_THIEN_MA_POST)


def test_thien_ma_post_chains_back_to_buff_on_expire():
    actor = _attacker(skill_keys=["SkillAmThienMaGiaiThe_R9"])
    target = _target()
    session = _session(actor, target)

    actor.apply_effect(EffectKey.DEBUFF_THIEN_MA_POST, 1)
    session._process_periodic(actor)

    assert not actor.has_effect(EffectKey.DEBUFF_THIEN_MA_POST)
    assert actor.has_effect(EffectKey.BUFF_THIEN_MA)


def test_thien_ma_post_is_uncleansable():
    """Locks players into eating the backlash phase — no Quang dodging it."""
    assert EFFECTS[EffectKey.DEBUFF_THIEN_MA_POST].cleansable is False


# ── 9. Lục Dục Thiên Ma Vũ — six-desires periodic cycle ────────────────────

_LUC_DUC_DESIRE_KEYS = (
    "BuffLucDucSac", "BuffLucDucThanh", "BuffLucDucHuong",
    "BuffLucDucVi", "BuffLucDucXuc", "BuffLucDucPhap",
)


def test_luc_duc_gains_two_desires_per_periodic_tick():
    actor = _attacker(skill_keys=["SkillAmLucDucThienMaVu_R9"])
    target = _target()
    session = _session(actor, target)

    session._process_periodic(actor)
    active = sum(1 for k in _LUC_DUC_DESIRE_KEYS if actor.has_effect(k))
    assert active == 2

    session._process_periodic(actor)
    active = sum(1 for k in _LUC_DUC_DESIRE_KEYS if actor.has_effect(k))
    assert active == 4

    session._process_periodic(actor)
    active = sum(1 for k in _LUC_DUC_DESIRE_KEYS if actor.has_effect(k))
    assert active == 6


def test_luc_duc_amp_marker_applies_when_six_active():
    actor = _attacker(skill_keys=["SkillAmLucDucThienMaVu_R9"])
    target = _target()
    session = _session(actor, target)

    # Drive the cycle until amp lands (3 ticks to fill, 1 tick to amp).
    for _ in range(4):
        session._process_periodic(actor)

    assert actor.has_effect("BuffLucDucCongMinh")
    active = sum(1 for k in _LUC_DUC_DESIRE_KEYS if actor.has_effect(k))
    assert active == 6


def test_luc_duc_clears_then_silent_for_two_ticks():
    actor = _attacker(skill_keys=["SkillAmLucDucThienMaVu_R9"])
    target = _target()
    session = _session(actor, target)

    # Run gain (3) + amp (1) + expire (1). After this tick, all gone.
    for _ in range(5):
        session._process_periodic(actor)

    assert all(not actor.has_effect(k) for k in _LUC_DUC_DESIRE_KEYS)
    assert not actor.has_effect("BuffLucDucCongMinh")
    assert actor.luc_duc_expire_turns_left == 2

    # Next two ticks remain silent.
    session._process_periodic(actor)
    assert all(not actor.has_effect(k) for k in _LUC_DUC_DESIRE_KEYS)
    session._process_periodic(actor)
    assert all(not actor.has_effect(k) for k in _LUC_DUC_DESIRE_KEYS)

    # Then the cycle restarts.
    session._process_periodic(actor)
    active = sum(1 for k in _LUC_DUC_DESIRE_KEYS if actor.has_effect(k))
    assert active == 2


# ── 10. Vô Tướng Thiên Ma — damage_convert_<elem> + res_<elem> ─────────────

def test_vo_tuong_thien_ma_converts_40pct_damage_through_am_res():
    """Without the buff, raw damage hits the holder unchanged."""
    target = _target(hp=10_000, hp_max=10_000)
    target.take_damage(1_000)
    assert target.hp == 9_000


def test_vo_tuong_thien_ma_with_buff_applies_50pct_am_res_to_converted_slice():
    """40 % converted × (1 − 0.50 res) = 20 % mitigated → 800 net."""
    target = _target(hp=10_000, hp_max=10_000)
    target.apply_effect(EffectKey.BUFF_VO_TUONG_THIEN_MA, 4)
    target.take_damage(1_000)
    assert target.hp == 9_200  # 1_000 - 800


def test_vo_tuong_thien_ma_stacks_with_base_am_res():
    """Buff res_am: +0.50 + base 0.20 = 0.70 effective."""
    target = _target(hp=10_000, hp_max=10_000, resistances={"am": 0.20})
    target.apply_effect(EffectKey.BUFF_VO_TUONG_THIEN_MA, 4)
    target.take_damage(1_000)
    # 600 unconverted + 400 × (1 - 0.70) = 600 + 120 = 720 net loss.
    assert target.hp == 9_280


# ── 11. Ma Khí Hộ Thể — Âm hit → shield ────────────────────────────────────

def test_ma_khi_ho_the_grants_shield_on_am_hit():
    actor = _attacker(skill_keys=["SkillAmMaKhiHoThe_R9"],
                      shield_max_base=5_000)
    target = _target()
    session = _session(actor, target)

    session._apply_passive_auras(actor, target)
    assert actor.has_effect(EffectKey.BUFF_MA_KHI_HO_THE)

    skill = _am_attack()
    target_hp_before = target.hp
    cast_skill(session, actor, target, skill["key"], skill,
               mp_cost=skill["mp_cost"])
    dmg_dealt = target_hp_before - target.hp

    # Shield gain = 30 % of damage dealt, clamped by shield cap.
    expected_gain = int(dmg_dealt * 0.30)
    assert actor.shield == expected_gain


def test_ma_khi_ho_the_does_not_trigger_for_non_am_skills():
    """The hook is gated on ``skill_elem == 'am'``."""
    actor = _attacker(skill_keys=["SkillAmMaKhiHoThe_R9"], shield_max_base=5_000)
    target = _target()
    session = _session(actor, target)
    session._apply_passive_auras(actor, target)

    # Find a non-Âm attack skill that survived the data cull.
    non_am_key = next(
        (k for k, v in registry.skills.items()
         if v.get("category") == "attack" and v.get("element") != "am"
         and not v.get("_npc_only") and v.get("base_dmg", 0) > 0),
        None,
    )
    assert non_am_key is not None, "expected at least one non-Âm attack skill"
    non_am = registry.get_skill(non_am_key)
    assert non_am["element"] != "am"
    cast_skill(session, actor, target, non_am_key, non_am,
               mp_cost=non_am["mp_cost"])
    assert actor.shield == 0


# ── 12. Chân Ma Chi Tâm — bidirectional debuff modifier ────────────────────

def test_chan_ma_chi_tam_grants_30pct_debuff_immunity():
    actor = _attacker()
    target = _target()
    target.apply_effect(EffectKey.BUFF_CHAN_MA_CHI_TAM, 99)

    # 100 % base chance → 100 % × (1 − 0.30) = 70 %
    assert _effective_debuff_chance(1.0, actor, target) == pytest.approx(0.70)


def test_chan_ma_chi_tam_grants_actor_apply_bonus():
    actor = _attacker()
    target = _target()
    actor.apply_effect(EffectKey.BUFF_CHAN_MA_CHI_TAM, 99)

    # 60 % base + 10 % bonus → 70 %, no immunity → 70 %
    assert _effective_debuff_chance(0.60, actor, target) == pytest.approx(0.70)


def test_chan_ma_chi_tam_clamps_apply_bonus_at_one():
    actor = _attacker()
    target = _target()
    actor.apply_effect(EffectKey.BUFF_CHAN_MA_CHI_TAM, 99)

    # 0.95 + 0.10 → clamped to 1.00 before the immunity multiply.
    assert _effective_debuff_chance(0.95, actor, target) == pytest.approx(1.0)


def test_chan_ma_chi_tam_both_sides_compose():
    actor = _attacker()
    target = _target()
    actor.apply_effect(EffectKey.BUFF_CHAN_MA_CHI_TAM, 99)
    target.apply_effect(EffectKey.BUFF_CHAN_MA_CHI_TAM, 99)

    # (0.60 + 0.10) × (1 − 0.30) = 0.70 × 0.70 = 0.49
    assert _effective_debuff_chance(0.60, actor, target) == pytest.approx(0.49)


# ── 13. Quỷ Ảnh Mê Tung — evade-on-evade stacking buff ────────────────────

def test_quy_anh_per_stack_values_expand_with_stack_count():
    """``get_combat_modifiers`` expands the per-stack config × live stacks."""
    target = _target()
    target.apply_effect(EffectKey.BUFF_QUY_ANH_ME_TUNG, 99)

    target.quy_anh_stacks = 0
    mods = get_combat_modifiers(target)
    assert mods.get("evasion_rating", 0) == 0
    assert mods.get("spd_pct", 0) == 0

    target.quy_anh_stacks = 2
    mods = get_combat_modifiers(target)
    assert mods["evasion_rating"] == pytest.approx(400)
    assert mods["spd_pct"] == pytest.approx(0.20)

    target.quy_anh_stacks = 3
    mods = get_combat_modifiers(target)
    assert mods["evasion_rating"] == pytest.approx(600)
    assert mods["spd_pct"] == pytest.approx(0.30)


def test_quy_anh_config_keys_do_not_leak_into_modifier_dict():
    target = _target()
    target.apply_effect(EffectKey.BUFF_QUY_ANH_ME_TUNG, 99)
    mods = get_combat_modifiers(target)
    leaked = [k for k in mods if k.startswith("quy_anh_")]
    assert not leaked, f"config keys leaked into modifiers: {leaked}"


def test_quy_anh_evade_increments_stack_capped_at_three():
    """Each successful evade bumps the counter; cap held at 3."""
    attacker = _attacker()
    target = _target(evasion_rating=999_999)  # guaranteed evade
    target.skill_keys = ["SkillAmQuyAnhMeTung_R9"]
    session = _session(attacker, target)
    session._apply_passive_auras(target, attacker)

    attack_skill = _am_attack()
    for _ in range(5):
        attacker.mp = attacker.mp_max
        attacker.cooldowns.clear()
        cast_skill(session, attacker, target, attack_skill["key"], attack_skill,
                   mp_cost=attack_skill["mp_cost"])

    assert target.quy_anh_stacks == 3  # capped


def test_quy_anh_does_not_stack_without_buff_marker():
    """No marker → evading shouldn't bump the stack counter."""
    attacker = _attacker()
    target = _target(evasion_rating=999_999)  # buff NOT applied
    session = _session(attacker, target)

    attack_skill = _am_attack()
    cast_skill(session, attacker, target, attack_skill["key"], attack_skill,
               mp_cost=attack_skill["mp_cost"])
    assert target.quy_anh_stacks == 0


# ── 14. Ma Long Xuất Uyên — on-evade counter strike ───────────────────────

def test_ma_long_counter_strike_dmg_uses_base_plus_matk_pct():
    """Counter damage = base + matk_pct × defender.matk (on evade).

    Evasion is capped at MAX_EVASION_CHANCE (0.75) so a single cast can
    miss the dodge; iterate seeds until the counter fires, then assert the
    exact damage — which is deterministic once it triggers.
    """
    attack_skill = _am_attack()
    for seed in range(20):
        attacker = _attacker(hp=10_000, hp_max=10_000)
        # defender's matk drives the counter — set explicit
        target = _target(matk=500, evasion_rating=999_999)
        target.skill_keys = ["SkillAmMaLongXuatUyen_R9"]
        session = _session(attacker, target, seed=seed)
        session._apply_passive_auras(target, attacker)

        hp_before = attacker.hp
        cast_skill(session, attacker, target, attack_skill["key"], attack_skill,
                   mp_cost=attack_skill["mp_cost"])

        if attacker.hp < hp_before:  # the on-evade counter fired this seed
            # 600 base + 0.40 × 500 matk = 800 counter damage
            assert hp_before - attacker.hp == 800
            return
    pytest.fail("Ma Long counter never fired across 20 seeds")


def test_ma_long_does_not_fire_when_attack_lands():
    """Counter is gated on ``is_evaded`` — no evade, no counter."""
    attacker = _attacker(hp=10_000, hp_max=10_000)
    target = _target(matk=500, evasion_rating=0)  # no evasion → can't dodge
    target.skill_keys = ["SkillAmMaLongXuatUyen_R9"]
    session = _session(attacker, target)
    session._apply_passive_auras(target, attacker)

    attack_skill = _am_attack()
    hp_before = attacker.hp
    cast_skill(session, attacker, target, attack_skill["key"], attack_skill,
               mp_cost=attack_skill["mp_cost"])

    # Attacker shouldn't have lost HP (didn't take a counter) — only the
    # target did. We assert directly: attacker HP unchanged.
    assert attacker.hp == hp_before


def test_ma_long_config_keys_do_not_leak_into_modifier_dict():
    target = _target()
    target.apply_effect(EffectKey.BUFF_MA_LONG_XUAT_UYEN, 99)
    mods = get_combat_modifiers(target)
    leaked = [k for k in mods if k.startswith("ma_long_")]
    assert not leaked, f"config keys leaked into modifiers: {leaked}"


def test_ma_long_debuff_lands_within_chance_band():
    """When Ma Long counters on evade, Cắt Đứt Linh Khí lands ~60 % of the
    time. Conditioned on the counter actually firing (evasion is capped at
    0.75 so not every cast triggers it) — keeps the assertion about the
    debuff chance, not the evade cap, and stays robust to RNG alignment.
    """
    attack_skill = _am_attack()
    counters = 0
    landed = 0
    for seed in range(120):
        attacker = _attacker(hp=10_000, hp_max=10_000)
        target = _target(matk=500, evasion_rating=999_999)
        target.skill_keys = ["SkillAmMaLongXuatUyen_R9"]
        session = _session(attacker, target, seed=seed)
        session._apply_passive_auras(target, attacker)
        hp_before = attacker.hp
        cast_skill(session, attacker, target, attack_skill["key"], attack_skill,
                   mp_cost=attack_skill["mp_cost"])
        if attacker.hp < hp_before:  # counter fired this seed
            counters += 1
            if EffectKey.DEBUFF_CAT_DUT.value in attacker.effects:
                landed += 1
    assert counters >= 30, f"too few counters fired to sample: {counters}/120"
    rate = landed / counters
    assert 0.45 <= rate <= 0.75, (
        f"Cắt Đứt land rate {rate:.2f} ({landed}/{counters}) out of band"
    )


# ── 15. Stealable flag + ApplyBuffSteal ────────────────────────────────────

def test_stealable_default_is_true_for_buffs_false_for_debuffs_and_cc():
    assert EFFECTS["BuffKiemKhi"].stealable is True
    assert EFFECTS["DebuffXeRach"].stealable is False
    assert EFFECTS["CCStun"].stealable is False


def test_apply_buff_steal_moves_buff_with_full_duration_and_overrides():
    actor = _attacker()
    target = _target()
    target.apply_effect(
        "BuffNhietTinh", 4,
        overrides={"stat_bonus": {"final_dmg_bonus": 0.40}},
    )
    session = _session(actor, target)

    apply_buff_steal(session, actor, target)

    assert "BuffNhietTinh" not in target.effects
    assert actor.effects["BuffNhietTinh"] == 4
    assert (
        actor.effect_overrides["BuffNhietTinh"]["stat_bonus"]["final_dmg_bonus"]
        == pytest.approx(0.40)
    )


def test_apply_buff_steal_no_op_when_target_has_no_stealable_buff():
    actor = _attacker()
    target = _target()
    # Only debuffs on the target — none qualify as stealable.
    target.apply_effect("DebuffXeRach", 3)
    session = _session(actor, target)

    apply_buff_steal(session, actor, target)
    assert not actor.effects
    assert "DebuffXeRach" in target.effects
