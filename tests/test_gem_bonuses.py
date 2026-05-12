"""Regression tests for ``compute_gem_bonuses`` and ``gem_element``."""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.systems.cultivation import compute_gem_bonuses
from src.game.systems.formation import gem_element


# ── Element gems: only their element's damage bonus ──────────────────────

def test_gem_kim_grants_kim_element_dmg_bonus():
    bonuses = compute_gem_bonuses(["GemKim_3"])
    per_grade = registry.gem_bonus["Kim"]["element_dmg_bonus"]["kim"]
    assert bonuses.get("element_dmg_bonus", {}).get("kim") == pytest.approx(per_grade * 3)


def test_gem_quang_grants_quang_element_dmg_bonus():
    bonuses = compute_gem_bonuses(["GemQuang_4"])
    per_grade = registry.gem_bonus["Quang"]["element_dmg_bonus"]["quang"]
    assert bonuses.get("element_dmg_bonus", {}).get("quang") == pytest.approx(per_grade * 4)


def test_element_gems_dont_grant_other_stats():
    """Old behavior was kim → crit_rating, moc → hp_regen_pct, etc.
    New behavior: pure element_dmg_bonus only — no leakage to other stats."""
    bonuses = compute_gem_bonuses(["GemKim_4", "GemMoc_4"])
    assert "crit_rating" not in bonuses
    assert "hp_regen_pct" not in bonuses
    assert "element_dmg_bonus" in bonuses


# ── Stat gems: each grants its corresponding flat stat ───────────────────

def test_gem_sat_grants_crit_rating():
    bonuses = compute_gem_bonuses(["GemSat_2"])
    per_grade = registry.gem_bonus["Sat"]["crit_rating"]
    assert bonuses.get("crit_rating") == pytest.approx(per_grade * 2)


def test_gem_chuan_grants_accuracy():
    bonuses = compute_gem_bonuses(["GemChuan_3"])
    per_grade = registry.gem_bonus["Chuan"]["accuracy_rating"]
    assert bonuses.get("accuracy_rating") == pytest.approx(per_grade * 3)


def test_gem_sinh_grants_hp_regen():
    bonuses = compute_gem_bonuses(["GemSinh_2"])
    per_grade = registry.gem_bonus["Sinh"]["hp_regen_pct"]
    assert bonuses.get("hp_regen_pct") == pytest.approx(per_grade * 2)


def test_gem_phap_grants_matk_bonus():
    bonuses = compute_gem_bonuses(["GemPhap_4"])
    per_grade = registry.gem_bonus["Phap"]["matk_bonus"]
    assert bonuses.get("matk_bonus") == pytest.approx(per_grade * 4)


def test_gem_cong_grants_atk_bonus():
    bonuses = compute_gem_bonuses(["GemCong_4"])
    per_grade = registry.gem_bonus["Cong"]["atk_bonus"]
    assert bonuses.get("atk_bonus") == pytest.approx(per_grade * 4)


# ── Mixing element + stat gems aggregates correctly ──────────────────────

def test_mixed_element_and_stat_gems_aggregate():
    bonuses = compute_gem_bonuses(["GemKim_2", "GemKim_3", "GemSat_2"])
    kim_per_grade = registry.gem_bonus["Kim"]["element_dmg_bonus"]["kim"]
    sat_per_grade = registry.gem_bonus["Sat"]["crit_rating"]
    # 2 kim gems contribute (2 + 3) × per_grade = 5 × 0.005 = 0.025
    assert bonuses.get("element_dmg_bonus", {}).get("kim") == pytest.approx(kim_per_grade * 5)
    # 1 sat gem at grade 2 → per_grade × 2
    assert bonuses.get("crit_rating") == pytest.approx(sat_per_grade * 2)


# ── Edge / passthrough cases ─────────────────────────────────────────────

def test_unknown_gem_prefix_returns_no_bonus():
    assert compute_gem_bonuses(["GemNotReal_1"]) == {}


def test_gem_element_passthrough_for_canonical_keys():
    assert gem_element("GemKim_2") == "kim"
    assert gem_element("GemPhong_3") == "phong"
    assert gem_element("GemTho_1") == "tho"
    assert gem_element("GemQuang_4") == "quang"
