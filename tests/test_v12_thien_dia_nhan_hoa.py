"""v12 constitution — Thiên Địa Nhân Hòa Thể (Heaven-Earth-Human Harmony, universal).

A slow-ramp universal juggernaut built on the Hòa Khí stack: +1/turn, cap 9,
never decays. L1 grants +2% atk/matk/def/spd PER stack (read via scaling_rules
off ``harmony_stacks``); L3 unlocks +25% dmg / +15% DR at ≥5 stacks; L6 radiates a
backlash aura (stacks × 6% of atk+matk) at ≥5 stacks; L9 fuses all three talents
permanently at 9 stacks (Thiên +20% dmg/+90 crit, Địa +18% DR/+6% shield, Nhân
+5% regen + cleanse 1/turn). NO revive. These tests pin every mechanic on the
flag-ON path plus the flag-OFF inert seam.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.engine.effects import get_combat_modifiers
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.constitution_process import (
    effective_effects, effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_ThienDiaNhanHoa"
_SKILL = "SkillAtkLietDiemPhanThienChuong_R7"
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1, discord_id=1, name="HarmonyTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["kim"], linh_can_levels={"kim": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.resistances = {}
    e.evasion_rating = 0
    e.spd = 0
    return e


def _player(level: int):
    return build_player_combatant(_make_char(level), player_skill_keys=[_SKILL])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[_SKILL],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "universal"
    assert data["rarity"] == "mythic"
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    # The harmony config lives in the L1 MILESTONE (process-gated), NOT the flat
    # base — so a flag-off body stays a plain even-stat body (no half-working ramp).
    assert "harmony_stack_per_turn" not in data["stat_bonuses"]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {
        "harmony_stack_per_turn": 1, "harmony_stack_cap": 9,
    }


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffNhanHoaTichLuy"]
    assert effective_effects(data, 9) == [
        "BuffNhanHoaTichLuy", "BuffThienDiaTuongUng",
        "BuffHoaKhiChienTruong", "BuffTamTaiHopNhat",
    ]
    # L6 backlash config surfaces at the L6 milestone.
    l6 = effective_stat_bonuses(data, 6)
    assert l6["harmony_backlash_pct_per_stack"] == pytest.approx(0.06)
    assert l6["harmony_backlash_min_stacks"] == 5
    assert effective_stat_bonuses(data, 9)["harmony_l9_cleanse"] == 1


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    assert _player(1).harmony_stack_per_turn == 1
    assert _player(1).harmony_stack_cap == 9
    assert _player(3).harmony_backlash_pct_per_stack == 0.0  # backlash is L6
    assert _player(6).harmony_backlash_pct_per_stack == pytest.approx(0.06)
    assert _player(9).harmony_l9_cleanse == 1


# ── 2. L1 Nhân Hòa Tích Lũy — per-stack all-stat ramp ───────────────────────


def test_l1_ramp_scales_all_stats_per_stack(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    for stacks, expect in [(0, 0.0), (1, 0.02), (5, 0.10), (9, 0.18)]:
        player.harmony_stacks = stacks
        mods = get_combat_modifiers(player)
        for stat in ("atk_pct", "matk_pct", "def_pct", "spd_pct"):
            assert mods.get(stat, 0.0) == pytest.approx(expect), f"{stat}@{stacks}"


# ── 3. L3 Thiên Địa Tương Ứng — ≥5-stack threshold ──────────────────────────


def test_l3_threshold_at_5_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.harmony_stacks = 4
    mods = get_combat_modifiers(player)
    assert mods.get("final_dmg_bonus", 0.0) == pytest.approx(0.0)
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(0.0)
    player.harmony_stacks = 5
    mods = get_combat_modifiers(player)
    assert mods.get("final_dmg_bonus", 0.0) == pytest.approx(0.25)
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(0.15)


# ── 4. L9 Tam Tài Hợp Nhất — fusion at 9 stacks ─────────────────────────────


def test_l9_fusion_stacks_all_three_talents(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.harmony_stacks = 8  # one short of max — fusion not yet online
    mods = get_combat_modifiers(player)
    assert mods.get("crit_rating", 0) == pytest.approx(0.0)
    player.harmony_stacks = 9
    mods = get_combat_modifiers(player)
    # Thiên: +20% dmg (on top of L3's +25%) + 90 crit.
    assert mods.get("final_dmg_bonus", 0.0) == pytest.approx(0.25 + 0.20)
    assert mods.get("crit_rating", 0) == pytest.approx(90)
    # Địa: +18% DR (on top of L3's +15%) + 6% shield regen.
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(0.15 + 0.18)
    assert mods.get("shield_regen_pct", 0.0) == pytest.approx(0.06)
    # Nhân: +5% HP regen.
    assert mods.get("hp_regen_pct", 0.0) == pytest.approx(0.05)


# ── 5. Hòa Khí accrual (periodic) ───────────────────────────────────────────


def test_hoa_khi_accrues_and_caps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    assert player.harmony_stacks == 0
    for expected in range(1, 13):
        session._process_periodic(player)
        assert player.harmony_stacks == min(9, expected)
    assert player.harmony_stacks == 9  # capped, never decays


# ── 6. L6 Hòa Khí Chiến Trường — backlash aura ──────────────────────────────


def test_l6_backlash_fires_at_5plus_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    player.harmony_stacks = 9  # at cap → increment is a no-op, backlash uses 9
    hp_before = enemy.hp
    session._process_periodic(player)
    dealt = hp_before - enemy.hp
    expected = int(9 * 0.06 * (player.atk + player.matk))
    assert dealt > 0
    assert dealt >= int(expected * 0.9)  # ×(1+final_dmg_bonus) only raises it


def test_l6_no_backlash_below_5_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    player.harmony_stacks = 2  # +1 → 3, still < 5
    hp_before = enemy.hp
    session._process_periodic(player)
    assert enemy.hp == hp_before  # no backlash


def test_l1_body_has_no_backlash(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)  # below L6 → backlash config absent
    enemy = _enemy()
    session = _session(player, enemy)
    player.harmony_stacks = 9
    hp_before = enemy.hp
    session._process_periodic(player)
    assert enemy.hp == hp_before


# ── 7. L9 Nhân self-cleanse ─────────────────────────────────────────────────


def test_l9_cleanse_at_max_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.apply_effect("DebuffPhaGiap", 9)  # a cleansable debuff on self
    player.harmony_stacks = 9
    session._process_periodic(player)
    assert not player.has_effect("DebuffPhaGiap")


def test_l9_no_cleanse_below_max(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.apply_effect("DebuffPhaGiap", 9)
    player.harmony_stacks = 7  # +1 → 8, still < 9
    session._process_periodic(player)
    assert player.has_effect("DebuffPhaGiap")  # not cleansed


# ── 8. NO revive (identity guard) ───────────────────────────────────────────


def test_no_revive(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


# ── 9. Flag-OFF inertness ───────────────────────────────────────────────────


def test_flag_off_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    for key in ("BuffNhanHoaTichLuy", "BuffThienDiaTuongUng",
                "BuffHoaKhiChienTruong", "BuffTamTaiHopNhat"):
        assert not player.has_effect(key)
    # The harmony config lives in the L1 milestone (process-gated), so flag-off
    # it is fully inert — no accrual, no backlash, no fused talents.
    assert player.harmony_stack_per_turn == 0
    assert player.harmony_stack_cap == 0
    assert player.harmony_backlash_pct_per_stack == 0.0
    assert player.harmony_l9_cleanse == 0
    player.harmony_stacks = 9
    mods = get_combat_modifiers(player)
    for stat in ("atk_pct", "final_dmg_bonus", "crit_rating"):
        assert mods.get(stat, 0.0) == 0.0


def test_flag_off_resolves_to_flat() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", 6)
    assert flat == _body_data()["stat_bonuses"]
    assert "harmony_stack_per_turn" not in flat
