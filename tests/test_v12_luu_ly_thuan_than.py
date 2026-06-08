"""Custom constitution — Lưu Ly Thuẫn Thân Thể (Glazed Aegis Body, universal).

A radical archetype: NO flesh, only an aegis. hp_max is locked to 1 and the
would-be HP pool is converted into shield (×1.2). ALL damage — true-damage,
shield-pierce, execute, DoT — is forced through the shield (nothing bypasses).
While the shield holds it is near-immortal; the instant it shatters, any leftover
ends it. The CORE mechanic sits in the FLAT stat_bonuses so it is always-on when
equipped (works regardless of the process flag); the L1/L3/L6/L9 ladder is the
flag-gated enhancement (heal→shield, shield-offense, once-per-fight reform).

Flag hygiene: ladder tests flip the global via ``monkeypatch.setattr`` so it
auto-reverts. The core-mechanic tests deliberately also assert the flag-OFF path.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_LuuLyThuanThan"
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1, discord_id=1, name="AegisTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="body",
        constitution_type=_BODY, linh_can=["kim"], linh_can_levels={"kim": 5},
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
    e.shield = 0
    e.resistances = {}
    return e


def _player(level: int = 9):
    return build_player_combatant(_make_char(level), player_skill_keys=["SkillKimTripleStrike"])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[player.skill_keys[0]],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "universal"
    assert data["rarity"] == "mythic"
    # CORE mechanic lives in the FLAT stat_bonuses (always-on when equipped).
    assert data["stat_bonuses"]["shield_only_body"] is True
    assert data["stat_bonuses"]["shield_from_hp_max_pct"] == 1.2


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffLuuLyThuanTam"]
    assert effective_effects(data, 9) == [
        "BuffLuuLyThuanTam", "BuffQuyThuanHoaNguyen",
        "BuffThuanPhanCuong", "BuffLuuLyBatDiet",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffLuuLyThuanTam", "BuffQuyThuanHoaNguyen",
                "BuffThuanPhanCuong", "BuffLuuLyBatDiet"):
        assert EFFECTS.get(key) is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    assert _player(3).heal_to_shield_pct == pytest.approx(1.0)
    assert _player(6).damage_bonus_from_shield_pct == pytest.approx(0.05)
    assert _player(9).aegis_reform_charges == 1
    assert _player(9).aegis_reform_shield_pct == pytest.approx(0.40)


# ── 2. Core: HP→shield conversion + hp locked to 1 ──────────────────────────


def test_core_hp_locked_and_shield_full(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.shield_only_body is True
    assert player.hp_max == 1 and player.hp == 1
    # The would-be HP pool is now shield — and the fight starts at full shield.
    assert player.shield == player.shield_cap()
    assert player.shield > 1000  # the conversion produced a real pool


def test_core_works_flag_off() -> None:
    # The CORE mechanic is in the FLAT stat_bonuses, so it applies even when the
    # process flag is off (unlike the milestone ladder). This body is meant to
    # WORK when equipped, not wait for launch.
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.shield_only_body is True
    assert player.hp_max == 1
    assert player.shield == player.shield_cap() and player.shield > 1000
    # Ladder configs stay dormant flag-off.
    assert player.heal_to_shield_pct == 0.0
    assert player.aegis_reform_charges == 0


# ── 3. Force-shield: nothing bypasses the aegis ─────────────────────────────


def test_true_damage_cannot_bypass_shield(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.shield = 10_000
    player.shield_recharge_delay = 0
    player.aegis_reform_charges = 0  # isolate the absorb from the L9 reform
    leaked = player.take_damage(4_000, bypass_shield=True)  # true-damage style
    assert leaked == 0            # nothing reached HP
    assert player.shield == 6_000  # the shield ate it
    assert player.hp == 1


def test_dot_cannot_bypass_shield(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.shield = 10_000
    player.shield_recharge_delay = 0
    player.aegis_reform_charges = 0
    player.take_damage(3_000, is_dot=True)  # DoT normally skips the shield
    assert player.shield == 7_000
    assert player.hp == 1


def test_shield_break_ends_the_body(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.shield = 500
    player.shield_recharge_delay = 0
    player.aegis_reform_charges = 0  # no save
    player.take_damage(2_000, bypass_shield=True)
    assert player.shield == 0
    assert player.hp == 0  # leftover hit the 1 HP — dead
    assert not player.is_alive()


# ── 4. L3 Quy Thuẫn Hóa Nguyên — heals route into shield ────────────────────


def test_l3_heal_routes_to_shield(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    player.shield = max(0, player.shield - 50_000)  # room under the cap
    shield_before, hp_before = player.shield, player.hp
    gained = session._apply_heal(player, 30_000)
    assert gained == 30_000                       # reported as shield restored
    assert player.shield == shield_before + 30_000
    assert player.hp == hp_before == 1            # HP never moves (no flesh)


# ── 5. L6 Thuẫn Phản Cương — weaponize the shield ───────────────────────────


def test_l6_shield_offense(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    from src.game.engine.damage.combat_hit import apply_damage_scaling
    player = _player(6)
    assert player.damage_bonus_from_shield_pct == pytest.approx(0.05)
    player.shield = 100_000
    scaled = apply_damage_scaling(1_000, player, {})
    assert scaled >= 1_000 + int(100_000 * 0.05)  # +5% of current shield as bonus


# ── 6. L9 Lưu Ly Bất Diệt — once-per-fight reform ───────────────────────────


def test_l9_reform_saves_once_then_dies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.shield_recharge_delay = 0
    assert player.aegis_reform_charges == 1
    # First shatter → reform (restore 40% of cap, negate the blow, survive).
    player.shield = 500
    player.take_damage(5_000, bypass_shield=True)
    assert player.aegis_reform_charges == 0
    assert player.aegis_reform_just_triggered is True
    assert player.hp == 1
    assert player.shield == int(player.shield_cap() * 0.40)
    # Second shatter → no charge → death.
    player.shield = 500
    player.take_damage(5_000, bypass_shield=True)
    assert player.hp == 0
    assert not player.is_alive()


def test_no_revive(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    # The aegis reform is a shield mechanic, NOT an HP revive.
    assert session._try_revive(player) is False
    assert not player.is_alive()
