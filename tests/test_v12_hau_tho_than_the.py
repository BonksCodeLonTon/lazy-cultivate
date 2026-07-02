"""v12 constitution — Hậu Thổ Thần Thể (Great Earth God Body, Thổ).

The 2nd Thổ body: an HP-VAMPIRE growth juggernaut (vs Kim Cang Bất Hoại's static
shield-wall + physical-negate). Identity: eat the enemy, become a mountain, crush
back with unmitigable true damage.

  L1 Đại Địa Tức Nhưỡng — +5% HP/+6% MP regen; each damaging strike (dmg>0, live
                          actor, not vs world boss) siphons 4% of the foe's CURRENT
                          HP → permanently GROWS the holder's max HP + heals via the
                          central heal pipeline (heal-reduction counters apply) and
                          banks it in the steal pool. Seeds Địa Mạch: every 1%
                          of the foe's MAX HP siphoned → +1 tier (cap 10; +0.5%
                          steal & +3% shield→dmg per tier; +10% max HP at 10).
  L3 Trọng Lực Chưởng Khống — per CAST, bonus TRUE damage = max(25% max HP, 40%
                          shield + tier bump), pierces resistance & shield, capped
                          at 12% of the TARGET's max HP per cast.
  L6 Địa Mẫu Hộ Trì    — +30% final_dmg_reduce, +18% res_all, big HP regen.
  L9 Luân Hồi Quy Tắc  — on a lethal hit, survive once with HP = total HP stolen
                          this battle (0 stolen → no save).

Reuses: the on-hit attacker proc seam (run_hau_tho_procs, like run_bac_minh_procs),
the shared _deal_capped_true_dmg per-cast rider (gated not _suppress_extras), and
the ON_REVIVE hook seam (dedicated priority-5 hook like Niết Bàn / Chân Dương).

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so it
auto-reverts; the dormant default is never left mutated.
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
from src.game.systems.combat.procs import run_hau_tho_procs
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_HauThoThanThe"
_ATTACK = "SkillKimTripleStrike"   # physical, lands reliably
_ENEMY = "TinhKimTho"


def _make_char(level: int | None = None, *, linh_can: list[str] | None = None) -> Character:
    lc = linh_can if linh_can is not None else ["tho"]
    return Character(
        player_id=1, discord_id=1, name="HauThoTester",
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
        "hau_tho_hp_steal_pct", "hau_tho_accumulate", "hau_tho_dmg_from_maxhp_pct",
        "hau_tho_rebirth_enabled",
    ):
        assert cfg not in flat
    assert flat["hp_pct"] == 0.12


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffDaiDiaTucNhuong", "BuffDiaMachHapThu"]
    assert effective_effects(data, 9) == [
        "BuffDaiDiaTucNhuong", "BuffDiaMachHapThu",
        "BuffTrongLucChuongKhong", "BuffDiaMauHoTri", "BuffLuanHoiQuyTac",
    ]


def test_new_effects_registered() -> None:
    for key in (
        "BuffDaiDiaTucNhuong", "BuffDiaMachHapThu", "BuffTrongLucChuongKhong",
        "BuffDiaMauHoTri", "BuffLuanHoiQuyTac",
    ):
        assert EFFECTS.get(key) is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    assert p1.hau_tho_hp_steal_pct == pytest.approx(0.04)
    assert p1.hau_tho_accumulate is True
    p3 = _player(3)
    assert p3.hau_tho_dmg_from_maxhp_pct == pytest.approx(0.25)
    assert p3.hau_tho_dmg_from_shield_pct == pytest.approx(0.40)
    assert _player(9).hau_tho_rebirth_enabled is True


# ── 2. L1 Đại Địa Tức Nhưỡng — HP-steal → max-HP growth ─────────────────────


def test_l1_steal_grows_maxhp_heals_and_drains(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.hau_tho_accumulate = False  # isolate the raw steal from tier growth
    enemy = _enemy(hp=100_000)
    session = _session(player, enemy)
    hp_max0 = player.hp_max
    player.hp = hp_max0 - 30_000  # room to heal
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    stolen = int(100_000 * 0.04)  # 4000
    assert enemy.hp == 100_000 - stolen           # foe drained
    assert player.hp_max == hp_max0 + stolen      # permanent max-HP growth
    assert player.hp == (hp_max0 - 30_000) + stolen  # healed by the same amount
    assert player.hau_tho_stolen_total == stolen  # banked for L9


def test_l1_no_steal_when_flag_off() -> None:
    # Dormant default — no monkeypatch.
    assert settings.constitution_process_enabled is False
    player = _player(1)
    enemy = _enemy(hp=100_000)
    session = _session(player, enemy)
    hp_max0 = player.hp_max
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    assert enemy.hp == 100_000
    assert player.hp_max == hp_max0
    assert player.hau_tho_stolen_total == 0


def test_l1_no_steal_on_negated_hit(monkeypatch) -> None:
    """A hit fully negated to 0 damage (Kim Cang Kim Thân) steals nothing."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy(hp=100_000)
    session = _session(player, enemy)
    hp_max0 = player.hp_max
    run_hau_tho_procs(session, player, enemy, dmg=0)
    assert enemy.hp == 100_000
    assert player.hp_max == hp_max0
    assert player.hau_tho_stolen_total == 0


def test_l1_dead_actor_does_not_unheal_death(monkeypatch) -> None:
    """An actor killed mid-pass (e.g. by reflect, which runs before this proc in
    ``apply_reactive_damage``) must NOT be resurrected by the steal-heal — death
    has to go through the ON_REVIVE cascade, spending revive charges."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy(hp=100_000)
    session = _session(player, enemy)
    player.hp = 0  # reflect-killed earlier in the same reactive pass
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    assert player.hp == 0            # stays dead
    assert enemy.hp == 100_000       # a corpse siphons nothing


def test_l1_no_steal_from_world_boss(monkeypatch) -> None:
    """The shared world-boss pool is off-limits to hp_max-mutation mechanics —
    same guard convention as the Âm soul-drain (builders sets ``is_world_boss``)."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy(hp=10**9)
    enemy.is_world_boss = True
    session = _session(player, enemy)
    hp_max0 = player.hp_max
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    assert enemy.hp == 10**9
    assert player.hp_max == hp_max0
    assert player.hau_tho_stolen_total == 0


def test_l1_steal_heal_respects_heal_reduction(monkeypatch) -> None:
    """The HP top-up routes through ``session._apply_heal`` — a heal-lock debuff
    (Hủ Thủy Ấn, heal_taken_reduce 0.40) reduces the sustain, while the max-HP
    growth and the L9 bank stay at the full stolen amount (growth isn't a heal)."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.hau_tho_accumulate = False  # isolate the raw steal from tier growth
    player.apply_effect("DebuffHuThuyAn", 3)
    enemy = _enemy(hp=100_000)
    session = _session(player, enemy)
    hp_max0 = player.hp_max
    player.hp = hp_max0 - 30_000  # room to heal
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    stolen = int(100_000 * 0.04)               # 4000 drained + grown + banked
    assert enemy.hp == 100_000 - stolen
    assert player.hp_max == hp_max0 + stolen
    assert player.hau_tho_stolen_total == stolen
    healed = int(stolen * (1.0 - 0.40))        # top-up reduced to 2400
    assert player.hp == (hp_max0 - 30_000) + healed


# ── 3. Địa Mạch Hấp Thụ — tier accumulation ────────────────────────────────


def test_stack_accrues_tiers_from_steal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy(hp=100_000)  # full HP → one 4%-steal = 0.04 progress = 4 tiers
    session = _session(player, enemy)
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    assert player.hau_tho_tier == 4  # 0.04 / 0.01 per tier


def test_stack_per_tier_raises_steal_rate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.hau_tho_tier = 4  # rate = 0.04 + 4×0.005 = 0.06
    player.hau_tho_accumulate = False  # freeze tier so we read the raised rate cleanly
    enemy = _enemy(hp=100_000)
    session = _session(player, enemy)
    run_hau_tho_procs(session, player, enemy, dmg=1_000)
    assert player.hau_tho_stolen_total == int(100_000 * 0.06)  # 6000, not 4000


def test_stack_caps_at_10_and_grants_maxhp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy(hp=10**9)
    session = _session(player, enemy)
    # Many strikes — tier must clamp at 10 and the one-time +10% maxHP fires once.
    hp_before_cap = None
    for _ in range(8):
        if player.hau_tho_tier >= 10 and hp_before_cap is None:
            hp_before_cap = player.hp_max
        run_hau_tho_procs(session, player, enemy, dmg=1_000)
    assert player.hau_tho_tier == 10
    assert player.hau_tho_tier10_applied is True


# ── 4. L3 Trọng Lực Chưởng Khống — per-cast true damage ─────────────────────


def _cast_enemy_loss(player, *, seed: int = 0) -> int:
    enemy = _enemy(hp=10**9)
    session = _session(player, enemy, seed=seed)
    session.rng.random = lambda: 0.99  # no procs/crits noise
    skill = registry.get_skill(_ATTACK)
    before = enemy.hp
    cast_skill(session, player, enemy, _ATTACK, dict(skill), skill.get("mp_cost", 0))
    return before - enemy.hp


def test_l3_true_dmg_from_maxhp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.hau_tho_hp_steal_pct = 0.0   # isolate L3 from the L1 steal (keeps hp_max fixed)
    player.crit_rating = 0
    player.shield = 0
    hp_max = player.hp_max
    loss_on = _cast_enemy_loss(player)
    # Same player with L3 disabled → measures the skill damage alone.
    player.hau_tho_dmg_from_maxhp_pct = 0.0
    player.hau_tho_dmg_from_shield_pct = 0.0
    loss_off = _cast_enemy_loss(player)
    assert loss_on - loss_off == int(hp_max * 0.25)  # the per-cast true-dmg rider


def test_l3_true_dmg_picks_higher_shield(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.hau_tho_hp_steal_pct = 0.0
    player.crit_rating = 0
    # Big shield so the shield branch (40%) beats the maxHP branch (25%).
    player.shield = player.hp_max * 4
    loss_on = _cast_enemy_loss(player)
    player.hau_tho_dmg_from_maxhp_pct = 0.0
    player.hau_tho_dmg_from_shield_pct = 0.0
    loss_off = _cast_enemy_loss(player)
    expected = max(int(player.hp_max * 0.25), int(player.shield * 0.40))
    assert loss_on - loss_off == expected


def test_l3_true_dmg_capped_at_target_maxhp_pct(monkeypatch) -> None:
    """A colossal shield/maxHP is capped at 12% of the target's max HP per cast."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.hau_tho_hp_steal_pct = 0.0
    player.crit_rating = 0
    player.shield = 10**8                    # 40% = 40M — far above the cap
    enemy = _enemy(hp=10**9)
    enemy.hp_max = 100_000                   # cap = 12% × 100k = 12_000
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    skill = registry.get_skill(_ATTACK)
    before = enemy.hp
    cast_skill(session, player, enemy, _ATTACK, dict(skill), skill.get("mp_cost", 0))
    loss_on = before - enemy.hp
    player.hau_tho_dmg_from_maxhp_pct = 0.0
    player.hau_tho_dmg_from_shield_pct = 0.0
    enemy2 = _enemy(hp=10**9)
    enemy2.hp_max = 100_000
    session2 = _session(player, enemy2)
    session2.rng.random = lambda: 0.99
    before2 = enemy2.hp
    cast_skill(session2, player, enemy2, _ATTACK, dict(skill), skill.get("mp_cost", 0))
    loss_off = before2 - enemy2.hp
    assert loss_on - loss_off == int(100_000 * 0.12)  # capped, not 40M


# ── 5. L6 Địa Mẫu Hộ Trì — DR / res / regen ────────────────────────────────


def test_l6_dr_res(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    assert player.final_dmg_reduce >= 0.30
    assert player.resistances.get("tho", 0.0) >= 0.18


# ── 6. L9 Luân Hồi Quy Tắc — lethal-survive with stolen total ───────────────


def test_l9_survives_with_stolen_total(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.hau_tho_stolen_total = 123_456
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.hp == min(player.hp_max, 123_456)
    assert player.hau_tho_rebirth_used is True


def test_l9_no_save_without_steal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.hau_tho_stolen_total = 0  # never vampired → no rebirth
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_l9_once_per_fight(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.hau_tho_stolen_total = 50_000
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is True
    player.hp = 0
    assert session._try_revive(player) is False  # charge spent


# ── 7. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.hau_tho_hp_steal_pct == pytest.approx(0.0)
    assert player.hau_tho_accumulate is False
    assert player.hau_tho_dmg_from_maxhp_pct == pytest.approx(0.0)
    assert player.hau_tho_rebirth_enabled is False
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False  # dormant → death is final
