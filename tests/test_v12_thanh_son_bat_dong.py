"""v12 constitution — Thánh Sơn Bất Động Thể (Sacred Mountain Immovable Body, Thổ).

The 3rd Thổ body: an IMMOVABLE FORTRESS — the mountain that only gets harder the
more you hit it (vs Kim Cang's shield-recursion wall, Hậu Thổ's HP-vampire growth).

  L1 Kiên Như Bàn Thạch  — +12% res, +20% DR; each hit TAKEN banks +1 Kiên Cố (cap
                           8). BuffKienCo's scaling_rules turn the stack count into
                           live res_all (+2%/stack) + final_dmg_reduce (+1.5%/stack).
  L3 Thái Sơn Áp Đỉnh    — per CAST, bonus TRUE dmg = 70% shield (+20% at ≥4 Kiên
                           Cố); per-hit 60% Bào Mòn / 35% Choáng 2t.
  L6 Bất Động Minh Vương — +280 crit_res, +35% DR, 60% debuff-shrug (real stats).
  L9 Vạn Vật Quy Trần    — while Kiên Cố is FULL (8): a would-be-lethal NON-DoT hit
                           leaves HP at 1 + restores 30% shield. Hard CC cracks a
                           Kiên Cố stack → drops below cap → mortal again.

Reuses: the on-hit proc seam (run_thanh_son_procs), the per-cast shield→true-dmg
seam, BuffKienCo scaling_rules, the take_damage survival step, and the inflict_debuff
post-stamp hook for the CC stack-strip.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so it
auto-reverts; the dormant default is never left mutated.
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
from src.game.systems.combat.casting import cast_skill, inflict_debuff
from src.game.systems.combat.procs import run_thanh_son_procs
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_ThanhSonBatDong"
_ATTACK = "SkillKimTripleStrike"
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None, *, linh_can: list[str] | None = None) -> Character:
    lc = linh_can if linh_can is not None else ["tho"]
    return Character(
        player_id=1, discord_id=1, name="ThanhSonTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=lc, linh_can_levels={e: 5 for e in lc},
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
    e.final_dmg_reduce = 0.0
    e.spd = 0
    return e


def _player(level: int, *, linh_can: list[str] | None = None):
    return build_player_combatant(
        _make_char(level, linh_can=linh_can), player_skill_keys=[_ATTACK],
    )


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[player.skill_keys[0]],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "tho"
    assert data["rarity"] == "legendary"
    assert data["cost"]["merit"] == 60000
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    flat = data["stat_bonuses"]
    for cfg in (
        "thanh_son_kien_co_on_hit", "thanh_son_dmg_from_shield_pct",
        "thanh_son_immovable_enabled",
    ):
        assert cfg not in flat
    assert flat["def_pct"] == 0.10


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffKienNhuBanThach", "BuffKienCo"]
    assert effective_effects(data, 9) == [
        "BuffKienNhuBanThach", "BuffKienCo", "BuffThaiSonApDinh",
        "BuffBatDongMinhVuong", "BuffVanVatQuyTran",
    ]


def test_new_effects_registered() -> None:
    for key in (
        "BuffKienNhuBanThach", "BuffKienCo", "BuffThaiSonApDinh",
        "BuffBatDongMinhVuong", "BuffVanVatQuyTran",
    ):
        assert EFFECTS.get(key) is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    assert p1.thanh_son_kien_co_on_hit is True
    assert p1.thanh_son_kien_co_cap == 8
    p3 = _player(3)
    assert p3.thanh_son_dmg_from_shield_pct == pytest.approx(0.35)
    assert p3.thanh_son_l3_full_bonus == pytest.approx(0.10)
    assert p3.thanh_son_bao_mon_chance == pytest.approx(0.60)
    assert p3.thanh_son_stun_chance == pytest.approx(0.35)
    p9 = _player(9)
    assert p9.thanh_son_immovable_enabled is True
    assert p9.thanh_son_survive_shield_pct == pytest.approx(0.30)


# ── 2. L1 Kiên Cố — defensive on-hit stack + scaling ────────────────────────


def test_l1_kien_co_accrues_on_hit_and_caps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    # ``player`` is the TARGET (being struck) → banks Kiên Cố. Strike 12× > cap 8.
    for _ in range(12):
        run_thanh_son_procs(session, enemy, player, dmg=1_000)
    assert player.thanh_son_kien_co_stacks == 8  # capped


def test_kien_co_scales_res_and_dr(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.thanh_son_kien_co_stacks = 5
    mods = get_combat_modifiers(player)
    assert mods.get("res_all", 0.0) == pytest.approx(5 * 0.02)        # +10%
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(5 * 0.015)  # +7.5%


# ── 3. L3 Thái Sơn Áp Đỉnh — per-cast shield→true-dmg + riders ──────────────


def _cast_enemy_loss(player, *, seed: int = 0) -> int:
    enemy = _enemy(hp=10**9)
    session = _session(player, enemy, seed=seed)
    session.rng.random = lambda: 0.99  # no riders/crits
    skill = registry.get_skill(_ATTACK)
    before = enemy.hp
    cast_skill(session, player, enemy, _ATTACK, dict(skill), skill.get("mp_cost", 0))
    return before - enemy.hp


def test_l3_true_dmg_from_shield(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.crit_rating = 0
    player.thanh_son_kien_co_stacks = 0      # below the 4-stack breakpoint
    player.shield = 200_000                  # 35% = 70k, well under the 12%-maxHP cap
    loss_on = _cast_enemy_loss(player)
    player.thanh_son_dmg_from_shield_pct = 0.0  # disable L3 → measure skill alone
    loss_off = _cast_enemy_loss(player)
    assert loss_on - loss_off == int(200_000 * 0.35)


def test_l3_full_bonus_at_4_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.crit_rating = 0
    player.thanh_son_kien_co_stacks = 4      # at the breakpoint → +10% ratio
    player.shield = 200_000                  # 45% = 90k, under the 12%-maxHP cap
    expected = int(200_000 * (0.35 + 0.10))  # match the code's float path (0.4499…)
    loss_on = _cast_enemy_loss(player)
    player.thanh_son_dmg_from_shield_pct = 0.0
    loss_off = _cast_enemy_loss(player)
    assert loss_on - loss_off == expected


def test_l3_true_dmg_capped_at_target_maxhp_pct(monkeypatch) -> None:
    """A colossal shield is capped at 12% of the target's max HP per cast."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.crit_rating = 0
    player.thanh_son_kien_co_stacks = 0
    player.shield = 10**8                    # 35% = 35M — far above the cap
    enemy = _enemy(hp=10**9)
    enemy.hp_max = 100_000                   # cap = 12% × 100k = 12_000
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    skill = registry.get_skill(_ATTACK)
    before = enemy.hp
    cast_skill(session, player, enemy, _ATTACK, dict(skill), skill.get("mp_cost", 0))
    loss_on = before - enemy.hp
    # disable L3, re-measure the skill alone on an identical enemy
    player.thanh_son_dmg_from_shield_pct = 0.0
    enemy2 = _enemy(hp=10**9)
    enemy2.hp_max = 100_000
    session2 = _session(player, enemy2)
    session2.rng.random = lambda: 0.99
    before2 = enemy2.hp
    cast_skill(session2, player, enemy2, _ATTACK, dict(skill), skill.get("mp_cost", 0))
    loss_off = before2 - enemy2.hp
    assert loss_on - loss_off == int(100_000 * 0.12)  # capped, not 35M


def test_l3_riders_apply_bao_mon_and_stun(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # both rider rolls land
    run_thanh_son_procs(session, player, enemy, dmg=1_000)  # player attacks → riders on enemy
    assert enemy.has_effect("DebuffBaoMon")
    assert enemy.has_effect("CCStun")


def test_l3_riders_skip_negated_hit(monkeypatch) -> None:
    """A hit fully negated to 0 damage triggers no CC riders and banks no stack."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # rolls would land if the gate let them
    run_thanh_son_procs(session, player, enemy, dmg=0)
    assert not enemy.has_effect("DebuffBaoMon")
    assert not enemy.has_effect("CCStun")


def test_no_posthumous_kien_co_stack(monkeypatch) -> None:
    """A holder killed earlier in the same cast (per-cast true-dmg rider) must
    not bank a Kiên Cố stack that would survive into a later revive."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0  # died mid-cast, before apply_reactive_damage ran
    run_thanh_son_procs(session, enemy, player, dmg=1_000)
    assert player.thanh_son_kien_co_stacks == 0


# ── 4. Kiên Cố weakness — hard CC strips a stack ────────────────────────────


def test_stun_strips_kien_co(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)  # L3: has Kiên Cố cap, no L6 debuff-immunity to resist the stun
    player.thanh_son_kien_co_stacks = 8
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    stun = EFFECTS.get("CCStun")
    assert stun is not None and stun.skips_turn
    inflict_debuff(session, "CCStun", stun, player, actor=enemy)
    assert player.thanh_son_kien_co_stacks == 7  # cracked exactly once, not twice
    assert player.thanh_son_kien_co_just_cracked is False  # consumed by the announce


def test_direct_apply_effect_cc_also_cracks_kien_co(monkeypatch) -> None:
    """The crack lives at the ``apply_effect`` stamp site — hard-CC paths that
    bypass ``inflict_debuff`` (Bắc Minh freeze, stun_on_hit, retaliate freezes)
    must crack too, or the L9 immovable gate has no counterplay against them."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.thanh_son_kien_co_stacks = player.thanh_son_kien_co_cap
    freeze = EFFECTS.get("DebuffDongBang")
    assert freeze is not None and freeze.skips_turn
    player.apply_effect("DebuffDongBang", 2)  # direct stamp — no inflict_debuff
    assert player.thanh_son_kien_co_stacks == player.thanh_son_kien_co_cap - 1
    assert player.thanh_son_kien_co_just_cracked is True  # fallback announcer flag


# ── 5. L6 Bất Động Minh Vương — crit-res / DR / debuff-shrug ────────────────


def test_l6_real_stats(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    assert player.crit_res_rating >= 280
    assert player.final_dmg_reduce >= 0.35
    assert player.debuff_immune_pct == pytest.approx(0.60)


# ── 6. L9 Vạn Vật Quy Trần — Kiên-Cố-gated last-stand ───────────────────────


def test_l9_survives_lethal_at_full_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.thanh_son_kien_co_stacks = player.thanh_son_kien_co_cap  # full → armed
    player.shield = 0
    player.hp = 5_000
    player.take_damage(10**9)
    assert player.hp == 1                            # cannot die from one hit
    assert player.shield > 0                         # 30% shield restored
    assert player.thanh_son_immovable_just_triggered is True


def test_l9_shield_restore_clamped_at_cap(monkeypatch) -> None:
    """The restore routes through ``add_shield`` — repeated triggers (bypass-shield
    true damage never drains the pool) must not compound shield past shield_cap."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.thanh_son_kien_co_stacks = player.thanh_son_kien_co_cap
    cap = player.shield_cap()
    assert cap > 0
    player.shield = cap                              # pool already full
    player.hp = 5_000
    player.take_damage(10**9, bypass_shield=True)    # lethal true damage
    assert player.hp == 1
    assert player.shield == cap                      # clamped — not 130% of cap


def test_l9_no_survive_below_full_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.thanh_son_kien_co_stacks = player.thanh_son_kien_co_cap - 1  # one short
    player.shield = 0
    player.hp = 5_000
    player.take_damage(10**9)
    assert player.hp == 0                            # mortal again


def test_l9_dot_bypasses_immortality(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.thanh_son_kien_co_stacks = player.thanh_son_kien_co_cap
    player.shield = 0
    player.hp = 5_000
    player.take_damage(10**9, is_dot=True)           # DoT ignores the last-stand
    assert player.hp == 0


# ── 7. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.thanh_son_kien_co_on_hit is False
    assert player.thanh_son_kien_co_cap == 0
    assert player.thanh_son_immovable_enabled is False
    enemy = _enemy()
    session = _session(player, enemy)
    # No Kiên Cố accrual while dormant.
    run_thanh_son_procs(session, enemy, player, dmg=1_000)
    assert player.thanh_son_kien_co_stacks == 0
    # No last-stand → a lethal hit kills.
    player.shield = 0
    player.hp = 5_000
    player.take_damage(10**9)
    assert player.hp == 0
