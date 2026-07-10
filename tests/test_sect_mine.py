"""Đại Chiến Khoáng Mạch (Phase 6) — mine map data, payout accrual math,
war constants, and garrison combatant construction.

Declaration/attempt/resolution atomics are DB-coupled (multi-row locks) and
exercised in integration; these tests pin the pure rules the atomics consume.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from src.data.registry import registry
from src.game.systems import sect_mine


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── mines.json integrity ──────────────────────────────────────────────────────

def test_mine_map_shape():
    mines = sect_mine.all_mines()
    assert len(mines) == 9
    tiers = Counter(m["tier"] for m in mines)
    assert tiers == {"thap": 4, "trung": 3, "cao": 1, "cuc_pham": 1}


def test_mine_rates_match_design():
    by_tier = {}
    for m in sect_mine.all_mines():
        by_tier.setdefault(m["tier"], set()).add(int(m["funds_per_hour"]))
    assert by_tier == {
        "thap": {250}, "trung": {600}, "cao": {1250}, "cuc_pham": {2500},
    }


def test_garrison_blocks_resolve():
    for m in sect_mine.all_mines():
        g = sect_mine.garrison_block(m)
        assert g.get("vi"), f"{m['key']} garrison unnamed"
        assert sect_mine.garrison_hp_max(m) > 0
        for sk in g.get("skill_pool", []):
            assert sk in registry.skills, f"{m['key']} garrison references unknown skill {sk}"
        assert g.get("skill_pool"), f"{m['key']} garrison has no skills"


def test_garrison_hp_scales_with_tier():
    hp_by_tier = {}
    for m in sect_mine.all_mines():
        hp_by_tier.setdefault(m["tier"], set()).add(sect_mine.garrison_hp_max(m))
    assert max(hp_by_tier["thap"]) < min(hp_by_tier["trung"])
    assert max(hp_by_tier["trung"]) < min(hp_by_tier["cao"])
    assert max(hp_by_tier["cao"]) < min(hp_by_tier["cuc_pham"])


def test_tier_labels_cover_all_tiers():
    for m in sect_mine.all_mines():
        assert m["tier"] in sect_mine.TIER_LABELS
        assert m["tier"] in sect_mine.TIER_ICONS


# ── Payout accrual math ───────────────────────────────────────────────────────

def _t(h: int, m: int = 0) -> datetime:
    return datetime(2026, 7, 9, h, m, tzinfo=timezone.utc)


def test_payout_hours_whole_hours_only():
    hours, marker = sect_mine.payout_hours(_t(8, 0), _t(10, 59))
    assert hours == 2
    # Marker advances by whole hours — the 59-minute remainder keeps accruing.
    assert marker == _t(10, 0)


def test_payout_hours_sub_hour_is_zero():
    hours, marker = sect_mine.payout_hours(_t(8, 0), _t(8, 59))
    assert hours == 0 and marker == _t(8, 0)


def test_payout_hours_handles_none_and_backwards_clock():
    assert sect_mine.payout_hours(None, _t(8))[0] == 0
    assert sect_mine.payout_hours(_t(9), _t(8))[0] == 0


def test_payout_remainder_never_lost():
    # 8:00 → 10:59 pays 2h, then 10:59… wait until 11:00: one more hour lands.
    _, marker = sect_mine.payout_hours(_t(8, 0), _t(10, 59))
    hours2, marker2 = sect_mine.payout_hours(marker, _t(11, 0))
    assert hours2 == 1 and marker2 == _t(11, 0)


def test_funds_per_hour_lookup():
    assert sect_mine.funds_per_hour("mo_huyen_am") == 2500
    assert sect_mine.funds_per_hour("unknown") == 0


# ── War constants & points ────────────────────────────────────────────────────

def test_war_knobs_match_design():
    assert sect_mine.MINE_WAR_MIN_SECT_LEVEL == 4
    assert sect_mine.DECLARE_FEE_FUNDS == 30_000
    assert sect_mine.WAR_WINDOW_HOURS == 24
    assert sect_mine.ATTEMPTS_PER_WAR == 5
    assert sect_mine.DEFENSE_SHIELD_HOURS == 48
    assert sect_mine.REDECLARE_COOLDOWN_HOURS == 72
    assert sect_mine.OCCUPANCY_CAP == 1


def test_duel_points():
    assert sect_mine.duel_points(True) == 10
    assert sect_mine.duel_points(False) == 3


# ── Garrison combatant ────────────────────────────────────────────────────────

def test_build_garrison_combatant_honors_pool_hp():
    mdef = sect_mine.get_mine("mo_thanh_son")
    c = sect_mine.build_garrison_combatant(mdef, hp_current=123_456, player_realm_total=30)
    assert c.hp == 123_456
    assert c.hp_max == sect_mine.garrison_hp_max(mdef)
    assert c.name == "Mộc Linh Thủ Vệ"
    assert c.immune_hard_cc is True


def test_garrison_scales_with_attacker_realm():
    mdef = sect_mine.get_mine("mo_huyen_am")
    weak = sect_mine.build_garrison_combatant(mdef, 1_000, 10)
    strong = sect_mine.build_garrison_combatant(mdef, 1_000, 70)
    assert strong.atk > weak.atk
