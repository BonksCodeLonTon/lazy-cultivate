"""Permanent combat-buff pill counters — cap, accrual, and stat application."""
from __future__ import annotations

import pytest

from src.game.models.character import Character, CharacterStats
from src.game.systems.alchemy import consume_pill
from src.game.systems.pill_buffs import (
    PILL_BUFF_CAP,
    PILL_BUFF_STATS,
    encode_counts,
    increment_count,
    is_buff_pill,
    parse_counts,
    total_buff_for_stat,
)


def _char(**kw) -> Character:
    return Character(player_id=1, discord_id=1, name="X", stats=CharacterStats(), **kw)


# ── Encoding round-trip ──────────────────────────────────────────────────

def test_parse_counts_handles_empty_and_malformed():
    assert parse_counts(None) == {}
    assert parse_counts("") == {}
    assert parse_counts("not json") == {}
    assert parse_counts("[1,2,3]") == {}  # not a dict


def test_parse_counts_round_trip():
    counts = {"buff_speed": 5, "buff_def": 12}
    assert parse_counts(encode_counts(counts)) == counts


# ── Cap + increment ─────────────────────────────────────────────────────

def test_increment_count_under_cap():
    ok, new = increment_count({"buff_speed": 3}, "buff_speed")
    assert ok and new == {"buff_speed": 4}


def test_increment_count_at_cap_refuses():
    ok, new = increment_count({"buff_speed": PILL_BUFF_CAP}, "buff_speed")
    assert not ok and new == {"buff_speed": PILL_BUFF_CAP}


def test_increment_count_returns_fresh_dict():
    src = {"buff_def": 0}
    _, new = increment_count(src, "buff_def")
    assert new is not src, "must not mutate the caller's dict"


# ── Buff registry ───────────────────────────────────────────────────────

def test_every_dead_buff_effect_has_an_entry():
    """All buff_* / buff_element_* effect_keys we promised to wire up
    must have a non-empty stat increment in PILL_BUFF_STATS."""
    expected = {
        "buff_speed", "buff_def", "buff_sword_dmg",
        "buff_element_kim", "buff_element_moc", "buff_element_thuy",
        "buff_element_hoa", "buff_element_tho", "buff_element_loi",
        "buff_element_phong", "buff_element_quang", "buff_element_am",
    }
    for ek in expected:
        assert is_buff_pill(ek), f"{ek} should be classified as a buff pill"
        assert PILL_BUFF_STATS[ek], f"{ek} has empty stat dict"


# ── Stat application ────────────────────────────────────────────────────

def test_total_buff_for_stat_scales_linearly_then_caps():
    """Cap clamps the per-key count, but multiple keys still summed."""
    counts = {"buff_speed": 5}
    assert total_buff_for_stat(counts, "spd") == pytest.approx(2.5)
    counts = {"buff_speed": PILL_BUFF_CAP + 100}  # over-cap stored values
    assert total_buff_for_stat(counts, "spd") == pytest.approx(PILL_BUFF_CAP * 0.5)


def test_total_buff_for_unknown_stat_returns_zero():
    assert total_buff_for_stat({"buff_speed": 10}, "atk") == 0.0


def test_compute_combat_stats_applies_pill_buffs():
    """End-to-end: the player's pill counters translate to runtime stats."""
    from src.game.systems.character_stats import compute_combat_stats

    # Baseline (no buff)
    char_clean = _char(qi_realm=2, body_realm=2)
    cs_clean = compute_combat_stats(char_clean, gem_count=0)

    # Same character + 10 buff_def + 8 buff_speed pills consumed.
    char_buffed = _char(
        qi_realm=2, body_realm=2,
        pill_buff_counts={"buff_def": 10, "buff_speed": 8, "buff_element_kim": 5},
    )
    cs_buffed = compute_combat_stats(char_buffed, gem_count=0)

    assert cs_buffed.def_stat - cs_clean.def_stat == 100   # 10 × +10
    assert cs_buffed.spd - cs_clean.spd == 4               # 8 × +0.5 = 4
    kim_clean = cs_clean.element_dmg_bonus.get("kim", 0.0)
    kim_buffed = cs_buffed.element_dmg_bonus.get("kim", 0.0)
    assert kim_buffed - kim_clean == pytest.approx(0.05)   # 5 × +0.01


# ── End-to-end consume_pill flow ────────────────────────────────────────

def test_consume_buff_pill_increments_counter_and_keeps_bonus():
    from src.data.registry import GameRegistry
    reg = GameRegistry.get()
    pill = next((k for k, v in reg.items.items() if v.get("effect_key") == "buff_speed"), None)
    assert pill, "expected at least one buff_speed pill in registry"

    char = _char(body_realm=2, qi_realm=2)
    res = consume_pill(char, pill, quality_tier=1)
    assert res.applied
    assert res.pill_buff_increment == "buff_speed"
    assert char.pill_buff_counts["buff_speed"] == 1


def test_consume_buff_pill_refuses_at_cap():
    from src.data.registry import GameRegistry
    reg = GameRegistry.get()
    pill = next((k for k, v in reg.items.items() if v.get("effect_key") == "buff_speed"), None)

    char = _char(
        body_realm=2, qi_realm=2,
        pill_buff_counts={"buff_speed": PILL_BUFF_CAP},
    )
    res = consume_pill(char, pill, quality_tier=1)
    assert not res.applied, "cap should block further consumption"
    # Counter must NOT have been bumped, even by accident.
    assert char.pill_buff_counts["buff_speed"] == PILL_BUFF_CAP
