"""Kho Tàng shared storage (Phase 3) — slot capacity, storable-item filter,
weekly-window math, and facility unlock gating.

The deposit/approve atomics are DB-coupled (row locks) and exercised in
integration; per project convention unit tests stay DB-free.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.registry import registry
from src.game.systems import sect


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Slot capacity ─────────────────────────────────────────────────────────────

def test_storage_slots_ladder():
    assert sect.storage_slots(0) == 0          # not built → storage locked
    assert sect.storage_slots(-2) == 0
    assert sect.storage_slots(1) == 20         # 15 + 5×1
    assert sect.storage_slots(10) == 65        # 15 + 5×10


def test_kho_tang_unlocks_at_sect_level_2():
    assert sect.facility_min_sect_level("kho_tang") == 2
    # Everything else buildable from L1.
    for key in ("tu_linh_tran", "luyen_dan_phong", "tang_kinh_cac", "tu_bao_cac"):
        assert sect.facility_min_sect_level(key) == 1
    # Unknown facilities default to 1 (no accidental hard-lock).
    assert sect.facility_min_sect_level("unknown") == 1


# ── Storable-item filter (D6) ─────────────────────────────────────────────────

def test_furnaces_and_specials_are_not_storable():
    furnaces = [i for i in registry.items.values() if i.get("type") == "furnace"]
    specials = [i for i in registry.items.values() if i.get("type") == "special"]
    assert furnaces, "expected furnace items in registry"
    for item in furnaces + specials:
        assert not sect.is_storable_item(item)


def test_common_stackables_are_storable():
    # Every non-excluded typed item is depositable — spot-check the types the
    # design calls out (materials, pills, chests, scrolls, tinh huyết).
    seen_types = set()
    for item in registry.items.values():
        t = item.get("type")
        if not t or t in sect.NON_STORABLE_TYPES:
            continue
        if sect.is_storable_item(item):
            seen_types.add(t)
    for expected in ("chest", "scroll"):
        assert expected in seen_types, f"expected storable type {expected}"


def test_is_storable_rejects_missing_defs():
    # Unknown keys resolve to None/{} from the registry — both must fail closed.
    assert not sect.is_storable_item(None)
    assert not sect.is_storable_item({})


# ── Weekly window ─────────────────────────────────────────────────────────────

def test_week_start_utc_is_monday_midnight():
    # 2026-07-08 is a Wednesday → ISO week starts Monday 2026-07-06.
    now = datetime(2026, 7, 8, 15, 30, tzinfo=timezone.utc)
    ws = sect.week_start_utc(now)
    assert (ws.year, ws.month, ws.day) == (2026, 7, 6)
    assert (ws.hour, ws.minute, ws.weekday()) == (0, 0, 0)
    assert ws.tzinfo == timezone.utc


def test_week_start_utc_monday_is_identity_date():
    monday = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)
    assert sect.week_start_utc(monday) == monday
    # Sunday 23:59 still belongs to the week that started 6 days earlier.
    sunday = datetime(2026, 7, 12, 23, 59, tzinfo=timezone.utc)
    assert sect.week_start_utc(sunday).day == 6


# ── Config sanity ─────────────────────────────────────────────────────────────

def test_storage_limits_config():
    assert sect.STORAGE_STACK_CAP == 9_999
    assert sect.MAX_PENDING_STORAGE_REQUESTS == 2
    assert sect.MAX_WEEKLY_STORAGE_WITHDRAWALS == 5
    assert sect.STORAGE_REQUEST_EXPIRE_HOURS == 72
