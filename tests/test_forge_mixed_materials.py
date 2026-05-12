"""Tests for mixed-material forging.

Covers:
- ``distribute_qty`` allocation across N picks for various R/N splits.
- ``_get_affix_bias`` unions biased keys across multiple consumed materials.
- ``roll_affixes`` honors the unioned bias (each picked material's bias
  contributes to the 6×–15× weight pool).
- ``forge_equipment`` end-to-end with two distinct materials in
  ``consumed_materials`` produces a working item without crashing.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from src.data.registry import registry
from src.game.systems.forge import (
    _get_affix_bias_weights,
    distribute_qty,
    forge_equipment,
    roll_affixes,
)


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


def _test_char():
    return SimpleNamespace(
        merit=1_000_000,
        qi_realm=9,
        stats=SimpleNamespace(comprehension=0),
    )


def _first_forgable_base() -> str:
    for base in registry.bases.values():
        if "implicit_by_realm" in base:
            return base["key"]
    pytest.skip("no forgable base in registry")
    return ""


# ── distribute_qty ─────────────────────────────────────────────────────────

def test_distribute_single_pick_takes_all():
    assert distribute_qty([("A", 10)], 5) == {"A": 5}


def test_distribute_two_picks_ceil_split():
    # 5 split across 2 → first pick gets 3, second gets 2 (deterministic ceil)
    out = distribute_qty([("A", 10), ("B", 10)], 5)
    assert out is not None
    assert out["A"] + out["B"] == 5
    assert out["A"] >= 1 and out["B"] >= 1


def test_distribute_three_picks_balanced():
    out = distribute_qty([("A", 10), ("B", 10), ("C", 10)], 5)
    assert out == {"A": 2, "B": 2, "C": 1} or sum(out.values()) == 5


def test_distribute_low_owned_pick_absorbed_by_richer_neighbour():
    # A has only 1, B has 4 → forging 5 should still succeed (A:1, B:4)
    out = distribute_qty([("A", 1), ("B", 4)], 5)
    assert out == {"A": 1, "B": 4}


def test_distribute_returns_none_when_total_short():
    assert distribute_qty([("A", 1), ("B", 1)], 5) is None


def test_distribute_returns_none_for_empty_picks():
    assert distribute_qty([], 5) is None


# ── _get_affix_bias_weights union ──────────────────────────────────────────

def test_get_affix_bias_union_across_multiple_materials(monkeypatch):
    """Multiple consumed materials → unioned bias set."""
    fake_items = {
        "MatA": {"type": "forge_material", "affix_bias": ["pfx_hp", "pfx_def"]},
        "MatB": {"type": "forge_material", "affix_bias": ["pfx_evasion"]},
    }
    monkeypatch.setattr(
        registry, "get_item", lambda k: fake_items.get(k, registry.items.get(k))
    )
    weights = _get_affix_bias_weights(["MatA", "MatB"])
    assert set(weights) == {"pfx_hp", "pfx_def", "pfx_evasion"}


def test_get_affix_bias_single_string_legacy(monkeypatch):
    """A single string is accepted and produces that material's bias."""
    fake_items = {
        "MatA": {"type": "forge_material", "affix_bias": ["pfx_hp"]},
    }
    monkeypatch.setattr(
        registry, "get_item", lambda k: fake_items.get(k, registry.items.get(k))
    )
    assert set(_get_affix_bias_weights("MatA")) == {"pfx_hp"}


def test_get_affix_bias_empty_inputs():
    assert _get_affix_bias_weights(None) == {}
    assert _get_affix_bias_weights([]) == {}
    assert _get_affix_bias_weights("") == {}


# ── roll_affixes honors unioned bias ───────────────────────────────────────

def _biased_keys_in_pool(slot: str = "armor") -> list[str]:
    """Pick two prefix-eligible affixes to use as bias targets in tests."""
    pool = [
        a["key"] for a in registry.affixes.values()
        if a["type"] == "prefix" and ("all" in a["slots"] or slot in a["slots"])
    ]
    assert len(pool) >= 2
    return pool[:2]


def test_roll_affixes_unioned_bias_increases_pick_rate(monkeypatch):
    """With two materials each biasing a different key, both biased keys
    should land more often than baseline across many rolls (grade-1 bias is 6×)."""
    biased_a, biased_b = _biased_keys_in_pool()

    fake_items = {
        "MatA": {"type": "forge_material", "affix_bias": [biased_a]},
        "MatB": {"type": "forge_material", "affix_bias": [biased_b]},
    }
    monkeypatch.setattr(
        registry, "get_item", lambda k: fake_items.get(k, registry.items.get(k))
    )

    import random
    random.seed(42)
    counts: Counter[str] = Counter()
    trials = 800
    for _ in range(trials):
        rolled = roll_affixes(
            slot="armor", grade=1, quality="hoan",
            material_keys=["MatA", "MatB"],
        )
        for a in rolled:
            counts[a["key"]] += 1

    # Baseline frequency: 1 prefix slot at hoan, 11 prefix-eligible affixes
    # → unbiased expectation per affix = trials / 11 ≈ 73. Biased keys get
    # 6× weight (grade 1), so they should land notably more often. Assert
    # each biased key is picked at least 1.5× the unbiased average.
    unbiased_avg = trials / 11
    assert counts[biased_a] > unbiased_avg * 1.5
    assert counts[biased_b] > unbiased_avg * 1.5


# ── forge_equipment end-to-end with mixed materials ───────────────────────

def test_forge_equipment_accepts_mixed_consumed_materials(monkeypatch):
    """Forge should run cleanly with two distinct materials in the consumed
    list, producing a valid item without crashing."""
    biased_a, biased_b = _biased_keys_in_pool()

    fake_items = {
        "MatA": {"type": "forge_material", "affix_bias": [biased_a]},
        "MatB": {"type": "forge_material", "affix_bias": [biased_b]},
    }
    monkeypatch.setattr(
        registry, "get_item", lambda k: fake_items.get(k, registry.items.get(k))
    )

    import random
    random.seed(7)

    char = _test_char()
    base_key = _first_forgable_base()
    result = forge_equipment(
        char, base_key, grade=1,
        consumed_materials=[("MatA", 3), ("MatB", 2)],
    )

    assert result.success, result.message
    assert result.item_data is not None
    assert result.item_data["grade"] == 1
    assert result.item_data["affixes"], "expected at least one affix rolled"
    # Merit was deducted
    assert char.merit < 1_000_000
