"""v12 constitution — Hỗn Nguyên Vô Cực Thể (Chaos-Origin Infinite Body, universal).

The omni-element amplifier (Vạn Nguyên Quy Nhất): a body with no single-element
specialisation that instead masters all nine. Its core makes EVERY skill paid the
SUM of all nine elements' damage bonuses, counted as the skill's OWN element (a
fire skill is paid hoa+moc+thuy+… as fire damage). Its L6/L9 payoff is a per-cast
chance to treat the target's elemental resistance as 0%. NO revive. These tests
pin every mechanic on the flag-ON path plus the flag-OFF inert seam.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.engine.damage.combat_hit import build_attack_stats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.constitution_process import (
    effective_effects, effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_HonNguyenVoCuc"
_HOA_SKILL = "SkillAtkLietDiemPhanThienChuong_R7"
_ENEMY = "TinhKimTho"
_ELEMS = ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am")


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1, discord_id=1, name="OmniTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["quang"], linh_can_levels={"quang": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9, res: dict | None = None):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.resistances = dict(res or {})
    e.evasion_rating = 0
    e.spd = 0
    return e


def _player(level: int):
    return build_player_combatant(_make_char(level), player_skill_keys=[_HOA_SKILL])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[_HOA_SKILL],
        rng=random.Random(seed), max_turns=200,
    )


def _cast_dmg(level: int, res: dict, rand_value: float, skill: str = _HOA_SKILL) -> int:
    """Cast ``skill`` once at a fresh enemy with ``res`` resistances; return the
    direct damage. ``rand_value`` pins every rng draw — crit is forced via a huge
    crit_rating so only the res-ignore proc (gated on rand_value) varies."""
    player = _player(level)
    player.crit_rating = 99999
    player.hp = 10**9
    enemy = _enemy(res=res)
    session = _session(player, enemy)
    session.rng.random = lambda: rand_value
    skill_data = registry.get_skill(skill)
    assert skill_data is not None
    mp_cost = skill_data.get("mp_cost", 0)
    player.mp = max(player.mp, mp_cost + 1)
    hp0 = enemy.hp
    cast_skill(session, player, enemy, skill, skill_data, mp_cost)
    return hp0 - enemy.hp


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "universal"
    assert data["rarity"] == "mythic"
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}
    # L1 flat carries the omni flag + the seed per-element dict.
    flat = data["stat_bonuses"]
    assert flat["omni_sum_element_dmg"] is True
    assert sum(flat["element_dmg_bonus"].values()) == pytest.approx(0.18)


def test_composition_l1_and_l9_effects() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffVanNguyenHopNhat"]
    assert effective_effects(data, 9) == [
        "BuffVanNguyenHopNhat", "BuffVanHanhTinhThong",
        "BuffVanPhapPhaCan", "BuffVanPhapQuyTong",
    ]


def test_element_sum_ramps_per_milestone() -> None:
    data = _body_data()
    sums = {lvl: sum(effective_stat_bonuses(data, lvl)["element_dmg_bonus"].values())
            for lvl in (1, 3, 6, 9)}
    assert sums[1] == pytest.approx(0.18)
    assert sums[3] == pytest.approx(0.36)
    assert sums[6] == pytest.approx(0.45)
    assert sums[9] == pytest.approx(0.54)
    # Every element advances together (no favoured element).
    l9 = effective_stat_bonuses(data, 9)["element_dmg_bonus"]
    assert all(l9[e] == pytest.approx(0.06) for e in _ELEMS)


def test_res_ignore_chance_ramp() -> None:
    data = _body_data()
    chances = {lvl: effective_stat_bonuses(data, lvl).get("omni_res_ignore_chance", 0.0)
               for lvl in (1, 3, 6, 9)}
    assert chances[1] == 0.0
    assert chances[3] == 0.0
    assert chances[6] == pytest.approx(0.20)
    assert chances[9] == pytest.approx(0.40)


# ── 2. Vạn Nguyên Quy Nhất — sum-all-elements as own element ─────────────────


def test_every_skill_paid_the_full_sum(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    assert player.omni_sum_element_dmg is True
    enemy = _enemy()
    # Gear example: hoa/moc/thuy/am at 20% each, the rest 0%.
    player.element_dmg_bonus = {"hoa": 0.20, "moc": 0.20, "thuy": 0.20, "am": 0.20}
    base = build_attack_stats(player, enemy, {}, None).final_dmg_bonus  # no element
    hoa = build_attack_stats(player, enemy, {}, "hoa").final_dmg_bonus
    kim = build_attack_stats(player, enemy, {}, "kim").final_dmg_bonus
    # A fire skill AND a metal skill are BOTH paid the full +0.80 sum.
    assert hoa - base == pytest.approx(0.80)
    assert kim - base == pytest.approx(0.80)
    assert hoa == pytest.approx(kim)


def test_non_omni_only_pays_own_element(monkeypatch) -> None:
    """Pin the gate: with the flag OFF the branch falls back to the skill's own
    element bonus (byte-identical to every other body)."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    player.element_dmg_bonus = {"hoa": 0.20, "moc": 0.20, "thuy": 0.20, "am": 0.20}
    player.omni_sum_element_dmg = False  # simulate a non-omni body
    base = build_attack_stats(player, enemy, {}, None).final_dmg_bonus
    hoa = build_attack_stats(player, enemy, {}, "hoa").final_dmg_bonus
    kim = build_attack_stats(player, enemy, {}, "kim").final_dmg_bonus
    assert hoa - base == pytest.approx(0.20)   # only hoa's own bonus
    assert kim - base == pytest.approx(0.0)    # kim has no bonus


# ── 3. Vạn Pháp Vô Cản — per-cast res-ignore proc ───────────────────────────


def test_res_ignore_proc_zeroes_resistance(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # rand 0.0 < 0.20 (L6) → the proc fires. A 50%-fire-res enemy then takes the
    # SAME damage as a 0%-res enemy: the resistance was treated as 0%.
    dmg_resisted = _cast_dmg(6, {"hoa": 0.5}, 0.0)
    dmg_no_res = _cast_dmg(6, {}, 0.0)
    assert dmg_resisted == dmg_no_res


def test_res_applies_when_proc_misses(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # rand 0.5 ≥ 0.20 (L6) → the proc does NOT fire. The 50% fire res now bites:
    # less damage than the same cast at a 0%-res enemy.
    dmg_resisted = _cast_dmg(6, {"hoa": 0.5}, 0.5)
    dmg_no_res = _cast_dmg(6, {}, 0.5)
    assert dmg_resisted < dmg_no_res


def test_l9_res_ignore_chance_is_higher(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # rand 0.30: fires at L9 (0.30 < 0.40) but NOT at L6 (0.30 ≥ 0.20).
    l9_fires = _cast_dmg(9, {"hoa": 0.5}, 0.30)
    l9_no_res = _cast_dmg(9, {}, 0.30)
    assert l9_fires == l9_no_res  # L9 proc fired → res ignored
    l6_misses = _cast_dmg(6, {"hoa": 0.5}, 0.30)
    l6_no_res = _cast_dmg(6, {}, 0.30)
    assert l6_misses < l6_no_res  # L6 proc missed → res applied


def test_res_ignore_chance_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    assert _player(1).omni_res_ignore_chance == 0.0
    assert _player(3).omni_res_ignore_chance == 0.0
    assert _player(6).omni_res_ignore_chance == pytest.approx(0.20)
    assert _player(9).omni_res_ignore_chance == pytest.approx(0.40)


# ── 4. NO revive (identity guard) ───────────────────────────────────────────


def test_no_revive(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


# ── 5. Flag-OFF inertness ───────────────────────────────────────────────────


def test_flag_off_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    for key in ("BuffVanNguyenHopNhat", "BuffVanHanhTinhThong",
                "BuffVanPhapPhaCan", "BuffVanPhapQuyTong"):
        assert not player.has_effect(key)
    # Milestone res-ignore is inert flag-off.
    assert player.omni_res_ignore_chance == 0.0
    # The omni-sum flag + seed per-element dict live in the FLAT base (like body
    # #6 poison / body #11 blind), so they ARE set even flag-off — the L1 body
    # still sums its +0.18 across every skill.
    assert player.omni_sum_element_dmg is True
    assert sum(player.element_dmg_bonus.values()) == pytest.approx(0.18)


def test_flag_off_resolves_to_flat() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", 6)
    assert flat == _body_data()["stat_bonuses"]
    assert "omni_res_ignore_chance" not in flat
