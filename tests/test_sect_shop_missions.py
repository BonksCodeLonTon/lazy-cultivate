"""Tông Môn Phase 4 — sect shop catalog math and daily-mission state rules.

Purchase / check-in / claim atomics are DB-coupled (row locks) and exercised
in integration; these tests cover the pure shelf composition, the rotation
determinism guarantee, and the mission progress JSON lifecycle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data.registry import registry
from src.game.systems import sect, sect_missions, sect_shop


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Catalog data integrity ────────────────────────────────────────────────────

def test_shop_catalog_items_exist_and_are_priced():
    data = registry.sect_shop
    assert data["fixed"] and data["rotating"]
    for entry in data["fixed"] + data["rotating"]:
        assert entry["item_key"] in registry.items, f"unknown item {entry['item_key']}"
        assert int(entry["price_ch"]) > 0
        assert int(entry.get("weekly_limit", 0)) >= 0


def test_mission_defs_integrity():
    defs = sect_missions.mission_defs()
    assert len(defs) == 4
    keys = {d["key"] for d in defs}
    assert keys == {
        sect_missions.EVENT_DUNGEON_CLEAR,
        sect_missions.EVENT_WORLD_BOSS_ATTACK,
        sect_missions.EVENT_ALCHEMY_CRAFT,
        sect_missions.EVENT_ARENA_DUEL,
    }
    for d in defs:
        assert int(d["target"]) >= 1
        assert int(d["reward_ch"]) > 0 and int(d["reward_exp"]) > 0


# ── Rotating shelf ────────────────────────────────────────────────────────────

def test_rotating_slot_count_ladder():
    assert sect_shop.rotating_slot_count(0) == 0
    assert sect_shop.rotating_slot_count(1) == 0
    assert sect_shop.rotating_slot_count(2) == 1
    assert sect_shop.rotating_slot_count(10) == 5


def test_rotation_is_deterministic_per_sect_and_week():
    a1 = sect_shop.rotating_slots(7, "2026-W28", 10)
    a2 = sect_shop.rotating_slots(7, "2026-W28", 10)
    assert [s.item_key for s in a1] == [s.item_key for s in a2]
    assert len(a1) == 5
    # Seed is crc32-based — stable across processes, unlike hash().
    assert sect_shop.rotation_seed(7, "2026-W28") == sect_shop.rotation_seed(7, "2026-W28")
    assert sect_shop.rotation_seed(7, "2026-W28") != sect_shop.rotation_seed(8, "2026-W28")


def test_rotation_premium_pool_gated_by_tu_bao_level():
    # Below Tụ Bảo Các L5 the premium entries (min_tu_bao_level=5) never appear.
    for week in ("2026-W01", "2026-W17", "2026-W28", "2026-W40", "2026-W52"):
        for sect_id in range(1, 8):
            slots = sect_shop.rotating_slots(sect_id, week, 4)
            assert len(slots) == 2
            for s in slots:
                assert s.item_key not in ("ChestDia", "ChestLuyenDan")


# ── Scroll shelf ──────────────────────────────────────────────────────────────

def test_scroll_gates_and_discounts():
    assert sect_shop.scroll_grade_cap(0) == 0
    assert sect_shop.scroll_grade_cap(1) == 1
    assert sect_shop.scroll_grade_cap(3) == 2
    assert sect_shop.scroll_discount(5) == 0.0
    assert sect_shop.scroll_discount(6) == pytest.approx(0.10)
    assert sect_shop.scroll_discount(10) == pytest.approx(0.20)


def test_scroll_price_conversion():
    # 1,000 merit → 100 CH; L6 → 90; L10 → 80; floor at 1 CH.
    assert sect_shop.scroll_price_ch(1_000, 1) == 100
    assert sect_shop.scroll_price_ch(1_000, 6) == 90
    assert sect_shop.scroll_price_ch(1_000, 10) == 80
    assert sect_shop.scroll_price_ch(3, 10) == 1


def test_scroll_slots_respect_grade_cap():
    l1 = sect_shop.scroll_slots(1)
    assert l1, "expected grade-1 scrolls in the catalog"
    assert all(s.grade == 1 for s in l1)
    l3 = sect_shop.scroll_slots(3)
    assert any(s.grade == 2 for s in l3)
    assert sect_shop.scroll_slots(0) == []


def test_full_catalog_and_find_slot():
    levels = {"tu_bao_cac": 10, "tang_kinh_cac": 3}
    catalog = sect_shop.full_catalog(1, "2026-W28", levels)
    sections = {s.section for s in catalog}
    assert sections == {"fixed", "rotating", "scroll"}
    slot = sect_shop.find_slot(catalog, "ChestHoang", 1)
    assert slot is not None and slot.price_ch == 400
    assert sect_shop.find_slot(catalog, "ChestHoang", 3) is None
    assert sect_shop.find_slot(catalog, "NotAnItem", 1) is None


# ── Week key ──────────────────────────────────────────────────────────────────

def test_week_key_format_and_stability():
    now = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)
    wk = sect.week_key(now)
    assert len(wk) == 8 and wk[4] == "-" and wk[5] == "W"
    # Same ISO week ⇒ same key; the Monday boundary rolls it.
    assert sect.week_key(sect.week_start_utc(now)) == wk
    next_monday = sect.week_start_utc(now).replace(day=13)
    assert sect.week_key(next_monday) != wk


# ── Mission progress lifecycle ────────────────────────────────────────────────

def _today():
    return datetime(2026, 7, 8, tzinfo=timezone.utc).date()


def test_parse_progress_fresh_and_garbage():
    fresh = sect_missions.parse_progress(None, _today())
    assert fresh == {"date": "2026-07-08", "counts": {}, "claimed": []}
    assert sect_missions.parse_progress("not json{", _today())["counts"] == {}


def test_parse_progress_resets_on_new_day():
    stale = '{"date": "2026-07-07", "counts": {"dungeon_clear": 2}, "claimed": ["arena_duel"]}'
    state = sect_missions.parse_progress(stale, _today())
    assert state["counts"] == {} and state["claimed"] == []
    same_day = sect_missions.parse_progress(
        '{"date": "2026-07-08", "counts": {"dungeon_clear": 1}, "claimed": []}', _today()
    )
    assert same_day["counts"] == {"dungeon_clear": 1}


def test_increment_and_progress_clamp():
    state = sect_missions.parse_progress(None, _today())
    state = sect_missions.increment(state, sect_missions.EVENT_ALCHEMY_CRAFT, 2)
    state = sect_missions.increment(state, sect_missions.EVENT_ALCHEMY_CRAFT, 5)
    mdef = sect_missions.mission_def(sect_missions.EVENT_ALCHEMY_CRAFT)
    cur, target = sect_missions.progress_of(state, mdef)
    assert (cur, target) == (3, 3)          # display clamps at target
    assert state["counts"][sect_missions.EVENT_ALCHEMY_CRAFT] == 7


def test_claimable_keys_and_roundtrip():
    state = sect_missions.parse_progress(None, _today())
    assert sect_missions.claimable_keys(state) == []
    state = sect_missions.increment(state, sect_missions.EVENT_ARENA_DUEL, 1)
    assert sect_missions.claimable_keys(state) == [sect_missions.EVENT_ARENA_DUEL]
    state["claimed"].append(sect_missions.EVENT_ARENA_DUEL)
    assert sect_missions.claimable_keys(state) == []
    # encode → parse roundtrip preserves everything same-day.
    rt = sect_missions.parse_progress(sect_missions.encode_progress(state), _today())
    assert rt == state


@pytest.mark.asyncio
async def test_record_event_is_failure_proof():
    # A broken session must be swallowed — mission bookkeeping can never fail
    # the reward flow that hosts it.
    exploding = MagicMock()
    exploding.execute = AsyncMock(side_effect=RuntimeError("db down"))
    await sect_missions.record_event(exploding, 1, sect_missions.EVENT_ARENA_DUEL)

    # Unknown event keys return before touching the session at all.
    untouched = MagicMock()
    untouched.execute = AsyncMock()
    await sect_missions.record_event(untouched, 1, "not_a_mission")
    untouched.execute.assert_not_called()
