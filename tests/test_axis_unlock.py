"""Season-2 axis lock — unlocked_axes parsing, unlock semantics, and the
Đạo Nguyên Thạch world drop wiring.

The registration flow / cog UI are Discord-coupled; these tests pin the pure
rules plus the data/loot integration every flow consumes.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.models.character import Character
from src.game.systems import sect
from src.game.systems.cultivation import (
    ALL_AXES,
    AXIS_UNLOCK_ITEM_KEY,
    DEFAULT_AXIS,
    format_unlocked_axes,
    is_axis_unlocked,
    parse_unlocked_axes,
    unlock_axis,
)


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Parsing ───────────────────────────────────────────────────────────────────

def test_parse_valid_sets():
    assert parse_unlocked_axes("qi") == ["qi"]
    assert parse_unlocked_axes("body,qi,formation") == ["body", "qi", "formation"]
    # Canonical order re-imposed regardless of input order.
    assert parse_unlocked_axes("formation,body") == ["body", "formation"]


def test_parse_degrades_safely():
    # Garbage / legacy rows can never strand a player with zero axes.
    assert parse_unlocked_axes(None) == [DEFAULT_AXIS]
    assert parse_unlocked_axes("") == [DEFAULT_AXIS]
    assert parse_unlocked_axes("nonsense,alsobad") == [DEFAULT_AXIS]
    assert parse_unlocked_axes(" qi , body ") == ["body", "qi"]
    assert parse_unlocked_axes("qi,qi,qi") == ["qi"]          # dedupe


def test_format_round_trip():
    for raw in ("qi", "body,qi", "body,qi,formation"):
        assert format_unlocked_axes(parse_unlocked_axes(raw)) == raw
    assert format_unlocked_axes([]) == DEFAULT_AXIS


# ── Unlock semantics ──────────────────────────────────────────────────────────

def test_unlock_axis_adds_and_is_idempotent():
    assert unlock_axis("qi", "body") == "body,qi"
    assert unlock_axis("body,qi", "body") == "body,qi"        # idempotent
    assert unlock_axis("body,qi", "formation") == "body,qi,formation"
    assert unlock_axis("qi", "not_an_axis") == "qi"           # invalid ignored


def test_is_axis_unlocked():
    unlocked = parse_unlocked_axes("body,formation")
    assert is_axis_unlocked(unlocked, "body")
    assert not is_axis_unlocked(unlocked, "qi")


# ── Character model compatibility ─────────────────────────────────────────────

def test_character_default_unlocks_everything():
    # Direct constructions (tests, benches, sims) must behave like pre-lock
    # characters — only DB-hydrated players carry a restricted set.
    char = Character(player_id=1, discord_id=1, name="T")
    assert list(char.unlocked_axes) == list(ALL_AXES)


# ── Đạo Nguyên Thạch item + world drop ────────────────────────────────────────

def test_unlock_stone_item_exists():
    item = registry.get_item(AXIS_UNLOCK_ITEM_KEY)
    assert item is not None
    assert item["type"] == "special"
    # Account-scoped specials never enter the sect shared storage.
    assert not sect.is_storable_item(item)


def test_unlock_stone_is_a_global_world_drop():
    entry = next(
        (e for e in registry.global_drops if e["item_key"] == AXIS_UNLOCK_ITEM_KEY),
        None,
    )
    assert entry is not None
    thien_menh = next(
        e for e in registry.global_drops if e["item_key"] == "MatThienMenhThach"
    )
    # "Extremely ultra rare": strictly rarer than the constitution stone.
    assert 0 < entry["weight"] < thien_menh["weight"]


def test_unlock_stone_injected_into_loot_tables():
    # Every non-empty loot table gains the world-drop lane via
    # registry.get_loot_table — spot-check a real zone table.
    key = next(
        (k for k, entries in registry.loot_tables.items() if entries), None
    )
    assert key is not None, "no loot tables loaded"
    table = registry.get_loot_table(key)
    assert any(e.get("item_key") == AXIS_UNLOCK_ITEM_KEY for e in table)
