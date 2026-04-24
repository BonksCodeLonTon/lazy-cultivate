"""Smoke tests for each elemental build flow after the combat.py refactor.

These exercise the paths that changed in the cleanup: propagate_stack_build
(burn/bleed/shock), _run_on_hit_procs, _apply_mana_gains, _apply_reactive_damage
(reflect + thorn), and the three _burst_* helpers.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    _propagate_stack_build,
)
from src.game.systems.combatant import Combatant


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def make_combatant(key: str = "p", **overrides) -> Combatant:
    """Minimal combatant seeded to non-zero HP/MP/ATK/MATK."""
    defaults = dict(
        name=key,
        hp=1_000, hp_max=1_000,
        mp=500, mp_max=500,
        spd=10, element=None,
        atk=100, matk=100, def_stat=20,
    )
    defaults.update(overrides)
    return Combatant(key=key, **defaults)


def make_session(player: Combatant, enemy: Combatant, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed),
        max_turns=5,
    )


# ── Fire / Burn build ──────────────────────────────────────────────────────
def test_burn_build_propagates_stack_cap_and_per_stack_pct():
    actor = make_combatant("a", burn_stack_cap=10, burn_per_stack_pct=0.02, dot_can_crit=True)
    target = make_combatant("t")
    _propagate_stack_build(actor, target, "burn")
    assert target.burn_stack_cap == 10
    assert target.burn_per_stack_pct == 0.02
    assert target.dot_can_crit is True
    # DoT source registration happens for burn
    assert "a" in target.dot_bonus_sources


def test_on_hit_burn_proc_applies_stack_and_debuff():
    actor = make_combatant("a", burn_on_hit_pct=1.0, burn_stack_cap=3)
    target = make_combatant("t")
    session = make_session(actor, target, seed=1)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.burn_stacks == 1
    assert target.has_effect(EffectKey.DEBUFF_THIEU_DOT)


def test_burst_burn_consumes_stacks_and_damages():
    actor = make_combatant("a", matk=200)
    target = make_combatant("t", hp=500, hp_max=500)
    target.add_burn_stack(3)
    target.apply_effect(EffectKey.DEBUFF_THIEU_DOT, 3)
    session = make_session(actor, target)
    session._burst_burn(actor, target, {"base_dmg": 100, "burst_per_stack_mult": 0.5})
    assert target.burn_stacks == 0
    assert not target.has_effect(EffectKey.DEBUFF_THIEU_DOT)
    assert target.hp < 500  # took damage


# ── Kim / Bleed build ──────────────────────────────────────────────────────
def test_bleed_build_propagates_heal_reduce():
    actor = make_combatant("a", bleed_stack_cap=8, bleed_heal_reduce=0.4)
    target = make_combatant("t")
    _propagate_stack_build(actor, target, "bleed")
    assert target.bleed_stack_cap == 8
    assert target.bleed_heal_reduce == 0.4
    assert "a" in target.dot_bonus_sources


def test_on_hit_bleed_applies_stack_and_debuff():
    actor = make_combatant("a", bleed_on_hit_pct=1.0)
    target = make_combatant("t")
    session = make_session(actor, target, seed=2)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.bleed_stacks == 1
    assert target.has_effect(EffectKey.DEBUFF_CHAY_MAU)


# ── Lôi / Shock build ──────────────────────────────────────────────────────
def test_shock_build_propagates_cap_and_per_stack():
    actor = make_combatant("a", shock_stack_cap=7, shock_per_stack_pct=0.08)
    target = make_combatant("t")
    _propagate_stack_build(actor, target, "shock")
    assert target.shock_stack_cap == 7
    assert target.shock_per_stack_pct == 0.08
    # shock does NOT propagate DoT bonus sources
    assert target.dot_bonus_sources == {}


def test_on_hit_shock_applies_stack_and_debuff():
    actor = make_combatant("a", shock_on_hit_pct=1.0)
    target = make_combatant("t")
    session = make_session(actor, target, seed=3)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.shock_stacks == 1
    assert target.has_effect(EffectKey.DEBUFF_SOC_DIEN)


# ── Phong / damage_bonus_from_evasion_pct includes SPD-derived evasion ──
def test_phong_damage_bonus_from_evasion_includes_spd_bonus():
    """SPD over the SPD_EVASION_BASELINE feeds eva_total alongside base evasion_rating."""
    from src.game.engine.damage import apply_damage_scaling, spd_evasion_bonus
    from src.game.constants.balance import SPD_EVASION_BASELINE

    fast_spd = SPD_EVASION_BASELINE + 30
    actor = make_combatant(
        "a",
        evasion_rating=100,
        damage_bonus_from_evasion_pct=0.5,
        spd=fast_spd,
    )
    spd_bonus = spd_evasion_bonus(fast_spd)
    assert spd_bonus > 0  # sanity — the chosen SPD does grant evasion

    base = 1_000
    out = apply_damage_scaling(base, actor, actor_mods={})
    expected_eva_total = 100 + spd_bonus
    expected_dmg = base + int(expected_eva_total * 0.5)
    assert out == expected_dmg


def test_phong_damage_bonus_from_evasion_no_spd_when_below_baseline():
    """Below baseline, SPD contributes nothing — only base evasion_rating counts."""
    from src.game.engine.damage import apply_damage_scaling

    actor = make_combatant(
        "a",
        evasion_rating=200,
        damage_bonus_from_evasion_pct=0.5,
        spd=1,  # well below baseline → spd_evasion_bonus = 0
    )
    out = apply_damage_scaling(1_000, actor, actor_mods={})
    assert out == 1_000 + int(200 * 0.5)


# ── Phong / Mark + Slow ────────────────────────────────────────────────────
def test_on_hit_mark_and_slow():
    actor = make_combatant("a", mark_on_hit_pct=1.0, slow_on_hit_pct=1.0)
    target = make_combatant("t")
    session = make_session(actor, target, seed=4)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.has_effect(EffectKey.DEBUFF_PHONG_AN)
    assert target.has_effect(EffectKey.DEBUFF_LAM_CHAM)


# ── Thổ / Stun + Shield burst ──────────────────────────────────────────────
def test_on_hit_stun_respects_hard_cc_immunity():
    actor = make_combatant("a", stun_on_hit_pct=1.0)
    target = make_combatant("t", immune_hard_cc=True)
    session = make_session(actor, target, seed=5)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert not target.has_effect(EffectKey.CC_STUN)


def test_on_hit_stun_lands_on_non_immune_target():
    actor = make_combatant("a", stun_on_hit_pct=1.0)
    target = make_combatant("t")
    session = make_session(actor, target, seed=6)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.has_effect(EffectKey.CC_STUN)


def test_paralysis_on_crit_fires_only_on_crit():
    actor = make_combatant("a", paralysis_on_crit=True)
    target = make_combatant("t")
    session = make_session(actor, target, seed=7)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert not target.has_effect(EffectKey.CC_STUN)
    session._run_on_hit_procs(actor, target, is_crit=True)
    assert target.has_effect(EffectKey.CC_STUN)


def test_burst_shield_consumes_and_damages():
    actor = make_combatant("a")
    actor.shield = 300
    target = make_combatant("t", hp=1_000, hp_max=1_000)
    session = make_session(actor, target)
    session._burst_shield(actor, target, {"burst_shield_mult": 2.0})
    assert actor.shield == 0
    assert target.hp < 1_000


# ── Thủy / Mana ────────────────────────────────────────────────────────────
def test_mana_gains_mp_leech_and_stack_accum():
    actor = make_combatant("a", mp=100, mp_max=500, mp_leech_pct=0.2,
                           mana_stack_per_attack=2, mana_stack_cap=5)
    target = make_combatant("t")
    session = make_session(actor, target)
    session._apply_mana_gains(actor, dmg=100)
    assert actor.mp == 100 + 20  # 20% of 100
    assert actor.mana_stacks == 2


def test_burst_mana_consumes_stacks_and_damages():
    actor = make_combatant("a", mp_max=500)
    actor.add_mana_stack(4)
    target = make_combatant("t", hp=1_000, hp_max=1_000)
    session = make_session(actor, target)
    session._burst_mana_stacks(actor, target, {"burst_per_mana_stack_mult": 0.1})
    assert actor.mana_stacks == 0
    assert target.hp < 1_000


# ── Reactive: reflect + thorn ──────────────────────────────────────────────
def test_reactive_reflect_damages_attacker():
    actor = make_combatant("a", hp=1_000, hp_max=1_000)
    target = make_combatant("t", reflect_pct=0.25)
    session = make_session(actor, target)
    session._apply_reactive_damage(actor, target, dmg=200)
    assert actor.hp < 1_000


def test_reactive_thorn_damages_attacker_but_not_fatal():
    actor = make_combatant("a", hp=10, hp_max=10, def_stat=0)
    target = make_combatant("t", thorn_pct=1.0)
    session = make_session(actor, target)
    session._apply_reactive_damage(actor, target, dmg=1_000)
    # thorn floors attacker at 1 HP, never kills
    assert actor.hp == 1


# ── build_player_combatant smoke — filtered asdict must populate all fields
def test_build_player_combatant_roundtrips_all_fields():
    """Verify the filtered asdict() copy preserves every shared field name."""
    from dataclasses import fields
    from src.game.models.character import Character, CharacterStats
    from src.game.systems.combat import build_player_combatant
    from src.game.systems.character_stats import compute_combat_stats

    char = Character(
        player_id=1, discord_id=1, name="Test",
        body_realm=1, body_level=1,
        qi_realm=1,   qi_level=1,
        formation_realm=1, formation_level=1,
        linh_can=[], stats=CharacterStats(),
    )
    cb = build_player_combatant(char, player_skill_keys=[])
    cs = compute_combat_stats(char)
    # Every CombatStats field that also exists on Combatant should have matching value.
    combatant_names = {f.name for f in fields(Combatant)}
    for f in fields(cs):
        if f.name not in combatant_names:
            continue
        cv = getattr(cb, f.name)
        sv = getattr(cs, f.name)
        # dicts compare by value
        assert cv == sv, f"field {f.name!r} drifted: combatant={cv!r} stats={sv!r}"


# ── Quang / Silence + Anti-Heal + Cleanse build ────────────────────────────
def test_on_hit_heal_reduce_applies_cat_dut():
    actor = make_combatant("a", heal_reduce_on_hit_pct=1.0)
    target = make_combatant("t")
    session = make_session(actor, target, seed=8)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.has_effect(EffectKey.DEBUFF_CAT_DUT)


def test_silence_on_crit_fires_only_on_crit():
    actor = make_combatant("a", silence_on_crit_pct=1.0)
    target = make_combatant("t")
    session = make_session(actor, target, seed=9)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert not target.has_effect(EffectKey.CC_MUTED)
    session._run_on_hit_procs(actor, target, is_crit=True)
    assert target.has_effect(EffectKey.CC_MUTED)


def test_silence_on_crit_respects_hard_cc_immunity():
    actor = make_combatant("a", silence_on_crit_pct=1.0)
    target = make_combatant("t", immune_hard_cc=True)
    session = make_session(actor, target, seed=10)
    session._run_on_hit_procs(actor, target, is_crit=True)
    assert not target.has_effect(EffectKey.CC_MUTED)


# ── Âm / Soul-Devour + Stat-Steal build ───────────────────────────────────
def test_soul_drain_shrinks_target_hp_max_and_grows_actor():
    actor = make_combatant("a", hp_max=1000, hp=1000, soul_drain_on_hit_pct=1.0)
    target = make_combatant("t", hp_max=10_000, hp=10_000)
    session = make_session(actor, target, seed=11)
    session._run_on_hit_procs(actor, target, is_crit=False)
    # Target hp_max shrank; actor hp_max grew
    assert target.hp_max < 10_000
    assert target.hp_max_drained > 0
    assert actor.hp_max > 1_000
    assert target.hp_max_original == 10_000


def test_soul_drain_respects_total_cap():
    """Once the per-fight cap is reached further procs do nothing."""
    from src.game.constants.balance import SOUL_DRAIN_CAP_PCT
    actor = make_combatant("a", soul_drain_on_hit_pct=1.0)
    target = make_combatant("t", hp_max=10_000, hp=10_000)
    session = make_session(actor, target, seed=12)
    # Enough iterations to guarantee the cap is hit
    for _ in range(200):
        session._run_on_hit_procs(actor, target, is_crit=False)
    cap = int(10_000 * SOUL_DRAIN_CAP_PCT)
    assert target.hp_max_drained == cap
    assert target.hp_max == 10_000 - cap


def test_stat_steal_transfers_from_target_to_actor():
    actor = make_combatant("a", atk=100, matk=100, def_stat=50, stat_steal_on_hit_pct=1.0)
    target = make_combatant("t", atk=200, matk=200, def_stat=100)
    session = make_session(actor, target, seed=13)
    session._run_on_hit_procs(actor, target, is_crit=False)
    # target stats decreased, actor stats increased by the same amount
    assert target.atk < 200
    assert target.matk < 200
    assert target.def_stat < 100
    assert actor.atk > 100
    assert actor.matk > 100
    assert actor.def_stat > 50


def test_stat_steal_respects_cap():
    from src.game.constants.balance import STAT_STEAL_CAP_PCT
    actor = make_combatant("a", stat_steal_on_hit_pct=1.0)
    target = make_combatant("t", atk=500, matk=500, def_stat=200)
    session = make_session(actor, target, seed=14)
    for _ in range(200):
        session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.stolen_atk == int(500 * STAT_STEAL_CAP_PCT)
    assert target.stolen_matk == int(500 * STAT_STEAL_CAP_PCT)
    assert target.stolen_def == int(200 * STAT_STEAL_CAP_PCT)


def test_crit_rating_vs_drained_only_applies_when_target_drained():
    from src.game.engine.damage import build_attack_stats
    actor = make_combatant("a", crit_rating=100, crit_rating_vs_drained=200)
    target = make_combatant("t")
    # Not drained yet — no bonus
    stats = build_attack_stats(actor, target, {})
    assert stats.crit_rating == 100
    # Once drained, bonus applies
    target.hp_max_drained = 500
    stats = build_attack_stats(actor, target, {})
    assert stats.crit_rating == 300


def test_skill_apply_soul_drain_fires_on_hit():
    """A skill listing ApplySoulDrain in effects triggers drain on hit."""
    actor = make_combatant("a")
    target = make_combatant("t", hp_max=10_000, hp=10_000)
    session = make_session(actor, target, seed=15)
    skill_data = {
        "effects": ["ApplySoulDrain"],
        "soul_drain_procs": 3,
    }
    session._apply_skill_effects(skill_data, actor, target, hit=True)
    # 3 procs → 3 * 1.5% of 10_000 = 450 hp_max drained
    assert target.hp_max_drained == 450
    assert target.hp_max == 10_000 - 450
    # actor grew by half of each drain
    assert actor.hp_max == 1_000 + (450 // 2)


def test_skill_apply_soul_drain_noop_when_evaded():
    """Drain should NOT fire on evaded hits."""
    actor = make_combatant("a")
    target = make_combatant("t", hp_max=10_000, hp=10_000)
    session = make_session(actor, target, seed=16)
    session._apply_skill_effects({"effects": ["ApplySoulDrain"]}, actor, target, hit=False)
    assert target.hp_max_drained == 0
    assert target.hp_max == 10_000


def test_skill_apply_stat_steal_multi_proc():
    actor = make_combatant("a", atk=100, matk=100, def_stat=50)
    target = make_combatant("t", atk=200, matk=200, def_stat=100)
    session = make_session(actor, target, seed=17)
    session._apply_skill_effects(
        {"effects": ["ApplyStatSteal"], "stat_steal_procs": 2},
        actor, target, hit=True,
    )
    # 2 procs * 4% of 200 atk/matk = 16 each, 2 * 4% of 100 def = 8
    assert target.stolen_atk == 16
    assert target.stolen_matk == 16
    assert target.stolen_def == 8
    assert actor.atk == 116
    assert target.atk == 184


def test_quang_cleanse_reverses_am_soul_drain_and_stat_steal():
    """A Quang-rooted target can slowly recover from Âm mutations."""
    from src.game.engine.linh_can_effects import quang

    actor = make_combatant(
        "a", linh_can=["quang"], cleanse_on_turn_pct=1.0,
        atk=100, matk=100, def_stat=50,
    )
    # Simulate Âm mutations already landed on the actor:
    actor.hp_max_original = 1000
    actor.hp_max_drained = 300
    actor.hp_max = 700  # already shrunk by 300
    actor.atk_original = 100
    actor.matk_original = 100
    actor.def_stat_original = 50
    actor.stolen_atk = 40
    actor.atk = 60
    actor.stolen_matk = 30
    actor.matk = 70
    actor.stolen_def = 10
    actor.def_stat = 40

    rng = random.Random(0)
    rng.random = lambda: 0.0  # type: ignore[method-assign]
    log: list[str] = []
    quang.try_cleanse(actor, rng, log)

    # HP max restored by 5% of original (50)
    assert actor.hp_max == 750
    assert actor.hp_max_drained == 250
    # Each stolen stat restored by 5% of its original
    assert actor.atk == 65 and actor.stolen_atk == 35
    assert actor.matk == 75 and actor.stolen_matk == 25
    # 5% of 50 is 2 (rounded down) but floored to 1 → actor.def_stat grows by ≥1
    assert actor.def_stat > 40 and actor.stolen_def < 10


def test_quang_cleanse_am_mutations_trigger_mp_restore_without_debuff():
    """Âm mutation restore counts as a successful cleanse — MP still restores."""
    from src.game.engine.linh_can_effects import quang

    actor = make_combatant(
        "a", linh_can=["quang"], cleanse_on_turn_pct=1.0,
        mp=100, mp_max=1000,
    )
    actor.hp_max_original = 1000
    actor.hp_max_drained = 100

    rng = random.Random(0)
    rng.random = lambda: 0.0  # type: ignore[method-assign]
    log: list[str] = []
    quang.try_cleanse(actor, rng, log)

    # Cleanse pulse fired via Âm-restore path → MP topped up by 5%
    assert actor.mp > 100


def test_buff_han_khi_aura_slows_target_on_hit():
    """BuffHanKhi's aura_on_hit(DebuffLamCham, 1.0) must apply Làm Chậm to
    the target on any successful hit from the buff holder."""
    actor = make_combatant("a")
    actor.apply_effect("BuffHanKhi", 3)
    target = make_combatant("t")
    session = make_session(actor, target, seed=42)
    session._run_on_hit_procs(actor, target, is_crit=False)
    assert target.has_effect(EffectKey.DEBUFF_LAM_CHAM)


def test_hoa_than_aura_applies_burn_on_hit():
    """BuffHoaThan's description promises 'xác suất gây Thiêu Đốt' — it now
    has an aura that rolls for DebuffThieuDot on hit."""
    actor = make_combatant("a")
    actor.apply_effect("BuffHoaThan", 3)
    target = make_combatant("t")
    # Force the 35 % proc chance to fire by stacking seeds until it triggers
    # at least once across a few hits — validates the aura is wired in.
    landed = False
    for seed in range(20):
        target.effects.clear()
        session = make_session(actor, target, seed=seed)
        session._run_on_hit_procs(actor, target, is_crit=False)
        if target.has_effect(EffectKey.DEBUFF_THIEU_DOT):
            landed = True
            break
    assert landed, "BuffHoaThan aura never applied Thiêu Đốt across 20 seeds"


def test_loi_than_aura_applies_paralysis_on_hit():
    """BuffLoiThan description promises per-hit paralysis chance."""
    actor = make_combatant("a")
    actor.apply_effect("BuffLoiThan", 3)
    target = make_combatant("t")
    landed = False
    for seed in range(30):
        target.effects.clear()
        session = make_session(actor, target, seed=seed)
        session._run_on_hit_procs(actor, target, is_crit=False)
        if target.has_effect(EffectKey.DEBUFF_TE_LIET):
            landed = True
            break
    assert landed, "BuffLoiThan aura never applied Tê Liệt across 30 seeds"


def test_han_khi_aura_respects_hard_cc_immunity():
    """Stun/silence-shaped auras must honor ``immune_hard_cc``. Làm Chậm is
    a soft debuff so it lands even on world bosses — this test documents
    that routing: soft debuffs bypass the hard-CC shield."""
    actor = make_combatant("a")
    actor.apply_effect("BuffHanKhi", 3)
    target = make_combatant("t", immune_hard_cc=True)
    session = make_session(actor, target, seed=43)
    session._run_on_hit_procs(actor, target, is_crit=False)
    # Làm Chậm is a soft slow — not a hard-CC — so still lands.
    assert target.has_effect(EffectKey.DEBUFF_LAM_CHAM)


def test_am_res_shred_applied_in_defense_stats():
    from src.game.engine.damage import build_defense_stats, spd_evasion_bonus
    actor = make_combatant("a", am_res_shred=0.2)
    target = make_combatant("t")
    target.resistances = {"am": 0.3}
    defs = build_defense_stats(target, {}, actor, spd_evasion_bonus)
    assert defs.resistances["am"] == pytest.approx(0.10)


def test_quang_cleanse_uses_cleanse_on_turn_pct_bonus():
    from src.game.engine.linh_can_effects import quang

    actor = make_combatant("a", linh_can=["quang"], cleanse_on_turn_pct=0.5)
    actor.apply_effect(EffectKey.DEBUFF_PHA_GIAP, 3)
    # With base 15% + 50% = 65%, rng value 0.2 → proc fires.
    rng = random.Random(0)
    rng.random = lambda: 0.2  # type: ignore[method-assign]
    log: list[str] = []
    quang.try_cleanse(actor, rng, log)
    assert not actor.has_effect(EffectKey.DEBUFF_PHA_GIAP)


def test_quang_cleanse_skips_when_no_debuffs():
    from src.game.engine.linh_can_effects import quang

    actor = make_combatant("a", linh_can=["quang"])
    rng = random.Random(0)
    rng.random = lambda: 0.0  # type: ignore[method-assign]
    log: list[str] = []
    quang.try_cleanse(actor, rng, log)
    assert log == []


def test_quang_barrier_on_cleanse_grants_shield():
    from src.game.engine.linh_can_effects import quang

    actor = make_combatant("a", linh_can=["quang"], matk=200, barrier_on_cleanse=True)
    actor.apply_effect(EffectKey.DEBUFF_PHA_GIAP, 3)
    rng = random.Random(0)
    rng.random = lambda: 0.0  # force cleanse
    log: list[str] = []
    quang.try_cleanse(actor, rng, log)
    assert actor.shield > 0
    assert any("Thánh Quang Hộ Thuẫn" in line for line in log)


def test_heal_can_crit_multiplies_restore():
    """heal_can_crit=True + forced crit roll → amount ×1.5 before clamp."""
    actor = make_combatant("a", hp=100, hp_max=1_000, heal_can_crit=True)
    target = make_combatant("t")
    session = make_session(actor, target, seed=11)
    session.rng.random = lambda: 0.0  # type: ignore[method-assign]
    applied = session._apply_heal(actor, amount=200)
    assert applied == 300  # 200 × 1.5
    assert actor.hp == 400


def test_heal_can_crit_skipped_when_flag_false():
    """heal_can_crit=False → no crit roll, heal returns raw amount."""
    actor = make_combatant("a", hp=100, hp_max=1_000, heal_can_crit=False)
    target = make_combatant("t")
    session = make_session(actor, target, seed=12)
    session.rng.random = lambda: 0.0  # type: ignore[method-assign]
    applied = session._apply_heal(actor, amount=200)
    assert applied == 200


def test_heal_crit_respects_bleed_heal_reduce():
    """Crit applies first, then bleed heal-reduction shrinks the crit value."""
    actor = make_combatant(
        "a", hp=100, hp_max=1_000,
        heal_can_crit=True,
        bleed_stacks=1, bleed_heal_reduce=0.5,
    )
    target = make_combatant("t")
    session = make_session(actor, target, seed=13)
    session.rng.random = lambda: 0.0  # type: ignore[method-assign]
    applied = session._apply_heal(actor, amount=200)
    # 200 × 1.5 = 300 → × (1 - 0.5) = 150
    assert applied == 150


def test_quang_res_shred_reduces_target_resistance():
    """quang_res_shred on attacker is picked up by build_defense_stats."""
    from src.game.engine.damage.combat_hit import build_defense_stats, spd_evasion_bonus

    attacker = make_combatant("a", quang_res_shred=0.2)
    defender = make_combatant("t", resistances={"quang": 0.5})
    stats = build_defense_stats(defender, target_mods={}, actor=attacker,
                                spd_evasion_bonus=spd_evasion_bonus)
    # 0.5 base - 0.2 shred = 0.3
    assert stats.resistances["quang"] == pytest.approx(0.3)


# ── End-to-end: run() on a realistic matchup still terminates ──────────────
def test_run_terminates_with_result():
    """Sanity: run() — which now delegates to step() — still completes."""
    # Use real skill so step path exercises _take_turn / damage pipeline
    attack_skill = "EnemyKim_T1"  # small base_dmg, low mp_cost
    actor = make_combatant("player", skill_keys=[attack_skill], mp=999, mp_max=999)
    target = make_combatant("enemy", hp=100, hp_max=100)
    session = CombatSession(
        player=actor, enemy=target,
        player_skill_keys=[attack_skill],
        rng=random.Random(42),
        max_turns=50,
    )
    result = session.run()
    assert result.reason in {
        CombatEndReason.PLAYER_WIN,
        CombatEndReason.PLAYER_DEAD,
        CombatEndReason.MAX_TURNS,
    }
    assert result.turns >= 1
