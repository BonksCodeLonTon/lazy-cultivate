"""Verify enemy stat caps match player caps for resistance and damage reduce.

Regression guard: a JSON entry that sets ``final_dmg_reduce: 0.95`` on an
enemy or world boss must be clamped to ``MAX_FINAL_DMG_REDUCE`` (the same
cap players are subject to via ``character_stats.compute_combat_stats``).
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.constants.balance import MAX_ELEMENTAL_RES, MAX_FINAL_DMG_REDUCE
from src.game.systems.combat.builders import (
    build_enemy_combatant, build_world_boss_combatant,
)


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _first_enemy_key() -> str:
    """Pick any registered enemy — we only need a real key the builder accepts."""
    for k, data in registry.enemies.items():
        if "base_hp" in data and data.get("rank"):
            return k
    raise RuntimeError("No suitable enemy in registry")


def test_enemy_final_dmg_reduce_clamped_to_player_cap(monkeypatch):
    key = _first_enemy_key()
    enemy_data = dict(registry.enemies[key])
    # Pretend a JSON entry tried to set 0.95 — the builder must clamp.
    enemy_data["final_dmg_reduce"] = 0.95

    monkeypatch.setitem(registry.enemies, key, enemy_data)
    combatant = build_enemy_combatant(key, player_realm_total=10)
    assert combatant is not None
    assert combatant.final_dmg_reduce == MAX_FINAL_DMG_REDUCE


def test_world_boss_final_dmg_reduce_clamped_to_player_cap():
    boss_data = {
        "key": "TestBoss",
        "vi": "Test Boss",
        "base_hp": 100_000,
        "base_spd": 12,
        "skill_pool": [],
        "final_dmg_reduce": 0.99,  # Above cap
    }
    combatant = build_world_boss_combatant(boss_data, current_hp=100_000, player_realm_total=10)
    assert combatant.final_dmg_reduce == MAX_FINAL_DMG_REDUCE


def test_enemy_resistance_cap_uses_player_constant():
    """Enemy ``base_res`` overrides clamp to ``MAX_ELEMENTAL_RES`` (via the
    JSON-tunable ``res_cap_pct`` whose default is the player cap)."""
    key = _first_enemy_key()
    enemy_data = dict(registry.enemies[key])
    enemy_data["base_res"] = {"hoa": 0.99}  # try to set 99%

    # Don't override res_cap_pct → defaults to MAX_ELEMENTAL_RES
    enemy_data.pop("res_cap_pct", None)

    # Build via direct registry mutation
    registry.enemies[key] = enemy_data
    try:
        combatant = build_enemy_combatant(key, player_realm_total=10)
        assert combatant is not None
        assert combatant.resistances.get("hoa", 0.0) == MAX_ELEMENTAL_RES
    finally:
        # registry will be reloaded by the session fixture if needed
        registry.load()


def test_caps_are_the_constants_not_magic_numbers():
    """Smoke check that the caps come from named constants, not stray
    literals — guards against future drift between player and enemy caps."""
    assert MAX_ELEMENTAL_RES == pytest.approx(0.90)
    assert MAX_FINAL_DMG_REDUCE == pytest.approx(0.90)
