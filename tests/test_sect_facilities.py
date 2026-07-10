"""Tông Môn facilities (Phase 2) — data integrity, upgrade costs, buff
aggregation, and the cultivation/alchemy hook math.

The golden combat-stats suite doubles as the "no combat leakage" guard:
``Character.sect_buffs`` defaults empty and ``compute_combat_stats`` never
reads it, so those snapshots must stay byte-identical.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.models.character import Character
from src.game.systems import sect
from src.game.systems.alchemy import apply_sect_quality_bonus
from src.game.systems.cultivation import advance_cultivation_xp, cultivation_speed_mult


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


def _fresh_char(**overrides) -> Character:
    defaults = dict(player_id=1, discord_id=1, name="Test", active_axis="qi")
    defaults.update(overrides)
    return Character(**defaults)


# ── facilities.json integrity ─────────────────────────────────────────────────

def test_facility_defs_shape():
    defs = sect.all_facilities()
    assert len(defs) == 5
    assert [f["key"] for f in defs] == [
        "tu_linh_tran", "luyen_dan_phong", "tang_kinh_cac", "tu_bao_cac", "kho_tang",
    ]
    for f in defs:
        assert int(f["max_level"]) == 10
        costs = f["upgrade_costs"]
        assert len(costs) == 10
        assert all(a < b for a, b in zip(costs, costs[1:])), f"{f['key']} costs must grow"


def test_buff_facilities_carry_expected_keys():
    assert sect.facility_def("tu_linh_tran")["buff_key"] == "cultivation_speed_bonus"
    # User-locked: +3% cultivation speed per level.
    assert sect.facility_def("tu_linh_tran")["buff_per_level"] == pytest.approx(0.03)
    assert sect.facility_def("luyen_dan_phong")["buff_key"] == "alchemy_quality_bonus"
    # Structural facilities have no character-level buff.
    for key in ("tang_kinh_cac", "tu_bao_cac", "kho_tang"):
        assert not sect.facility_def(key)["buff_key"]


# ── Upgrade costs ─────────────────────────────────────────────────────────────

def test_upgrade_cost_ladder():
    assert sect.facility_upgrade_cost("tu_linh_tran", 0) == 20_000
    assert sect.facility_upgrade_cost("tu_linh_tran", 9) == 1_100_000
    assert sect.facility_upgrade_cost("tu_linh_tran", 10) is None      # maxed
    assert sect.facility_upgrade_cost("tu_linh_tran", -1) is None
    assert sect.facility_upgrade_cost("unknown_facility", 0) is None


# ── Buff aggregation ──────────────────────────────────────────────────────────

def test_facility_bonuses_single():
    assert sect.facility_bonuses({"tu_linh_tran": 3}) == {
        "cultivation_speed_bonus": pytest.approx(0.09)
    }


def test_facility_bonuses_combined_and_filtered():
    out = sect.facility_bonuses({
        "tu_linh_tran": 10,
        "luyen_dan_phong": 4,
        "kho_tang": 7,             # structural — no buff
        "unknown": 3,              # unknown — ignored
        "tang_kinh_cac": 0,        # level 0 — ignored
    })
    assert out == {
        "cultivation_speed_bonus": pytest.approx(0.30),
        "alchemy_quality_bonus": pytest.approx(0.02),
    }


def test_facility_bonuses_clamps_levels():
    # Above max clamps to L10; negative contributes nothing.
    assert sect.facility_bonuses({"tu_linh_tran": 99}) == {
        "cultivation_speed_bonus": pytest.approx(0.30)
    }
    assert sect.facility_bonuses({"tu_linh_tran": -3}) == {}
    assert sect.facility_bonuses({}) == {}


def test_facility_buff_line_formatting():
    assert "9%" in sect.facility_buff_line("tu_linh_tran", 3)
    assert sect.facility_buff_line("kho_tang", 5) is None


# ── Cultivation hook ──────────────────────────────────────────────────────────

def test_cultivation_speed_mult_adds_sect_term():
    baseline = cultivation_speed_mult(_fresh_char())
    boosted = _fresh_char(sect_buffs={"cultivation_speed_bonus": 0.09})
    assert cultivation_speed_mult(boosted) == pytest.approx(baseline + 0.09)


def test_advance_cultivation_xp_respects_sect_buff():
    turns = 1_000
    plain = _fresh_char()
    buffed = _fresh_char(sect_buffs={"cultivation_speed_bonus": 0.30})

    gained_plain = advance_cultivation_xp(plain, turns)["exp_gained"]
    gained_buffed = advance_cultivation_xp(buffed, turns)["exp_gained"]

    mult_plain = cultivation_speed_mult(_fresh_char())
    expected_ratio = (mult_plain + 0.30) / mult_plain
    assert gained_buffed == pytest.approx(gained_plain * expected_ratio, rel=0.01)
    assert gained_buffed > gained_plain


# ── Alchemy hook ──────────────────────────────────────────────────────────────

def test_apply_sect_quality_bonus_shifts_hoan_to_huyen():
    chances = {"hoan": 0.70, "huyen": 0.20, "dia": 0.08, "thien": 0.02}
    out = apply_sect_quality_bonus(chances, 0.05)
    assert out["hoan"] == pytest.approx(0.65)
    assert out["huyen"] == pytest.approx(0.25)
    assert out["dia"] == pytest.approx(0.08)
    assert out["thien"] == pytest.approx(0.02)
    # Input map untouched (immutability).
    assert chances["hoan"] == pytest.approx(0.70)


def test_apply_sect_quality_bonus_clamps_and_noops():
    out = apply_sect_quality_bonus({"hoan": 0.02, "huyen": 0.98}, 0.05)
    assert out["hoan"] == 0.0
    assert out["huyen"] == pytest.approx(1.03)

    chances = {"hoan": 0.5, "huyen": 0.5}
    assert apply_sect_quality_bonus(chances, 0.0) == chances
    assert apply_sect_quality_bonus(chances, -0.1) == chances
