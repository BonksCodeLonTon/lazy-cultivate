"""v12 constitution — Thiên Thủy Thánh Thể (Heaven Water Sacred Body, Thủy).

The 4th Thủy body: an immortal holy-spring SUSTAIN tank (vs #5 tidal, #14 freeze,
#15 attrition). L1 huge HP/MP regen (pure stats), L3 convert 30% of damage taken
into healing + reflect 15% of the remainder, L6 every heal → 50% cleanse + bank
Tịnh Hóa (raises cleanse rate + MP-on-heal), L9 Quy Khư Thôn Hải — passive Thủy
resist + final_dmg_reduce, and at 5 Tịnh Hóa SPEND for an abyss swallow (soak 80%
of one hit — NOT 0 — heal it, reflect 25%, reset). NO revive. Reuses res_thuy /
final_dmg_reduce (real stats) + the _apply_heal chokepoint + apply_reactive_damage.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
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
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.procs import apply_reactive_damage
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_ThienThuyThanh"
_PHYS = "SkillKimTripleStrike"   # physical, kim — no res_thuy interference
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1, discord_id=1, name="ThanhTuyenTester",
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
    e.shield = 0
    e.resistances = {}
    e.evasion_rating = 0
    e.spd = 0
    return e


def _player(level: int):
    return build_player_combatant(_make_char(level), player_skill_keys=[_PHYS])


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
    # Flat is pure stats; all mechanic config lives in the milestones.
    assert "tt_dmg_convert_heal_pct" not in data["stat_bonuses"]
    assert data["process"]["levels"]["9"]["stat_bonuses"]["res_thuy"] == 0.40


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffThanhTuyen"]
    assert effective_effects(data, 9) == [
        "BuffThanhTuyen", "BuffNhuThuyHoaKinh",
        "BuffThanhTuyenTayLe", "BuffQuyKhuThonHai",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffThanhTuyen", "BuffNhuThuyHoaKinh",
                "BuffThanhTuyenTayLe", "BuffQuyKhuThonHai"):
        assert EFFECTS.get(key) is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    assert _player(3).tt_dmg_convert_heal_pct == pytest.approx(0.30)
    assert _player(3).tt_reflect_remainder_pct == pytest.approx(0.15)
    assert _player(6).tt_heal_cleanse_chance == pytest.approx(0.50)
    assert _player(6).tt_tinh_hoa_cap == 5
    assert _player(6).tt_tinh_hoa_per_stack_cleanse == pytest.approx(0.08)
    assert _player(9).tt_abyss_threshold == 5
    assert _player(9).tt_abyss_reduce_pct == pytest.approx(0.80)
    assert _player(9).tt_abyss_reflect_pct == pytest.approx(0.25)


# ── 2. L1 Thánh Tuyền — regen ───────────────────────────────────────────────


def test_l1_regen_heals(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = player.hp_max // 2
    before = player.hp
    session._process_periodic(player)
    # ≥7% HP/turn at L1 (flat 3% + L1 4% + growth).
    assert (player.hp - before) >= int(player.hp_max * 0.06)


# ── 3. L3 Nhu Thủy Hóa Kình — damage→heal + reflect ─────────────────────────


def test_l3_convert_heals_and_reflects(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.hp = player.hp_max - 200_000  # room to heal
    player.reflect_pct = 0.0  # isolate from the Thủy linh-căn base reflect lane
    enemy = _enemy()
    session = _session(player, enemy)
    hp0, e0 = player.hp, enemy.hp
    apply_reactive_damage(session, enemy, player, 100_000, skill_element="kim")
    assert player.hp - hp0 == int(100_000 * 0.30)                 # convert 30%
    assert e0 - enemy.hp == int(100_000 * 0.70 * 0.15)            # reflect 15% of remainder


# ── 4. L6 Thánh Tuyền Tẩy Lễ — heal-cleanse + Tịnh Hóa ──────────────────────


def test_l6_cleanse_on_heal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.apply_effect("DebuffLamCham", 3)  # a cleansable debuff
    assert player.has_effect("DebuffLamCham")
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # < cleanse chance
    player.hp = player.hp_max - 50_000  # room to heal
    session._apply_heal(player, 50_000)
    assert not player.has_effect("DebuffLamCham")
    assert player.tt_tinh_hoa_stacks == 1


def test_l6_cleanse_respects_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    for _ in range(10):  # far more heals (each with a cleansable debuff) than cap
        player.apply_effect("DebuffLamCham", 3)
        player.hp = player.hp_max - 10_000
        session._apply_heal(player, 10_000)
    assert player.tt_tinh_hoa_stacks == 5  # cap


def test_l6_mp_on_heal_scales_with_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.tt_tinh_hoa_stacks = 3
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # no cleanse → isolate MP-on-heal
    player.hp = player.hp_max - 20_000
    player.mp = 0
    healed = session._apply_heal(player, 10_000)
    expected = min(int(healed * 0.10 * 3), player.mp_max)
    assert player.mp == expected


# ── 5. L9 Quy Khư Thôn Hải — Thủy resist + abyss swallow ────────────────────


def test_l9_thuy_resist(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.resistances.get("thuy", 0.0) >= 0.40


def test_l9_abyss_swallow_spends_and_reflects(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.tt_dmg_convert_heal_pct = 0.0   # isolate from L3
    player.tt_heal_cleanse_chance = 0.0    # isolate from L6 re-banking
    player.tt_tinh_hoa_stacks = 5
    enemy = _enemy()
    skill = dict(registry.get_skill(_PHYS))
    skill["hit_count"] = 1
    session = _session(player, enemy)
    session.rng.random = lambda: 0.5
    e0 = enemy.hp
    cast_skill(session, enemy, player, _PHYS, skill, 0)
    assert player.tt_tinh_hoa_stacks == 0  # charge spent
    assert enemy.hp < e0                    # abyss reflect landed on the attacker


def test_l9_abyss_soaks_80pct(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    def _loss(stacks: int) -> int:
        player = _player(9)
        player.hp = player.hp_max          # no heal room → measure pure damage taken
        player.shield = 0
        player.tt_dmg_convert_heal_pct = 0.0
        player.tt_heal_cleanse_chance = 0.0
        player.tt_tinh_hoa_stacks = stacks
        enemy = _enemy()
        enemy.crit_rating = 0
        skill = dict(registry.get_skill(_PHYS))
        skill["hit_count"] = 1
        session = _session(player, enemy)
        session.rng.random = lambda: 0.5
        hp0 = player.hp
        cast_skill(session, enemy, player, _PHYS, skill, 0)
        return hp0 - player.hp

    full = _loss(0)
    soaked = _loss(5)
    assert full > 0
    assert soaked == pytest.approx(full * 0.20, rel=0.12)  # ~80% reduced, never 0


# ── 6. NO revive (identity guard) ───────────────────────────────────────────


def test_no_revive(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


# ── 7. Flag-OFF inertness ───────────────────────────────────────────────────


def test_flag_off_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    for key in ("BuffThanhTuyen", "BuffNhuThuyHoaKinh",
                "BuffThanhTuyenTayLe", "BuffQuyKhuThonHai"):
        assert not player.has_effect(key)
    assert player.tt_dmg_convert_heal_pct == 0.0
    assert player.tt_heal_cleanse_chance == 0.0
    assert player.tt_abyss_threshold == 0
    # No abyss soak flag-off: convert/reflect/cleanse all dormant.
    enemy = _enemy()
    session = _session(player, enemy)
    hp0 = player.hp
    apply_reactive_damage(session, enemy, player, 100_000, skill_element="kim")
    assert player.hp == hp0  # no convert-heal flag-off


def test_flag_off_resolves_to_flat() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", 6)
    assert flat == _body_data()["stat_bonuses"]
    assert "tt_dmg_convert_heal_pct" not in flat
    assert "res_thuy" not in flat
