"""v12 constitution — Huyền Minh Nhược Thể (Dark Underworld Weak Water, Thủy).

The 3rd Thủy body: an anti-physical ATTRITION disruptor (where Huyền Thủy Trường
Sinh is a tidal tank and Bắc Minh Băng Phách is a freeze/control body). L1 a
physical-ONLY damage-reduction lane (magic/true bypass — the deliberate caster
weakness), L3 bidirectional MP→HP drain that siphons life once the enemy is MP-dry
(banking Uyên), L6 evasion + an Uyên (Abyss Depth) ramp that grows on every dodge,
L9 per-CAST guaranteed poison+bleed corrosion that stamps Hủ Thủy Ấn (DoT +50% /
heal −40%) plus a drown burst at full Uyên. One genuinely-new field
(phys_dmg_reduce_pct) + one new mark (DebuffHuThuyAn). NO revive.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
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
from src.game.systems.combat.procs import run_huyen_minh_procs
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_HuyenMinhNhuoc"
_PHYS = "SkillKimTripleStrike"                      # physical, kim, hit_count 3
_MAG = "SkillAtkLietDiemPhanThienChuong_R7"         # magical, hoa
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None, skill: str = _MAG) -> Character:
    return Character(
        player_id=1, discord_id=1, name="HuyenMinhTester",
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


def _player(level: int, skill: str = _MAG):
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
    assert "phys_dmg_reduce_pct" not in data["stat_bonuses"]
    assert data["process"]["levels"]["1"]["stat_bonuses"]["phys_dmg_reduce_pct"] == 0.25


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffNhuocThuyChiNhu"]
    assert effective_effects(data, 9) == [
        "BuffNhuocThuyChiNhu", "BuffNhuocThuyThonKhi",
        "BuffHuyenMinhHuTinh", "BuffHacThuyDietThe",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffNhuocThuyChiNhu", "BuffNhuocThuyThonKhi",
                "BuffHuyenMinhHuTinh", "BuffHacThuyDietThe", "DebuffHuThuyAn"):
        assert EFFECTS.get(key) is not None
    mark = EFFECTS["DebuffHuThuyAn"]
    assert mark.stat_bonus.get("dot_taken_bonus") == 0.50
    assert mark.stat_bonus.get("heal_taken_reduce") == 0.40


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    assert _player(1).phys_dmg_reduce_pct == pytest.approx(0.25)
    assert _player(1).hm_mp_drain_pct == 0.0
    assert _player(3).hm_mp_drain_pct == pytest.approx(0.10)
    assert _player(3).hm_mp_drain_heal_pct == pytest.approx(0.50)
    assert _player(3).hm_hp_siphon_pct == pytest.approx(0.02)
    assert _player(6).hm_uyen_cap == 7
    assert _player(9).hm_corrode_poison_stacks == 2
    assert _player(9).hm_corrode_bleed_stacks == 1
    assert _player(9).hm_drown_burst_drain_pct == pytest.approx(0.25)
    # phys reduce persists from L1 → L9 (set once, never doubled).
    assert _player(9).phys_dmg_reduce_pct == pytest.approx(0.25)


# ── 2. L1 Nhược Thủy Chi Nhu — physical-only reduction ──────────────────────


def _holder_hp_loss(monkeypatch, reduce_pct: float, attack_type: str) -> int:
    """Enemy strikes the holder once with ``attack_type``; return HP lost."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    skill = dict(registry.get_skill(_PHYS))
    skill["attack_type"] = attack_type
    skill["hit_count"] = 1
    player = _player(1)
    player.def_stat = 0
    player.shield = 0
    player.evasion_rating = 0
    player.resistances = {}
    player.phys_dmg_reduce_pct = reduce_pct
    enemy = _enemy()
    enemy.crit_rating = 0
    session = _session(player, enemy)
    session.rng.random = lambda: 0.5  # no crit, no evade
    hp0 = player.hp
    cast_skill(session, enemy, player, _PHYS, skill, 0)
    return hp0 - player.hp


def test_l1_reduces_physical(monkeypatch) -> None:
    base = _holder_hp_loss(monkeypatch, 0.0, "physical")
    reduced = _holder_hp_loss(monkeypatch, 0.25, "physical")
    assert base > 0
    assert reduced == pytest.approx(base * 0.75, rel=0.03)


def test_l1_does_not_touch_magical(monkeypatch) -> None:
    base = _holder_hp_loss(monkeypatch, 0.0, "magical")
    reduced = _holder_hp_loss(monkeypatch, 0.25, "magical")
    assert base > 0
    assert reduced == pytest.approx(base, rel=0.001)  # magic bypasses the lane


# ── 3. L3 Nhược Thủy Thôn Khí — MP drain → heal, dry-siphon ─────────────────


def test_l3_mp_drain_heals(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.hp = player.hp_max - 50_000  # room to heal
    enemy = _enemy(mp=100_000)
    session = _session(player, enemy)
    hp_before, mp_before = player.hp, enemy.mp
    run_huyen_minh_procs(session, player, enemy)
    drained = mp_before - enemy.mp
    assert drained == int(100_000 * 0.10)               # 10% current MP
    assert player.hp - hp_before == int(drained * 0.50)  # heal 50% of drained
    assert player.hm_mp_drained_total == drained


def test_l3_dry_siphon_drains_hp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy(mp=0)            # MP-dry → HP siphon path
    enemy.hp = enemy.hp_max = 1_000_000
    session = _session(player, enemy)
    run_huyen_minh_procs(session, player, enemy)
    assert enemy.hp_max - enemy.hp == int(1_000_000 * 0.02)  # 2% max HP siphon
    # Uyên cap is unset before L6 → no stack banked yet.
    assert player.hm_uyen_stacks == 0


def test_l3_drain_bidirectional(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy(mp=50_000)
    session = _session(player, enemy)
    mp_before = enemy.mp
    run_huyen_minh_procs(session, enemy, player)  # holder is the defender
    assert enemy.mp < mp_before  # holder drained the attacker on the way in


# ── 4. L6 Huyền Minh Hư Tĩnh — Uyên ramp ────────────────────────────────────


def test_l6_uyen_scales_evasion(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    assert player.has_effect("BuffHuyenMinhHuTinh")
    player.hm_uyen_stacks = 0
    base_eva = get_combat_modifiers(player).get("evasion_rating", 0.0)
    player.hm_uyen_stacks = 5
    eva5 = get_combat_modifiers(player).get("evasion_rating", 0.0)
    assert eva5 - base_eva == pytest.approx(5 * 30)  # +30 evasion / Uyên stack


def test_l6_uyen_gains_on_dodge(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    skill = dict(registry.get_skill(_PHYS))
    skill["hit_count"] = 1
    player = _player(6)
    player.evasion_rating = 10**9  # guarantee the dodge
    enemy = _enemy()
    enemy.accuracy_rating = 0
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    u0 = player.hm_uyen_stacks
    cast_skill(session, enemy, player, _PHYS, skill, 0)
    assert player.hm_uyen_stacks == u0 + 1


def test_l6_uyen_respects_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # L6 (not L9) so the drown burst doesn't consume Uyên mid-loop — isolates
    # the cap. ``hm_drown_burst_drain_pct`` unlocks only at L9.
    player = _player(6)             # cap 7, no burst yet
    assert player.hm_drown_burst_drain_pct == 0.0
    enemy = _enemy(mp=0)
    enemy.hp = enemy.hp_max = 10**9
    session = _session(player, enemy)
    for _ in range(12):             # far more dry-siphons than the cap
        run_huyen_minh_procs(session, player, enemy)
    assert player.hm_uyen_stacks == 7


# ── 5. L9 Hắc Thủy Diệt Thế — per-cast corrosion + Hủ Thủy Ấn ───────────────


def test_l9_corrosion_is_per_cast(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    skill = registry.get_skill(_PHYS)
    assert int(skill.get("hit_count", 1)) == 3   # 3 hits in one cast
    player = _player(9, skill=_PHYS)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.5
    player.mp = max(player.mp, skill.get("mp_cost", 0) + 1)
    cast_skill(session, player, enemy, _PHYS, skill, skill.get("mp_cost", 0))
    # Once per CAST, not per hit (per-hit would give 6 poison / 3 bleed).
    assert enemy.poison_stacks == 2
    assert enemy.bleed_stacks == 1
    assert enemy.has_effect("DebuffHuThuyAn")


def test_hu_thuy_an_amps_dot_and_cuts_heal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    enemy = _enemy()
    enemy.apply_effect("DebuffHuThuyAn", 3)
    mods = get_combat_modifiers(enemy)
    assert mods.get("dot_taken_bonus") == pytest.approx(0.50)
    assert mods.get("heal_taken_reduce") == pytest.approx(0.40)


# ── 6. Drown burst at full Uyên ─────────────────────────────────────────────


def test_drown_burst_at_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    enemy.shield = 0
    session = _session(player, enemy)
    player.hm_uyen_stacks = 7
    player.hm_mp_drained_total = 200_000
    hp_before = enemy.hp
    run_huyen_minh_procs(session, player, enemy)
    dealt = hp_before - enemy.hp
    assert dealt >= int(200_000 * 0.25)        # burst = 25% of drained tally
    assert enemy.has_effect("DebuffLamCham")   # drown-slow rider
    assert player.hm_uyen_stacks == 0          # reset after burst


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
    for key in ("BuffNhuocThuyChiNhu", "BuffNhuocThuyThonKhi",
                "BuffHuyenMinhHuTinh", "BuffHacThuyDietThe"):
        assert not player.has_effect(key)
    # All config in the milestones → flag-off the body is fully inert.
    assert player.phys_dmg_reduce_pct == 0.0
    assert player.hm_mp_drain_pct == 0.0
    assert player.hm_corrode_poison_stacks == 0
    assert player.hm_drown_burst_drain_pct == 0.0
    enemy = _enemy()
    session = _session(player, enemy)
    run_huyen_minh_procs(session, player, enemy)  # no-op flag-off
    assert enemy.mp == enemy.mp_max
    assert player.hm_uyen_stacks == 0


def test_flag_off_resolves_to_flat() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", 6)
    assert flat == _body_data()["stat_bonuses"]
    assert "phys_dmg_reduce_pct" not in flat
