"""Regression tests for ``compute_gem_bonuses`` and ``gem_element``.

The legacy ``GemTo_*`` (earth) and ``GemDuong_*`` (yang/light) gem keys live
in ``gems.json``, loot tables, admin presets and end-game probes — they used
to silently grant zero stats because the prefix-to-element table only listed
the canonical element spellings (``Tho`` / ``Quang``). These tests pin down
the alias fix so the regressions don't sneak back in.
"""
from __future__ import annotations

import pytest

from src.game.constants.balance import GEM_ELEMENT_BASE_BONUS
from src.game.systems.cultivation import compute_gem_bonuses
from src.game.systems.formation import gem_element


def test_gem_to_alias_grants_earth_stats():
    # GemTo_2 must alias to the "tho" (earth) element — base bonus × grade.
    bonuses = compute_gem_bonuses(["GemTo_2"])
    expected_def = GEM_ELEMENT_BASE_BONUS["tho"]["def_bonus"] * 2
    assert bonuses.get("def_bonus") == pytest.approx(expected_def)


def test_gem_duong_alias_grants_quang_stats():
    # GemDuong_3 should provide the same per-gem heal bonus as GemQuang_3.
    bonuses = compute_gem_bonuses(["GemDuong_3"])
    expected_heal = GEM_ELEMENT_BASE_BONUS["quang"]["heal_pct"] * 3
    assert bonuses.get("heal_pct") == pytest.approx(expected_heal)


def test_unknown_gem_prefix_returns_no_bonus():
    # Sanity check: a fabricated key still no-ops instead of crashing.
    assert compute_gem_bonuses(["GemNotReal_1"]) == {}


def test_gem_element_aliases_to_canonical():
    assert gem_element("GemTo_1") == "tho"
    assert gem_element("GemDuong_4") == "quang"


def test_gem_element_passthrough_for_canonical_keys():
    assert gem_element("GemKim_2") == "kim"
    assert gem_element("GemPhong_3") == "phong"
