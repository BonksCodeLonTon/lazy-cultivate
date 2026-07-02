"""v12 constitution — Bắc Minh Băng Phách Thể (Northern Darkness Ice Soul, Thủy).

The 2nd Thủy body: a freeze/MP-drain CONTROL disruptor (where Huyền Thủy Trường
Sinh was a tidal tank). L1 bidirectional cold aura (slow + multi-hit shave on
attack OR being attacked), L3 45% freeze + frozen auto-crit, L6 bidirectional
MP-drain→heal that banks Hàn Khí, L9 heal-reduce (−90% regen, 40%/70%-vs-frozen)
plus a Hàn Khí burst at 9 stacks. Three new effects: DebuffReduceHitCount,
DebuffCucHan, plus the BuffMpDrainToHeal mechanic. NO revive.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, regen_reduce_pct
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.procs import run_bac_minh_procs
from src.game.systems.constitution_process import (
    effective_effects, effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_BacMinhBangPhach"
_SKILL = "SkillAtkLietDiemPhanThienChuong_R7"
_MULTI = "SkillKimTripleStrike"  # hit_count 3
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None, skill: str = _SKILL) -> Character:
    return Character(
        player_id=1, discord_id=1, name="BangPhachTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["thuy"], linh_can_levels={"thuy": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9, mp: int = 100_000):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.mp = e.mp_max = mp
    e.resistances = {}
    e.evasion_rating = 0
    e.spd = 0
    return e


def _player(level: int, skill: str = _SKILL):
    return build_player_combatant(_make_char(level, skill), player_skill_keys=[skill])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[player.skill_keys[0]],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "thuy"
    assert data["rarity"] == "legendary"
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    # All mechanic config lives in the milestones (process-gated) so flag-off is
    # a clean even-stat body. L1≠flat by design.
    assert "bm_cold_aura_enabled" not in data["stat_bonuses"]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {"bm_cold_aura_enabled": True}


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffCuuAmHanKhi"]
    assert effective_effects(data, 9) == [
        "BuffCuuAmHanKhi", "BuffBangPhach",
        "BuffBacMinhThonThuc", "BuffCucHanPhongAn",
    ]


def test_new_effects_registered() -> None:
    for key in ("DebuffReduceHitCount", "DebuffCucHan"):
        assert EFFECTS.get(key) is not None
    assert EFFECTS["DebuffCucHan"].stat_bonus.get("regen_reduce_pct") == 0.90


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    assert _player(1).bm_cold_aura_enabled is True
    assert _player(1).freeze_on_skill_chance < 0.45
    # Freeze rides the GENERIC freeze_on_skill_chance lane (additive with
    # any linh-căn contribution).
    assert _player(3).freeze_on_skill_chance >= 0.45
    assert _player(6).bm_mp_drain_pct == pytest.approx(0.08)
    assert _player(6).bm_han_khi_cap == 9
    assert _player(9).bm_heal_reduce_chance == pytest.approx(0.40)
    assert _player(9).bm_heal_reduce_vs_frozen_chance == pytest.approx(0.70)
    assert _player(9).bm_burst_freeze_turns == 3


# ── 2. L1 Cửu Âm Hàn Khí — bidirectional cold aura ──────────────────────────


def test_l1_cold_aura_on_attack(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    run_bac_minh_procs(session, player, enemy)  # player attacks
    assert enemy.has_effect("DebuffLamCham")
    assert enemy.has_effect("DebuffReduceHitCount")


def test_l1_cold_aura_bidirectional_on_defense(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    run_bac_minh_procs(session, enemy, player)  # enemy attacks the holder
    # The holder's cold aura lashes back at the attacker.
    assert enemy.has_effect("DebuffLamCham")
    assert enemy.has_effect("DebuffReduceHitCount")


def test_reduce_hit_count_shaves_multihit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    skill_data = registry.get_skill(_MULTI)
    assert skill_data and int(skill_data.get("hit_count", 1)) == 3
    mp_cost = skill_data.get("mp_cost", 0)

    def _cast_dmg(with_debuff: bool) -> int:
        player = _player(1, skill=_MULTI)
        player.crit_rating = 99999
        player.bm_cold_aura_enabled = False  # isolate: no aura interference
        enemy = _enemy()
        if with_debuff:
            player.apply_effect("DebuffReduceHitCount", 2)
        session = _session(player, enemy)
        session.rng.random = lambda: 0.0
        player.mp = max(player.mp, mp_cost + 1)
        hp0 = enemy.hp
        cast_skill(session, player, enemy, _MULTI, skill_data, mp_cost)
        return hp0 - enemy.hp

    full = _cast_dmg(False)   # 3 hits
    shaved = _cast_dmg(True)  # 2 hits
    assert shaved < full
    assert shaved == pytest.approx(full * 2 / 3, rel=0.05)


# ── 3. L3 Băng Phách — freeze on attack ─────────────────────────────────────


def test_l3_freeze_fires(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # < 0.45 → freeze
    # Freeze rides the GENERIC freeze_on_skill_chance lane (run_on_hit_procs).
    player.crit_rating = 0
    skill = registry.get_skill(_SKILL)
    cast_skill(session, player, enemy, _SKILL, dict(skill), 0)
    assert enemy.has_effect("DebuffDongBang")


def test_l3_freeze_misses_on_high_roll(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # ≥ 0.45 → no freeze
    player.crit_rating = 0
    skill = registry.get_skill(_SKILL)
    cast_skill(session, player, enemy, _SKILL, dict(skill), 0)
    assert not enemy.has_effect("DebuffDongBang")


# ── 4. L6 Bắc Minh Thôn Thực — MP drain → heal + Hàn Khí ────────────────────


def test_l6_mp_drain_heals_and_banks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.hp = player.hp_max - 50_000  # room to heal
    enemy = _enemy(mp=100_000)
    session = _session(player, enemy)
    hp_before, mp_before = player.hp, enemy.mp
    run_bac_minh_procs(session, player, enemy)
    drained = mp_before - enemy.mp
    assert drained == int(100_000 * 0.08)               # 8% MP
    assert player.hp - hp_before == int(drained * 0.50)  # heal 50% of drained
    assert player.han_khi_stacks == 1
    assert player.han_khi_mp_drained_total == drained


def test_l6_mp_drain_bidirectional(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    enemy.mp = 50_000
    mp_before = enemy.mp
    run_bac_minh_procs(session, enemy, player)  # holder is the defender
    assert enemy.mp < mp_before  # holder drained the attacker on the way in
    assert player.han_khi_stacks == 1


# ── 5. L9 Cực Hàn Phong Ấn — heal-reduce + DebuffCucHan ─────────────────────


def test_l9_heal_reduce_applies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.50  # ≥0.40 base, but <0.70 vs frozen
    enemy.apply_effect("DebuffDongBang", 2)  # frozen → 70% gate
    run_bac_minh_procs(session, player, enemy)
    assert enemy.has_effect("DebuffCucHan")


def test_cuc_han_reduces_regen_90pct(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    enemy = _enemy()
    enemy.hp_regen_pct = 0.10
    enemy.hp = enemy.hp_max // 2
    player = _player(9)
    session = _session(player, enemy)

    enemy.hp = enemy.hp_max // 2
    session._process_periodic(enemy)
    healed_full = enemy.hp - enemy.hp_max // 2

    enemy.hp = enemy.hp_max // 2
    enemy.apply_effect("DebuffCucHan", 2)
    assert regen_reduce_pct(enemy) == pytest.approx(0.90)
    session._process_periodic(enemy)
    healed_locked = enemy.hp - enemy.hp_max // 2

    assert healed_locked == pytest.approx(healed_full * 0.10, rel=0.05)


# ── 6. Hàn Khí burst at 9 stacks ────────────────────────────────────────────


def test_han_khi_burst_at_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    enemy.shield = 0
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # suppress freeze/heal-reduce rolls
    player.han_khi_stacks = 9
    player.han_khi_mp_drained_total = 200_000
    hp_before = enemy.hp
    run_bac_minh_procs(session, player, enemy)
    dealt = hp_before - enemy.hp
    assert dealt >= int(200_000 * 0.30)        # burst = 30% of drained tally
    assert enemy.has_effect("DebuffDongBang")  # guaranteed 3-turn freeze
    assert player.han_khi_stacks == 0          # reset after burst


# ── 7. NO revive (identity guard) ───────────────────────────────────────────


def test_no_revive(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


# ── 8. Flag-OFF inertness ───────────────────────────────────────────────────


def test_flag_off_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    for key in ("BuffCuuAmHanKhi", "BuffBangPhach",
                "BuffBacMinhThonThuc", "BuffCucHanPhongAn"):
        assert not player.has_effect(key)
    # All config in the milestones → flag-off the body is fully inert.
    assert player.bm_cold_aura_enabled is False
    assert player.freeze_on_skill_chance < 0.45
    assert player.bm_mp_drain_pct == 0.0
    assert player.bm_heal_reduce_chance == 0.0
    enemy = _enemy()
    session = _session(player, enemy)
    run_bac_minh_procs(session, player, enemy)  # no-op flag-off
    assert not enemy.has_effect("DebuffLamCham")
    assert not enemy.has_effect("DebuffReduceHitCount")


def test_flag_off_resolves_to_flat() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", 6)
    assert flat == _body_data()["stat_bonuses"]
    assert "freeze_on_skill_chance" not in flat
