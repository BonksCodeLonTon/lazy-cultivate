"""v12 constitution — Trường Xuân Linh Mộc Thể (Eternal Spring Spirit-Wood, Mộc).

The first Mộc body on the Constitution-Process engine: a longevity/poison tank
that gases the enemy on every hit, dominates via slow, renews harder the more
debuffs it has stacked on the opponent, and — at L9 — refuses to die while the
spring still flows. These tests pin every mechanic on the flag-ON path plus the
flag-OFF inert seam. Phase-0 byte-identity is guarded separately by
``test_constitution_process_guard.py``.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.engine.damage.combat_hit import build_attack_stats
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.combat.procs import run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_TruongXuanLinhMoc"
_ATTACK_SKILL = "SkillAtkMoc1"
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 6
_PLAYER_REALM_TOTAL = 15


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="TruongXuanTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1,
        active_axis="qi",
        constitution_type=_BODY,
        linh_can=["moc"],
        linh_can_levels={"moc": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None, f"{_BODY!r} must be registered"
    assert data.get("process"), f"{_BODY!r} must carry a process block"
    return data


def _enemy(hp: int = 10**9):
    e = build_enemy_combatant(_ENEMY_KEY, player_realm_total=_PLAYER_REALM_TOTAL)
    assert e is not None
    e.hp = e.hp_max = hp
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.shield = 0
    e.poison_immunity = False
    return e


def _player(level: int):
    return build_player_combatant(_make_char(level), player_skill_keys=[_ATTACK_SKILL])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition (pure seam) ──────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "moc"
    assert data["rarity"] == "legendary"
    assert data["roll_weight"] == 0
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffLinhMocChiDoc"]


def test_composition_milestone_and_growth_math() -> None:
    data = _body_data()

    l3 = effective_stat_bonuses(data, 3)
    assert l3["matk_pct"] == pytest.approx(0.06 + 0.05 + 0.004 * 2)
    assert l3["slow_on_hit_pct"] == 0.40
    assert l3["moc_vs_slowed_dmg_bonus"] == 0.30

    l6 = effective_stat_bonuses(data, 6)
    assert l6["moc_regen_per_enemy_debuff"] == 0.01
    assert l6["moc_regen_debuff_cap"] == 8

    l9 = effective_stat_bonuses(data, 9)
    assert l9["matk_pct"] == pytest.approx(0.06 + (0.05 + 0.05 + 0.07) + 0.004 * 8)
    assert l9["hp_regen_pct"] == pytest.approx(0.03 + 0.02 + 0.0015 * 8)
    assert l9["dot_dmg_bonus"] == pytest.approx(0.10 + 0.006 * 8)
    assert l9["moc_undying_spring_enabled"] is True
    assert l9["moc_undying_cooldown_turns"] == 5
    assert l9["moc_undying_min_hp"] == 1
    assert l9["moc_undying_heal_reduce_gate"] == 0.50
    assert l9["moc_guaranteed_poison_on_attack"] is True
    assert l9["moc_guaranteed_poison_stacks"] == 1
    assert effective_effects(data, 9) == [
        "BuffLinhMocChiDoc", "BuffMocVuongThongLinh",
        "BuffTruongXuanHoiNguyen", "BuffTruongXuanBatTu",
    ]


# ── 2. L1 Linh Mộc Chi Độc — on-hit poison proc ─────────────────────────────


def test_l1_poison_proc_fires_on_forced_roll(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    assert player.has_effect("BuffLinhMocChiDoc")
    assert player.poison_on_hit_pct == pytest.approx(0.55)
    enemy = _enemy()
    session = _session(player, enemy)

    session.rng.random = lambda: 0.0  # < 0.55 → poison lands
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.has_effect(EffectKey.DEBUFF_DOC_TO)
    assert enemy.poison_stacks >= 1


def test_l1_poison_denied_on_high_roll(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # >= 0.55 → no poison
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.poison_stacks == 0


def test_l1_poison_inert_without_flag() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(1)
    # Flat path still stamps the L1 poison chance (it lives in the body's FLAT
    # stat_bonuses), but the proc only fires with a roll — pinned elsewhere.
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.poison_stacks == 0


# ── 3. L3 Mộc Vương Thống Lĩnh — +30% final dmg vs a slowed target ──────────


def test_l3_vs_slowed_adds_final_dmg_bonus(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    assert player.moc_vs_slowed_dmg_bonus == pytest.approx(0.30)
    enemy = _enemy()

    clean = build_attack_stats(player, enemy, {}, "moc")
    enemy.apply_effect(EffectKey.DEBUFF_LAM_CHAM, 2)
    slowed = build_attack_stats(player, enemy, {}, "moc")

    assert slowed.final_dmg_bonus - clean.final_dmg_bonus == pytest.approx(0.30)


def test_l3_no_bonus_vs_non_slowed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()  # not slowed
    # Two reads with no slow in between → identical bonus.
    a = build_attack_stats(player, enemy, {}, "moc")
    b = build_attack_stats(player, enemy, {}, "moc")
    assert a.final_dmg_bonus == pytest.approx(b.final_dmg_bonus)


def test_l3_vs_slowed_inert_without_flag() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(3)
    assert player.moc_vs_slowed_dmg_bonus == 0.0
    enemy = _enemy()
    enemy.apply_effect(EffectKey.DEBUFF_LAM_CHAM, 2)
    clean = build_attack_stats(player, enemy, {}, "moc")
    # Even though the target is slowed, the zero flag contributes nothing.
    enemy.effects.clear()
    unslowed = build_attack_stats(player, enemy, {}, "moc")
    assert clean.final_dmg_bonus == pytest.approx(unslowed.final_dmg_bonus)


# ── 4. L6 Trường Xuân Hồi Nguyên — heal per enemy debuff, capped ────────────


def test_l6_regen_scales_with_enemy_debuff_count(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    assert player.moc_regen_per_enemy_debuff == pytest.approx(0.01)
    player.hp_regen_pct = 0.0  # isolate the L6 heal from the base regen tick
    player.hp = player.hp_max // 2
    enemy = _enemy()
    # Three distinct DEBUFF-kind effects on the enemy.
    enemy.apply_effect(EffectKey.DEBUFF_DOC_TO, 3)
    enemy.apply_effect(EffectKey.DEBUFF_CHAY_MAU, 3)
    enemy.apply_effect(EffectKey.DEBUFF_LAM_CHAM, 3)
    session = _session(player, enemy)

    hp_before = player.hp
    session._process_periodic(player)
    healed = player.hp - hp_before
    assert healed == int(player.hp_max * 0.01 * 3)


def test_l6_regen_respects_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.hp_regen_pct = 0.0
    player.moc_regen_debuff_cap = 2  # force a low cap
    player.hp = player.hp_max // 2
    enemy = _enemy()
    enemy.apply_effect(EffectKey.DEBUFF_DOC_TO, 3)
    enemy.apply_effect(EffectKey.DEBUFF_CHAY_MAU, 3)
    enemy.apply_effect(EffectKey.DEBUFF_LAM_CHAM, 3)
    session = _session(player, enemy)

    hp_before = player.hp
    session._process_periodic(player)
    healed = player.hp - hp_before
    assert healed == int(player.hp_max * 0.01 * 2)  # capped at 2, not 3


def test_l6_regen_noop_when_enemy_clean(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.hp_regen_pct = 0.0
    player.hp = player.hp_max // 2
    enemy = _enemy()  # no debuffs
    session = _session(player, enemy)
    hp_before = player.hp
    session._process_periodic(player)
    assert player.hp == hp_before


# ── 5. L9 Trường Xuân Bất Tử — Undying Spring + guaranteed poison ───────────


def test_l9_undying_survives_lethal_then_locks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.moc_undying_spring_enabled is True
    assert player.moc_undying_cooldown_turns == 5
    enemy = _enemy()
    session = _session(player, enemy)

    # First lethal → trues out at 1 HP, cooldown armed.
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.hp == 1
    assert player.moc_undying_cd == 5

    # Second lethal while on cooldown → death is final.
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_l9_undying_rearms_after_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    assert session._try_revive(player) is True
    assert player.moc_undying_cd == 5

    # The PRE_TURN aura ticks the cooldown down on each acted turn.
    for _ in range(5):
        run_phase(TurnPhase.PRE_TURN, TurnContext(actor=player, target=enemy, session=session))
    assert player.moc_undying_cd == 0

    # Re-armed → a fresh lethal hit is survived again.
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.hp == 1
    assert player.moc_undying_cd == 5


def test_l9_undying_blocked_when_spring_choked_by_anti_heal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    # DebuffHuMoc stamps heal_taken_reduce 0.5 == the gate → spring NOT flowing.
    player.apply_effect("DebuffHuMoc", 4)

    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_l9_undying_blocked_when_regen_zeroed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp_regen_pct = 0.0  # spring dry → no Undying

    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_l9_guaranteed_poison_on_every_attack(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.moc_guaranteed_poison_on_attack is True
    assert player.moc_guaranteed_poison_stacks == 1
    enemy = _enemy()
    session = _session(player, enemy)

    # Force every chance roll to MISS (0.99 >= 0.55 poison, >= 0.40 slow) so the
    # ONLY poison that lands is the L9 guaranteed stack.
    session.rng.random = lambda: 0.99
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.poison_stacks == 1


def test_l9_guaranteed_poison_respects_immunity(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    enemy.poison_immunity = True
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.poison_stacks == 0


# ── 6. Inertness — flag OFF leaves the body flat, no buffs, paths no-op ──────


def test_flag_off_no_effects_stamped() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    for key in (
        "BuffLinhMocChiDoc", "BuffMocVuongThongLinh",
        "BuffTruongXuanHoiNguyen", "BuffTruongXuanBatTu",
    ):
        assert not player.has_effect(key)
    # Milestone-only flags (L3/L6/L9) stay at their inert defaults on the flat path.
    assert player.moc_vs_slowed_dmg_bonus == 0.0
    assert player.moc_regen_per_enemy_debuff == 0.0
    assert player.moc_undying_spring_enabled is False
    assert player.moc_guaranteed_poison_on_attack is False


def test_flag_off_resolves_to_flat_stat_bonuses() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)
    data = _body_data()
    assert flat == data["stat_bonuses"]
    assert "moc_vs_slowed_dmg_bonus" not in flat
    assert "moc_undying_spring_enabled" not in flat


def test_flag_off_revive_and_periodic_paths_noop() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    # L9 Undying inert → a lethal hit is final.
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()

    # L6 periodic regen inert → no heal even with a fully debuffed enemy.
    player.hp = player.hp_max // 2
    player.hp_regen_pct = 0.0
    enemy.apply_effect(EffectKey.DEBUFF_DOC_TO, 3)
    enemy.apply_effect(EffectKey.DEBUFF_CHAY_MAU, 3)
    hp_before = player.hp
    session._process_periodic(player)
    assert player.hp == hp_before
