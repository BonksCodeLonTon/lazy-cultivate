"""Tông Môn (sect) rules — level curve, donation economy, rank matrix, validation.

Pure-logic coverage of ``src/game/systems/sect.py`` (Phase 1). Repository and
cog behavior are DB/Discord-coupled and exercised in integration; per project
convention unit tests stay DB-free.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.data.registry import registry
from src.game.systems import sect


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Level curve data ──────────────────────────────────────────────────────────

def test_level_table_shape():
    rows = registry.sect_levels
    assert len(rows) == sect.MAX_SECT_LEVEL
    assert [r["level"] for r in rows] == list(range(1, sect.MAX_SECT_LEVEL + 1))


def test_level_table_monotonic():
    rows = registry.sect_levels
    exps = [r["exp_to_next"] for r in rows[:-1]]          # last row is 0 (max)
    assert all(a < b for a, b in zip(exps, exps[1:])), "exp curve must strictly grow"
    caps = [r["member_cap"] for r in rows]
    assert caps == sorted(caps)
    dons = [r["donation_cap"] for r in rows]
    assert dons == sorted(dons)


def test_level_row_clamps_out_of_range():
    assert sect.level_row(0)["level"] == 1
    assert sect.level_row(99)["level"] == sect.MAX_SECT_LEVEL


def test_member_and_donation_caps_scale():
    assert sect.member_cap(1) == 10
    assert sect.member_cap(10) == 28
    # Donation cap scales with sect level (user-locked): 5k at L1 → 14k at L10.
    assert sect.donation_cap(1) == 5_000
    assert sect.donation_cap(10) == 14_000


def test_exp_to_next_zero_at_max():
    assert sect.exp_to_next(sect.MAX_SECT_LEVEL) == 0
    assert sect.exp_to_next(1) == 30_000


# ── EXP application ───────────────────────────────────────────────────────────

def test_apply_exp_simple_gain_no_level():
    res = sect.apply_exp(1, 0, 29_999)
    assert (res.level, res.exp, res.levels_gained) == (1, 29_999, 0)


def test_apply_exp_single_level_up_carries_remainder():
    res = sect.apply_exp(1, 25_000, 10_000)          # 35k vs 30k threshold
    assert (res.level, res.exp, res.levels_gained) == (2, 5_000, 1)


def test_apply_exp_multi_level_carry():
    # 30k (L1→2) + 60k (L2→3) + 10k leftover
    res = sect.apply_exp(1, 0, 100_000)
    assert (res.level, res.exp, res.levels_gained) == (3, 10_000, 2)


def test_apply_exp_clamps_at_max_and_pins_exp_to_zero():
    res = sect.apply_exp(9, 0, 99_999_999)
    assert res.level == sect.MAX_SECT_LEVEL
    assert res.exp == 0
    res2 = sect.apply_exp(sect.MAX_SECT_LEVEL, 0, 12_345)
    assert (res2.level, res2.exp, res2.levels_gained) == (sect.MAX_SECT_LEVEL, 0, 0)


def test_apply_exp_ignores_negative_inputs():
    res = sect.apply_exp(1, -50, -100)
    assert (res.level, res.exp, res.levels_gained) == (1, 0, 0)


# ── Donation economy ──────────────────────────────────────────────────────────

def test_split_donation_rates():
    s = sect.split_donation(1_000)
    assert (s.funds, s.exp, s.contribution) == (1_000, 100, 100)


def test_split_donation_floors_dust():
    s = sect.split_donation(999)
    assert (s.funds, s.exp, s.contribution) == (999, 99, 99)
    assert sect.split_donation(0) == sect.split_donation(-5)


def test_remaining_donation_same_day_accumulates():
    today = date(2026, 7, 8)
    assert sect.remaining_donation_today(0, None, today, sect_level=1) == 5_000
    assert sect.remaining_donation_today(3_000, today, today, sect_level=1) == 2_000
    assert sect.remaining_donation_today(5_000, today, today, sect_level=1) == 0
    # Over-cap stored value never goes negative.
    assert sect.remaining_donation_today(9_999, today, today, sect_level=1) == 0


def test_remaining_donation_resets_on_new_day():
    today = date(2026, 7, 8)
    yesterday = date(2026, 7, 7)
    assert sect.remaining_donation_today(5_000, yesterday, today, sect_level=1) == 5_000
    assert sect.donated_so_far_today(5_000, yesterday, today) == 0


def test_remaining_donation_scales_with_level():
    today = date(2026, 7, 8)
    assert sect.remaining_donation_today(0, None, today, sect_level=5) == 9_000
    assert sect.remaining_donation_today(0, None, today, sect_level=10) == 14_000


# ── Creation gate ─────────────────────────────────────────────────────────────

def test_realm_gate_any_axis():
    assert sect.meets_realm_gate(3, 0, 0)
    assert sect.meets_realm_gate(0, 3, 0)
    assert sect.meets_realm_gate(0, 0, 3)
    assert not sect.meets_realm_gate(2, 2, 2)


# ── Name / tag validation ─────────────────────────────────────────────────────

def test_validate_name_ok_vietnamese():
    name, err = sect.validate_sect_name("Thanh Vân Môn")
    assert err is None and name == "Thanh Vân Môn"


def test_validate_name_normalizes_whitespace():
    name, err = sect.validate_sect_name("  Thanh   Vân   Môn  ")
    assert err is None and name == "Thanh Vân Môn"


def test_validate_name_rejects_length():
    assert sect.validate_sect_name("ab")[1] is not None
    assert sect.validate_sect_name("x" * 33)[1] is not None


def test_validate_name_rejects_symbols():
    assert sect.validate_sect_name("Thanh@Vân")[1] is not None
    assert sect.validate_sect_name("Thanh_Vân")[1] is not None


def test_validate_tag_uppercases():
    tag, err = sect.validate_sect_tag("tvm")
    assert err is None and tag == "TVM"


def test_validate_tag_rejects_bad_input():
    assert sect.validate_sect_tag("a")[1] is not None            # too short
    assert sect.validate_sect_tag("ABCDEFG")[1] is not None      # too long
    assert sect.validate_sect_tag("Vân")[1] is not None          # non-ASCII
    assert sect.validate_sect_tag("A-B")[1] is not None          # symbol


# ── Rank matrix ───────────────────────────────────────────────────────────────

def test_rank_power_ordering():
    assert (
        sect.rank_power(sect.RANK_TONG_CHU)
        > sect.rank_power(sect.RANK_TRUONG_LAO)
        > sect.rank_power(sect.RANK_CHAP_SU)
        > sect.rank_power(sect.RANK_DE_TU)
    )
    assert sect.rank_power("unknown_rank") == 0


def test_review_and_announcement_permissions():
    assert sect.can_review_applications(sect.RANK_CHAP_SU)
    assert sect.can_review_applications(sect.RANK_TONG_CHU)
    assert not sect.can_review_applications(sect.RANK_DE_TU)

    assert sect.can_set_announcement(sect.RANK_TRUONG_LAO)
    assert not sect.can_set_announcement(sect.RANK_CHAP_SU)


def test_kick_matrix():
    # Officers kick strictly below; nobody kicks the Tông Chủ; Đệ Tử kicks nobody.
    assert sect.can_kick(sect.RANK_CHAP_SU, sect.RANK_DE_TU)
    assert sect.can_kick(sect.RANK_TRUONG_LAO, sect.RANK_CHAP_SU)
    assert sect.can_kick(sect.RANK_TONG_CHU, sect.RANK_TRUONG_LAO)
    assert not sect.can_kick(sect.RANK_CHAP_SU, sect.RANK_CHAP_SU)
    assert not sect.can_kick(sect.RANK_TRUONG_LAO, sect.RANK_TONG_CHU)
    assert not sect.can_kick(sect.RANK_DE_TU, sect.RANK_DE_TU)


def test_set_rank_matrix():
    TC, TL, CS, DT = (
        sect.RANK_TONG_CHU, sect.RANK_TRUONG_LAO, sect.RANK_CHAP_SU, sect.RANK_DE_TU,
    )
    # Trưởng Lão may move Đệ Tử ↔ Chấp Sự…
    assert sect.can_set_rank(TL, DT, CS)
    assert sect.can_set_rank(TL, CS, DT)
    # …but anything touching the Trưởng Lão rank needs Tông Chủ.
    assert not sect.can_set_rank(TL, CS, TL)
    assert not sect.can_set_rank(TL, TL, CS)
    assert sect.can_set_rank(TC, CS, TL)
    assert sect.can_set_rank(TC, TL, CS)
    # The Tông Chủ seat is never assignable here; no-op changes rejected.
    assert not sect.can_set_rank(TC, TC, TL)
    assert not sect.can_set_rank(TC, DT, TC)
    assert not sect.can_set_rank(TC, DT, DT)
    # Chấp Sự has no rank authority at all.
    assert not sect.can_set_rank(CS, DT, CS)


def test_officer_caps():
    assert sect.officer_cap(sect.RANK_CHAP_SU, 1) == sect.CHAP_SU_CAP
    assert sect.officer_cap(sect.RANK_DE_TU, 1) is None
    # Trưởng Lão seats grow with level: 2 + level//3.
    assert sect.truong_lao_cap(1) == 2
    assert sect.truong_lao_cap(3) == 3
    assert sect.truong_lao_cap(9) == 5
    assert sect.officer_cap(sect.RANK_TRUONG_LAO, 9) == 5
