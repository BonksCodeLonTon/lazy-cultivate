"""Tests for Phase-Lock invulnerability mode (Chung Yên — Thập Nhật Chung Yên).

When a boss carrying ``phase_lock_config`` drops below the configured HP
threshold for the first time, it heals to full, gains MAX final_dmg_reduce +
MAX res on every element + a shield equal to its hp_max, and skips its own
turns. Each round those bonuses decay linearly back toward the pre-phase
profile; if the timer expires without a kill, the player dies.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.constants.balance import MAX_ELEMENTAL_RES, MAX_FINAL_DMG_REDUCE
from src.game.systems.combat import CombatSession, build_enemy_combatant
from src.game.systems.combat import phase as phase_lock
from src.game.systems.combatant import Combatant


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _make_combatant(key: str = "p", **overrides) -> Combatant:
    defaults = dict(
        name=key,
        hp=10_000, hp_max=10_000,
        mp=500, mp_max=500,
        spd=10, element=None,
        atk=100, matk=100, def_stat=20,
    )
    # Caller-supplied keys win over defaults, including ``name`` and ``hp``.
    defaults.update(overrides)
    return Combatant(key=key, **defaults)


def _make_boss(**overrides) -> Combatant:
    cfg = {
        "trigger_hp_pct": 0.5,
        "duration": 10,
        "phase_name_vi": "Thập Nhật Chung Yên",
        "phase_emoji": "☀️",
    }
    base = dict(
        hp=10_000, hp_max=10_000,
        final_dmg_reduce=0.10,
        resistances={"kim": 0.20, "hoa": 0.30},
        phase_lock_config=cfg,
    )
    base.update(overrides)
    return _make_combatant(key="boss", name="boss", **base)


def _make_session(player: Combatant, enemy: Combatant, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed),
        max_turns=20,
    )


# ── Trigger conditions ──────────────────────────────────────────────────────


def test_trigger_does_not_fire_above_threshold():
    boss = _make_boss(hp=8_000)
    fired = phase_lock.try_trigger(boss, log=[])
    assert fired is False
    assert boss.phase_lock_active is False


def test_trigger_fires_at_exact_threshold():
    boss = _make_boss(hp=5_000)
    log: list[str] = []
    fired = phase_lock.try_trigger(boss, log=log)
    assert fired is True
    assert boss.phase_lock_active is True
    assert boss.phase_lock_triggered is True
    assert boss.phase_lock_remaining == 10
    assert any("Thập Nhật Chung Yên" in line for line in log)


def test_trigger_heals_to_full_and_grants_shield():
    boss = _make_boss(hp=4_000, hp_max=10_000)
    phase_lock.try_trigger(boss, log=[])
    assert boss.hp == 10_000
    assert boss.shield == 10_000
    assert boss.shield_max_base >= 10_000


def test_trigger_caps_dr_and_resistances_at_max():
    boss = _make_boss(hp=4_000)
    phase_lock.try_trigger(boss, log=[])
    assert boss.final_dmg_reduce == MAX_FINAL_DMG_REDUCE
    # Every element seen by the player skill set is forced to MAX, including
    # ones the boss didn't originally carry.
    for elem in ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am"):
        assert boss.resistances[elem] == MAX_ELEMENTAL_RES


def test_trigger_only_fires_once_even_after_phase_ends():
    boss = _make_boss(hp=4_000)
    phase_lock.try_trigger(boss, log=[])
    # Force-clear the active flag and drop HP — second trigger must not fire.
    boss.phase_lock_active = False
    boss.hp = 1_000
    fired = phase_lock.try_trigger(boss, log=[])
    assert fired is False


def test_trigger_does_not_fire_at_zero_hp():
    """Lethal damage routes to phoenix-revive first; phase only fires while
    the boss is still drawing breath."""
    boss = _make_boss(hp=0)
    fired = phase_lock.try_trigger(boss, log=[])
    assert fired is False


def test_trigger_skips_when_no_config():
    boss = _make_combatant("plain", hp=1_000, hp_max=10_000)
    assert phase_lock.try_trigger(boss, log=[]) is False
    assert boss.phase_lock_active is False


# ── Decay & expiry ──────────────────────────────────────────────────────────


def test_tick_decays_dr_linearly():
    boss = _make_boss(hp=4_000, final_dmg_reduce=0.10)
    phase_lock.try_trigger(boss, log=[])
    starting_dr = boss.final_dmg_reduce
    step = boss.phase_lock_dr_step
    assert step > 0

    phase_lock.tick(boss, _make_combatant("p"), log=[])
    assert boss.final_dmg_reduce == pytest.approx(starting_dr - step)


def test_tick_clamps_decay_at_original():
    """Repeated decay never undershoots the pre-phase value."""
    boss = _make_boss(hp=4_000, final_dmg_reduce=0.10)
    phase_lock.try_trigger(boss, log=[])
    for _ in range(20):  # over-decay
        phase_lock.tick(boss, _make_combatant("p"), log=[])
    assert boss.final_dmg_reduce >= 0.10 - 1e-6


def test_tick_returns_false_when_phase_inactive():
    boss = _make_boss(hp=10_000)
    assert phase_lock.tick(boss, _make_combatant("p"), log=[]) is False


def test_tick_kills_player_on_expiry():
    boss = _make_boss(hp=4_000)
    player = _make_combatant("p", hp=10_000)
    phase_lock.try_trigger(boss, log=[])

    expired = False
    for _ in range(10):
        expired = phase_lock.tick(boss, player, log=[])
    assert expired is True
    assert player.hp == 0
    assert boss.phase_lock_active is False


def test_tick_restores_pre_phase_profile_on_expiry():
    boss = _make_boss(hp=4_000, final_dmg_reduce=0.10)
    original_res = dict(boss.resistances)
    phase_lock.try_trigger(boss, log=[])
    for _ in range(10):
        phase_lock.tick(boss, _make_combatant("p"), log=[])
    assert boss.final_dmg_reduce == pytest.approx(0.10)
    for elem, val in original_res.items():
        assert boss.resistances[elem] == pytest.approx(val)


# ── Session integration ────────────────────────────────────────────────────


def test_phase_active_skips_enemy_turn():
    """A boss inside the phase must not deal damage during its turn."""
    player = _make_combatant("p", hp=10_000, hp_max=10_000)
    boss = _make_boss(hp=4_000)
    session = _make_session(player, boss)
    phase_lock.try_trigger(boss, log=session.log)

    hp_before = player.hp
    session._take_turn(boss, player)
    assert player.hp == hp_before


def test_player_attack_below_threshold_arms_phase():
    player = _make_combatant("p", hp=10_000, hp_max=10_000)
    boss = _make_boss(hp=10_000, hp_max=10_000)
    session = _make_session(player, boss)
    # Simulate a hit that takes the boss to 40% HP — player turn cycle then
    # checks the trigger gate.
    boss.hp = 4_000
    result = session._actor_phase(player, boss, actor_is_player=True)
    assert result is None
    assert boss.phase_lock_active is True


def test_session_step_expires_phase_and_returns_defeat():
    """Full session run: trigger phase, let timer elapse, player dies."""
    player = _make_combatant("p", hp=10_000, hp_max=10_000, atk=0, matk=0)
    boss = _make_boss(hp=4_000, hp_max=10_000)
    session = _make_session(player, boss)
    # Fire the phase manually before the loop so we don't rely on damage RNG.
    phase_lock.try_trigger(boss, log=session.log)

    result = None
    for _ in range(15):
        _, result = session.step()
        if result is not None:
            break
    assert result is not None
    assert result.reason.value == "player_dead"


# ── Registry: BossChungYen ships the phase-lock config ─────────────────────


def test_registry_has_chung_yen_with_phase_lock():
    enemy = registry.get_enemy("BossChungYen")
    assert enemy is not None
    cfg = enemy.get("phase_lock")
    assert cfg is not None
    assert cfg["trigger_hp_pct"] == pytest.approx(0.5)
    assert cfg["duration"] == 10
    assert "Thập Nhật" in cfg["phase_name_vi"]


def test_build_enemy_combatant_propagates_phase_config():
    boss = build_enemy_combatant("BossChungYen", player_realm_total=80)
    assert boss is not None
    assert boss.phase_lock_config is not None
    assert boss.phase_lock_config["duration"] == 10
    # JSON dict is copied — mutating the live combatant must not leak back.
    boss.phase_lock_config["duration"] = 1
    fresh = build_enemy_combatant("BossChungYen", player_realm_total=80)
    assert fresh is not None
    assert fresh.phase_lock_config["duration"] == 10
