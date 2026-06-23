"""v12 constitution — Tịnh Quang Hộ Pháp Thể (Pure Light Guardian Body, Quang).

The first Quang body: a pure-defense guardian (no offense stats) that blinds on
hit, banks Thánh Quang stacks off those blinds, self-cleanses on a cadence,
fields a PERMANENT Holy Guardian summon (damage + aura buff), and judges buffed
enemies (strip + Phá Giáp). NO revive. These tests pin every mechanic on the
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
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.combat.procs import run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects, effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_TinhQuangHoPhap"
_SKILL = "SkillSupMoc"
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1, discord_id=1, name="QuangTester",
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
    assert data["element"] == "quang"
    assert data["rarity"] == "legendary"
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_and_l9_effects() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffHoPhapThanhQuang"]
    assert effective_effects(data, 9) == [
        "BuffHoPhapThanhQuang", "BuffTinhQuangTayTran",
        "BuffHoPhapThienGiap", "BuffThienQuangThamPhan",
    ]


def test_defensive_ramp_no_offense() -> None:
    data = _body_data()
    l9 = effective_stat_bonuses(data, 9)
    assert l9["def_pct"] == pytest.approx(0.08 + 0.06 + 0.06 + 0.005 * 8)
    assert l9["hp_pct"] == pytest.approx(0.08 + 0.04 + 0.05 + 0.05 + 0.005 * 8)
    assert l9["crit_res_rating"] == pytest.approx(120 + 100 + 18 * 8)
    # Pure guardian: no offense stats anywhere.
    for off in ("atk_pct", "matk_pct", "crit_rating", "final_dmg_bonus"):
        assert off not in l9


# ── 2. L1 blind-on-hit + BuffHoPhap + Thánh Quang ───────────────────────────


def test_l1_blind_and_guardian_buff(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    assert player.blind_on_hit_pct == pytest.approx(0.45)
    assert player.has_effect("BuffHoPhapThanhQuang")
    # BuffHoPhapThanhQuang grants +10% DR + 100 crit-res.
    mods = get_combat_modifiers(player)
    assert mods.get("final_dmg_reduce", 0.0) >= 0.10
    assert mods.get("crit_res_rating", 0) >= 100


def test_thanh_quang_stacks_on_blind(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # force the blind proc

    for _ in range(7):
        run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_SKILL)
    assert player.thanh_quang_stacks == 5  # caps at 5
    # At 5 stacks BuffHoPhapThanhQuang's scaling_rules add +0.15 DR and +0.25 blind chance.
    mods = get_combat_modifiers(player)
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(0.10 + 0.15)
    assert mods.get("blind_on_hit_pct", 0.0) == pytest.approx(0.25)


# ── 3. L3 self-cleanse cadence ──────────────────────────────────────────────


def test_l3_self_cleanse_every_3_turns(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    assert player.quang_self_cleanse_interval == 3
    enemy = _enemy()
    session = _session(player, enemy)
    player.apply_effect("DebuffPhaGiap", 9)  # a cleansable debuff on self

    for _ in range(2):
        run_phase(TurnPhase.PRE_TURN, TurnContext(actor=player, target=enemy, session=session))
    assert player.has_effect("DebuffPhaGiap")  # not yet (turns 1,2)
    run_phase(TurnPhase.PRE_TURN, TurnContext(actor=player, target=enemy, session=session))
    assert not player.has_effect("DebuffPhaGiap")  # turn 3 → cleansed


# ── 4. L6 permanent Holy Guardian summon (damage + buff) ────────────────────


def test_l6_guardian_summon_present_and_buffs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    guardians = [s for s in player.summons if s.get("aura_buff_key") == "BuffHoPhapKimCuong"]
    assert len(guardians) == 1
    g = guardians[0]
    assert g["element"] == "quang"
    assert g["dmg"] == max(1, int(player.matk * 0.60))
    # The summon's aura folds into the owner's stats: BuffHoPhapThanhQuang (0.10)
    # + the summon's BuffHoPhapKimCuong (0.10) = 0.20 DR.
    mods = get_combat_modifiers(player)
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(0.20)


def test_l6_guardian_summon_deals_damage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    hp_before = enemy.hp
    session.turn = 1
    session._process_periodic(player)
    dealt = hp_before - enemy.hp
    assert dealt >= int(player.matk * 0.60) * 0.5  # ~60% matk, after quang res


def test_l1_has_no_guardian_summon(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)  # below L6 → no summon
    assert not any(s.get("aura_buff_key") == "BuffHoPhapKimCuong" for s in player.summons)


# ── 5. L9 Thiên Quang Thẩm Phán — strip buff + Phá Giáp ─────────────────────


def test_l9_judgment_strips_buff_and_pha_giap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.quang_judgment_strip_chance == pytest.approx(0.60)
    enemy = _enemy()
    enemy.apply_effect("BuffTangToc", 3)  # a buff to strip
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # < 0.60 → judgment fires

    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_SKILL)
    assert not enemy.has_effect("BuffTangToc")   # buff stripped
    assert enemy.has_effect("DebuffPhaGiap")     # armor broken


def test_l9_judgment_burst_and_ramp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    enemy.shield = 0
    enemy.apply_effect("BuffTangToc", 3)
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0

    hp_before = enemy.hp
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_SKILL)
    # Burst = 200% atk + 200% matk (raw Quang judgment).
    assert hp_before - enemy.hp >= int(2.0 * player.atk + 2.0 * player.matk)
    # Each strip ramps the guardian's own final_dmg +5%.
    assert player.quang_judgment_dmg_bonus == pytest.approx(0.05)
    from src.game.engine.damage.combat_hit import build_attack_stats
    assert build_attack_stats(player, enemy, {}, "quang").final_dmg_bonus >= 0.05

    # Cap binds at +50%.
    player.quang_judgment_dmg_bonus = 0.48
    enemy.apply_effect("BuffTangToc", 3)
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_SKILL)
    assert player.quang_judgment_dmg_bonus == pytest.approx(0.50)


def test_l9_judgment_noop_vs_unbuffed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()  # no buffs
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_SKILL)
    assert not enemy.has_effect("DebuffPhaGiap")  # no buff → no judgment


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
    for key in ("BuffHoPhapThanhQuang", "BuffTinhQuangTayTran", "BuffHoPhapThienGiap",
                "BuffThienQuangThamPhan"):
        assert not player.has_effect(key)
    # Milestone flags inert; no guardian summon.
    assert player.quang_self_cleanse_interval == 0
    assert player.quang_guardian_summon_matk_pct == 0.0
    assert player.quang_judgment_strip_chance == 0.0
    assert not any(s.get("aura_buff_key") == "BuffHoPhapKimCuong" for s in player.summons)
    # blind_on_hit_pct lives in the FLAT base, so it IS set even flag-off (like
    # body #6 poison) — but quang_blind_stack drives the milestone stacking only.
    assert player.blind_on_hit_pct == pytest.approx(0.45)


def test_flag_off_resolves_to_flat() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", 6)
    assert flat == _body_data()["stat_bonuses"]
    assert "quang_guardian_summon_matk_pct" not in flat
