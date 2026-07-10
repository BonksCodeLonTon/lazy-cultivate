"""Live-combat power benchmark — every body must fit the tunable fight envelope.

Complements ``test_constitution_balance_bands`` (stat-sheet envelope): custom
mechanics (procs, cadences, riders, forms) only show their real power in
combat, so each L9 body runs a standardized seeded fight — fixed generic kit,
fixed anchor elite, fixed realm shape — and its win rate / average
turns-to-kill / remaining effective pool (HP + shield) must land inside the
``combat_benchmark.thresholds`` block of ``docs/constitution_balance_bands.json``.

Reading the thresholds (calibrated 2026-07-08, baseline Phàm Thể = 14.0 turns
/ 50% pool):
  * ``avg_ttk_min``  — over-tune tripwire: no body may kill meaningfully
    faster than the current top assassin (ThaiBachCanhKim, 3.8).
  * ``avg_ttk_max`` / ``end_pool_min`` — a body must at least match the
    no-constitution baseline: not slower AND not squishier than bare Phàm Thể.
To admit a deliberately stronger/slower body, tune the JSON — the failure IS
the review checkpoint.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatEndReason, CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.utils.config import settings

BANDS_PATH = Path(__file__).parent.parent / "docs" / "constitution_balance_bands.json"
_BENCH = json.loads(BANDS_PATH.read_text(encoding="utf-8"))["combat_benchmark"]


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


@pytest.fixture()
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


def _process_bodies() -> list[str]:
    return [c["key"] for c in registry.constitutions.values() if c.get("process")]


def _bench_char(body_key: str) -> Character:
    cfg = _BENCH["char"]
    return Character(
        player_id=1, discord_id=1, name="Bench",
        body_realm=cfg["body_realm"], body_level=1,
        qi_realm=cfg["qi_realm"], qi_level=1,
        formation_realm=cfg["formation_realm"], formation_level=1,
        active_axis=cfg["active_axis"],
        constitution_type=body_key,
        linh_can=[cfg["linh_can"]],
        linh_can_levels={cfg["linh_can"]: cfg["linh_can_level"]},
        constitution_levels={body_key: 9},
        stats=CharacterStats(),
    )


def run_benchmark(body_key: str) -> tuple[int, float, float]:
    """Return (wins, avg_ttk, avg_end_pool) over the configured seeds.

    ``avg_ttk`` is averaged over the WINNING fights (99.0 when none);
    ``end_pool`` is (hp + shield) / (hp_max + shield_cap) so the shield-only
    aegis body measures on its real pool.
    """
    # Restricted-path bodies (e.g. the MATK-locked chaos brute) bench with a
    # LEGAL kit of the same statistical power — declared in kit_overrides.
    skills = _BENCH.get("kit_overrides", {}).get(body_key, _BENCH["skills"])
    wins, ttks, pools = 0, [], []
    for seed in _BENCH["seeds"]:
        player = build_player_combatant(_bench_char(body_key), skills)
        player.mp = player.mp_max = 99_999
        enemy = build_enemy_combatant(
            _BENCH["enemy_key"], _BENCH["player_realm_total_vs_enemy"],
        )
        assert enemy is not None, _BENCH["enemy_key"]
        enemy.mp = enemy.mp_max = 99_999
        session = CombatSession(
            player=player, enemy=enemy, player_skill_keys=skills,
            rng=random.Random(seed), max_turns=_BENCH["max_turns"],
        )
        result = session.run()
        if result.reason == CombatEndReason.PLAYER_WIN:
            wins += 1
            ttks.append(result.turns)
        cap = player.hp_max + player.shield_cap()
        pools.append((player.hp + player.shield) / cap if cap else 0.0)
    avg_ttk = sum(ttks) / len(ttks) if ttks else 99.0
    return wins, avg_ttk, sum(pools) / len(pools)


def test_anchor_enemy_registered():
    assert registry.get_enemy(_BENCH["enemy_key"]) is not None


@pytest.mark.parametrize("body_key", _process_bodies() or ["<none>"])
def test_body_within_combat_envelope(body_key, _flag_on):
    th = _BENCH["thresholds"]
    wins, avg_ttk, end_pool = run_benchmark(body_key)
    problems = []
    if wins < th["min_wins"]:
        problems.append(f"wins {wins} < {th['min_wins']}")
    if avg_ttk < th["avg_ttk_min"]:
        problems.append(
            f"avg_ttk {avg_ttk:.1f} < {th['avg_ttk_min']} — OVER-tuned "
            "(kills faster than the roster's top assassin band)"
        )
    if avg_ttk > th["avg_ttk_max"]:
        problems.append(
            f"avg_ttk {avg_ttk:.1f} > {th['avg_ttk_max']} — slower than the "
            "bare Phàm Thể baseline"
        )
    if end_pool < th["end_pool_min"]:
        problems.append(
            f"end_pool {end_pool:.2f} < {th['end_pool_min']} — squishier than "
            "the bare Phàm Thể baseline"
        )
    assert not problems, (
        f"{body_key} outside the combat envelope — retune the body or "
        f"deliberately adjust docs/constitution_balance_bands.json "
        f"combat_benchmark.thresholds:\n  " + "\n  ".join(problems)
    )
