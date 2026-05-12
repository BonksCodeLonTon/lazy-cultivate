"""Tests for the Phân Giải (recycle) system."""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.systems.recycle import (
    get_recycle_material_grade,
    recycle_equipment,
)


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Mapping ─────────────────────────────────────────────────────────────────


def test_mapping_covers_all_equipment_grades():
    for g in range(1, 10):
        assert get_recycle_material_grade(g) is not None


def test_mapping_returns_none_for_invalid_grades():
    assert get_recycle_material_grade(0) is None
    assert get_recycle_material_grade(10) is None
    assert get_recycle_material_grade(-1) is None


def test_mapping_is_monotonic_non_decreasing():
    """Higher equipment grades should yield equal-or-higher material grades —
    guarantees recycling rewards scale with the source item's tier."""
    prev = 0
    for g in range(1, 10):
        mat_g = get_recycle_material_grade(g)
        assert mat_g >= prev, f"Grade {g} maps to mat_grade {mat_g} < prev {prev}"
        prev = mat_g


def test_mapping_never_exceeds_rarity_cap():
    """Recycle output must never exceed the rarity cap (4 = Thiên Phẩm),
    even for end-game equipment grades. Pin the invariant here so a future
    retune can't accidentally produce a non-existent grade-5+ material."""
    for grade in range(1, 10):
        recycle_mat = get_recycle_material_grade(grade)
        assert recycle_mat is not None
        assert 1 <= recycle_mat <= 4, (
            f"Grade {grade}: recycle yields mat_grade {recycle_mat} outside 1..4"
        )


# ── Random pick ─────────────────────────────────────────────────────────────


def test_recycle_returns_forge_material_of_expected_grade():
    """Across many rolls the returned key must always be a forge_material at
    the grade dictated by the mapping."""
    random.seed(42)
    for grade in range(1, 10):
        expected_mat_grade = get_recycle_material_grade(grade)
        for _ in range(20):
            key = recycle_equipment(grade)
            assert key is not None, f"No candidate for grade {grade}"
            item = registry.get_item(key)
            assert item is not None
            assert item["type"] == "forge_material"
            assert int(item["grade"]) == expected_mat_grade


def test_recycle_distribution_covers_all_candidates():
    """Over many rolls the random pick should hit every candidate at least
    once — guards against a regression to a deterministic single-key return."""
    random.seed(7)
    candidates_g1 = {
        k for k, v in registry.items.items()
        if v.get("type") == "forge_material" and int(v.get("grade", 0)) == 1
    }
    seen: set[str] = set()
    for _ in range(500):
        seen.add(recycle_equipment(1))  # type: ignore[arg-type]
    assert seen == candidates_g1, f"missing: {candidates_g1 - seen}"


def test_recycle_returns_none_for_invalid_grade():
    assert recycle_equipment(0) is None
    assert recycle_equipment(99) is None
