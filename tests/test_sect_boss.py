"""Trấn Sơn Thú sect boss (Phase 5) — boss selection, HP formula, reward
tiers, and combatant construction from the new enemy family.

The damage/claim atomics are DB-coupled (row locks, structural clones of the
world-boss repo already covered there) and exercised in integration.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.registry import registry
from src.game.systems import sect, sect_boss


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Data integrity — the new enemy family ─────────────────────────────────────

def test_boss_family_shape():
    defs = sect_boss.boss_defs()
    assert len(defs) == 3
    tiers = sorted(int(b["min_sect_level"]) for b in defs.values())
    assert tiers == [3, 6, 9]
    hps = [b["base_hp"] for b in sorted(defs.values(), key=lambda b: b["min_sect_level"])]
    assert hps == sorted(hps), "higher tiers must have bigger HP pools"


def test_boss_skill_pools_resolve():
    for b in sect_boss.boss_defs().values():
        assert b["skill_pool"], f"{b['key']} has no skills"
        for sk in b["skill_pool"]:
            assert sk in registry.skills, f"{b['key']} references unknown skill {sk}"


def test_boss_rewards_resolve():
    for b in sect_boss.boss_defs().values():
        reward = sect_boss.reward_block(b)
        for chest_field in ("participation_chest", "top3_chest"):
            key = reward.get(chest_field)
            assert key in registry.items, f"{b['key']} reward chest {key} unknown"
        assert int(reward.get("participation_ch", 0)) > 0
        assert int(reward.get("sect_exp", 0)) > 0
        assert int(reward.get("sect_funds", 0)) > 0


# ── Boss selection ────────────────────────────────────────────────────────────

def test_select_boss_by_sect_level():
    assert sect_boss.select_boss(1) is None
    assert sect_boss.select_boss(2) is None
    assert sect_boss.select_boss(3)["min_sect_level"] == 3
    assert sect_boss.select_boss(5)["min_sect_level"] == 3
    assert sect_boss.select_boss(6)["min_sect_level"] == 6
    assert sect_boss.select_boss(8)["min_sect_level"] == 6
    assert sect_boss.select_boss(9)["min_sect_level"] == 9
    assert sect_boss.select_boss(10)["min_sect_level"] == 9


# ── HP formula ────────────────────────────────────────────────────────────────

def _tier1() -> dict:
    return sect_boss.select_boss(3)


def test_hp_formula_full_roster_at_tier_floor():
    b = _tier1()
    hp = sect_boss.compute_hp_max(b, sect_level=3, member_count=14, member_cap=14)
    assert hp == int(b["base_hp"])          # level mult 1.0 × roster mult 1.0


def test_hp_formula_roster_floor_is_60pct():
    b = _tier1()
    hp = sect_boss.compute_hp_max(b, sect_level=3, member_count=0, member_cap=14)
    assert hp == int(b["base_hp"] * 0.6)


def test_hp_formula_grows_with_sect_level():
    b = _tier1()
    hp5 = sect_boss.compute_hp_max(b, sect_level=5, member_count=14, member_cap=14)
    assert hp5 == int(b["base_hp"] * (1 + 0.15 * 2))
    # Roster overshoot clamps at 1.0 (never inflates past the cap factor).
    hp_over = sect_boss.compute_hp_max(b, sect_level=3, member_count=99, member_cap=14)
    assert hp_over == int(b["base_hp"])


# ── Week rollover ─────────────────────────────────────────────────────────────

def test_next_week_start_is_following_monday():
    now = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)   # Wednesday
    expiry = sect_boss.next_week_start(now)
    assert expiry == datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    assert expiry.weekday() == 0
    # The expiry lands exactly on the NEXT week's key boundary.
    assert sect.week_key(expiry) != sect.week_key(now)


# ── Reward tiers ──────────────────────────────────────────────────────────────

def test_reward_tiers():
    hp_max = 1_000_000
    threshold = sect_boss.participation_threshold(hp_max)
    assert threshold == 5_000                                   # 0.5%
    assert sect_boss.reward_tier(1, 100_000, hp_max) == "top"
    assert sect_boss.reward_tier(3, threshold, hp_max) == "top"
    assert sect_boss.reward_tier(4, 100_000, hp_max) == "participant"
    assert sect_boss.reward_tier(1, threshold - 1, hp_max) == "none"


# ── Combatant construction ────────────────────────────────────────────────────

def test_build_boss_combatant_honors_instance_hp():
    b = _tier1()
    boss_c = sect_boss.build_boss_combatant(
        b, hp_current=123_456, hp_max=2_500_000, player_realm_total=30,
    )
    # The instance's member-scaled pool wins over the tier's raw base_hp.
    assert boss_c.hp_max == 2_500_000
    assert boss_c.hp == 123_456
    assert boss_c.name == b["vi"]
    assert boss_c.immune_hard_cc is True


def test_build_boss_combatant_scales_with_attacker_realm():
    b = _tier1()
    weak = sect_boss.build_boss_combatant(b, 1_000_000, 1_000_000, player_realm_total=10)
    strong = sect_boss.build_boss_combatant(b, 1_000_000, 1_000_000, player_realm_total=70)
    assert strong.atk > weak.atk
    assert strong.def_stat > weak.def_stat
