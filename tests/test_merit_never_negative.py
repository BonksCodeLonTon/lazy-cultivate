"""Audit merit-deduction sites for negative-balance possibilities.

Each subtraction site in the codebase should either (a) refuse to mutate
when ``merit < cost`` or (b) be guarded by a caller that does the same.
If we let merit go negative, every later "do you have N Công Đức?" check
silently passes (negative number < cost is still true compared the wrong
way, etc.) and players can amass unlimited spend below zero.
"""
from __future__ import annotations

import pytest

from src.game.models.character import Character, CharacterStats
from src.game.systems.alchemy import craft_pill
from src.game.systems.cultivation import (
    FORMATION_BREAKTHROUGH_MERIT,
    apply_breakthrough,
    can_breakthrough,
    consume_breakthrough_costs,
    study_formation_with_merit,
)


def _char(merit: int = 0, **kw) -> Character:
    return Character(
        player_id=1, discord_id=1, name="X",
        stats=CharacterStats(),
        merit=merit,
        **kw,
    )


# ─── study_formation_with_merit ──────────────────────────────────────────
def test_study_formation_rejects_overspend():
    char = _char(merit=100)
    res = study_formation_with_merit(char, 1000)
    assert not res["success"]
    assert char.merit == 100, "merit must not be deducted on rejected request"


def test_study_formation_rejects_negative_request():
    char = _char(merit=100)
    res = study_formation_with_merit(char, -50)
    assert not res["success"]
    assert char.merit == 100


def test_study_formation_rejects_zero_request():
    char = _char(merit=100)
    res = study_formation_with_merit(char, 0)
    assert not res["success"]
    assert char.merit == 100


# ─── alchemy craft_pill ──────────────────────────────────────────────────
def test_alchemy_rejects_when_merit_insufficient():
    """A recipe that costs more merit than the player has should bounce
    out before ``char.merit -= cost`` runs. We use a real recipe so the
    validator path matches production."""
    from src.data.registry import GameRegistry
    reg = GameRegistry.get()
    # Find any recipe with cost > 0; first one will do.
    recipe = next(
        (r for r in reg.pill_recipes.values() if int(r.get("cost_cong_duc", 0)) > 0),
        None,
    )
    assert recipe is not None, "expected at least one recipe with merit cost"

    cost = int(recipe["cost_cong_duc"])
    char = _char(merit=cost - 1)
    res = craft_pill(char, recipe["key"], inventory_map={}, owned_furnace_keys=())
    assert not res.success
    assert char.merit == cost - 1, "merit must not move on a rejected craft"


# ─── breakthrough flow ──────────────────────────────────────────────────
def test_can_breakthrough_blocks_formation_when_merit_insufficient():
    """``can_breakthrough`` is the proper gate — it must reject formation
    breakthroughs that would drive merit negative."""
    realm_with_cost = next(
        (r for r, c in FORMATION_BREAKTHROUGH_MERIT.items() if c > 0),
        None,
    )
    assert realm_with_cost is not None

    cost = FORMATION_BREAKTHROUGH_MERIT[realm_with_cost]
    char = _char(
        merit=0,
        formation_realm=realm_with_cost,
        formation_level=9,
        formation_xp=10_000_000,  # past bậc-9 threshold so EXP gate passes
    )
    ok, reason = can_breakthrough(char, "formation")
    assert not ok, "merit gate should block formation breakthrough"
    assert "Công Đức" in reason


def test_can_breakthrough_allows_formation_when_merit_sufficient():
    realm_with_cost = next(
        (r for r, c in FORMATION_BREAKTHROUGH_MERIT.items() if c > 0),
        None,
    )
    cost = FORMATION_BREAKTHROUGH_MERIT[realm_with_cost]
    char = _char(
        merit=cost,
        formation_realm=realm_with_cost,
        formation_level=9,
        formation_xp=10_000_000,
    )
    ok, _ = can_breakthrough(char, "formation")
    assert ok


def test_apply_breakthrough_drives_merit_negative_when_unguarded():
    """Reproduces the actual bug: with 0 merit and a non-zero formation
    breakthrough cost, ``apply_breakthrough`` happily subtracts and
    leaves merit < 0."""
    realm_with_cost = next(
        (r for r, c in FORMATION_BREAKTHROUGH_MERIT.items() if c > 0),
        None,
    )
    assert realm_with_cost is not None

    cost = FORMATION_BREAKTHROUGH_MERIT[realm_with_cost]
    char = _char(
        merit=0,
        formation_realm=realm_with_cost,
        formation_level=9,
        formation_xp=10_000_000,
    )
    apply_breakthrough(char, "formation")
    # This SHOULD be impossible. It currently isn't.
    assert char.merit >= 0, (
        f"apply_breakthrough drove merit negative: {char.merit} (cost was {cost}). "
        "Fix: add a merit-cost gate to can_breakthrough OR refuse to deduct "
        "below zero in consume/apply_breakthrough."
    )


def test_consume_breakthrough_costs_drives_merit_negative():
    """Same root cause via the older ``consume_breakthrough_costs`` entry."""
    realm_with_cost = next(
        (r for r, c in FORMATION_BREAKTHROUGH_MERIT.items() if c > 0),
        None,
    )
    cost = FORMATION_BREAKTHROUGH_MERIT[realm_with_cost]
    char = _char(merit=0, formation_realm=realm_with_cost)
    consume_breakthrough_costs(char, "formation")
    assert char.merit >= 0, (
        f"consume_breakthrough_costs drove merit negative: {char.merit} (cost was {cost})."
    )
