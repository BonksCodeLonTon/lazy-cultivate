"""Tests for the Luyện Đan system (src/game/systems/alchemy.py)."""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.quality import (
    QUALITY_SPECIAL,
    implicit_multiplier,
    quality_tier_index,
    roll_quality,
)
from src.game.models.character import Character, CharacterStats
from src.game.systems.alchemy import (
    AlchemyResult,
    check_requirements,
    consume_pill,
    craft_pill,
    get_recipe,
)


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _make_char(qi_realm: int = 0, merit: int = 10_000, comprehension: int = 0) -> Character:
    char = Character(player_id=1, discord_id=42, name="T")
    char.qi_realm = qi_realm
    char.merit = merit
    char.stats = CharacterStats(comprehension=comprehension)
    char.body_xp = 0
    char.qi_xp = 0
    return char


# ── Quality module ──────────────────────────────────────────────────────────


def test_quality_tier_index_and_implicit_multiplier_match_spec():
    assert quality_tier_index("hoan") == 1
    assert quality_tier_index("thien") == 4
    assert implicit_multiplier("hoan") == 1.0
    assert implicit_multiplier("thien") == QUALITY_SPECIAL["thien"]["implicit_mult"]


def test_roll_quality_respects_distribution_with_seeded_rng():
    random.seed(123)
    chances = {"hoan": 0.70, "huyen": 0.20, "dia": 0.08, "thien": 0.02}
    counts = {"hoan": 0, "huyen": 0, "dia": 0, "thien": 0}
    for _ in range(2000):
        counts[roll_quality(chances)] += 1
    # Hoàng should dominate
    assert counts["hoan"] > counts["huyen"] > counts["dia"] >= counts["thien"]
    # Thiên should be rare but non-zero
    assert counts["thien"] > 0


# ── Alchemy validation ──────────────────────────────────────────────────────


def _default_inv():
    return {"KimHuyetTinh": 5, "HaThuO": 5, "NgungHuyetThao": 10}


def _default_furnaces():
    return ["DanLoThuong_G1"]


def test_check_requirements_blocks_below_min_realm():
    recipe = get_recipe("DanPhuongKimLuyenHuyetDan")
    assert recipe is not None
    char = _make_char(qi_realm=0, merit=10_000)
    forced = dict(recipe)
    forced["min_qi_realm"] = 5
    ok, err, picks, _ = check_requirements(char, forced, _default_inv(), _default_furnaces())
    assert not ok
    assert "Luyện Khí" in err
    assert picks == []


def test_check_requirements_blocks_without_merit():
    recipe = get_recipe("DanPhuongKimLuyenHuyetDan")
    char = _make_char(merit=0)
    ok, err, *_ = check_requirements(char, recipe, _default_inv(), _default_furnaces())
    assert not ok
    assert "Công Đức" in err


def test_check_requirements_reports_missing_ingredients():
    recipe = get_recipe("DanPhuongKimLuyenHuyetDan")
    char = _make_char()
    ok, err, picks, _ = check_requirements(char, recipe, {}, _default_furnaces())
    assert not ok
    assert "Thiếu nguyên liệu" in err


def test_check_requirements_blocks_without_furnace():
    recipe = get_recipe("DanPhuongKimLuyenHuyetDan")
    char = _make_char()
    ok, err, _, chosen = check_requirements(char, recipe, _default_inv(), owned_furnace_keys=[])
    assert not ok
    assert "Đan Lô" in err
    assert chosen is None


def test_check_requirements_blocks_with_undersized_furnace():
    """A tier-1 furnace should not satisfy a tier-2 recipe."""
    # Pick any tier-2 recipe
    recipe = next(r for r in registry.pill_recipes.values() if int(r.get("furnace_tier", 1)) == 2)
    char = _make_char(qi_realm=recipe["min_qi_realm"], merit=recipe["cost_cong_duc"] * 2)
    # Stock every ingredient slot
    inv = {opt["key"]: opt["qty"] + 5 for slot in recipe["ingredients"] for opt in slot["options"]}
    ok, err, _, chosen = check_requirements(char, recipe, inv, ["DanLoThuong_G1"])
    assert not ok, err
    assert "Cấp 2" in err
    assert chosen is None


def test_check_requirements_prefers_unique_furnace_over_normal():
    from src.game.systems.alchemy import _pick_best_furnace

    recipe = get_recipe("DanPhuongKimLuyenHuyetDan")
    char = _make_char()
    ok, _, _, chosen = check_requirements(
        char, recipe, _default_inv(), ["DanLoThuong_G1", "DanLoBachLuyen_G1"],
    )
    assert ok
    assert chosen is not None
    assert chosen["is_unique"] is True
    # Helper independently agrees
    alt = _pick_best_furnace(["DanLoThuong_G1", "DanLoBachLuyen_G1"], 1)
    assert alt is not None and alt["is_unique"] is True


def test_check_requirements_picks_first_satisfiable_option():
    """Recipes with alternate ingredient options should pick the first owned one."""
    alt_recipe = None
    for r in registry.pill_recipes.values():
        if any(len(slot["options"]) > 1 for slot in r["ingredients"]):
            alt_recipe = r
            break
    assert alt_recipe is not None

    char = _make_char(qi_realm=alt_recipe["min_qi_realm"], merit=alt_recipe["cost_cong_duc"] * 2)
    inv = {}
    for slot in alt_recipe["ingredients"]:
        last = slot["options"][-1]
        inv[last["key"]] = last["qty"] + 2
    # Need a furnace of adequate tier
    tier = int(alt_recipe.get("furnace_tier", 1))
    furnace_key = {1: "DanLoThuong_G1", 2: "DanLoThuong_G2",
                   3: "DanLoThuong_G3", 4: "DanLoThuong_G4"}[tier]
    ok, err, picks, _ = check_requirements(char, alt_recipe, inv, [furnace_key])
    assert ok, err
    chosen_keys = {p.key for p in picks}
    expected_keys = {slot["options"][-1]["key"] for slot in alt_recipe["ingredients"]}
    assert chosen_keys == expected_keys


# ── craft_pill ──────────────────────────────────────────────────────────────


def test_craft_pill_happy_path_consumes_merit_and_returns_tier():
    random.seed(7)
    char = _make_char(merit=5_000)
    result = craft_pill(
        char, "DanPhuongKimLuyenHuyetDan", _default_inv(), _default_furnaces(),
    )
    assert result.success
    assert result.pill_key == "KimLuyenHuyetDan"
    assert 1 <= result.quality_tier <= 4
    assert result.furnace_key == "DanLoThuong_G1"
    assert char.merit == 5_000 - result.cost_cong_duc
    base_doc = registry.get_pill("KimLuyenHuyetDan")["dan_doc"]
    assert result.dan_doc_delta <= base_doc


def test_craft_pill_fails_without_merit():
    char = _make_char(merit=0)
    result = craft_pill(
        char, "DanPhuongKimLuyenHuyetDan", _default_inv(), _default_furnaces(),
    )
    assert not result.success
    assert char.merit == 0


def test_craft_pill_fails_without_any_furnace():
    char = _make_char()
    result = craft_pill(char, "DanPhuongKimLuyenHuyetDan", _default_inv(), owned_furnace_keys=[])
    assert not result.success
    assert "Đan Lô" in result.message


def test_craft_pill_quality_distribution_seeded():
    """Over many rolls, distribution should be dominated by Hoàng."""
    random.seed(1234)
    char = _make_char(merit=10 ** 7)
    counts = {"hoan": 0, "huyen": 0, "dia": 0, "thien": 0}
    for _ in range(500):
        char.merit = 10 ** 7
        r = craft_pill(char, "DanPhuongKimLuyenHuyetDan", _default_inv(), _default_furnaces())
        if r.success:
            counts[r.quality] += 1
    assert counts["hoan"] > counts["huyen"] > counts["dia"]


def test_unique_furnace_raises_high_quality_rates():
    """A unique furnace should measurably increase Địa+Thiên hits vs a normal one."""
    def roll_n(seed: int, furnace: str, n: int = 2000) -> dict:
        random.seed(seed)
        char = _make_char(merit=10 ** 8)
        counts = {"hoan": 0, "huyen": 0, "dia": 0, "thien": 0}
        for _ in range(n):
            char.merit = 10 ** 8
            r = craft_pill(char, "DanPhuongKimLuyenHuyetDan", _default_inv(), [furnace])
            if r.success:
                counts[r.quality] += 1
        return counts

    normal = roll_n(42, "DanLoThuong_G1")
    unique = roll_n(42, "DanLoBachLuyen_G1")
    assert unique["dia"] + unique["thien"] > normal["dia"] + normal["thien"]
    assert unique["thien"] >= normal["thien"]


# ── consume_pill ────────────────────────────────────────────────────────────


def test_consume_pill_applies_higher_effect_at_higher_quality():
    char = _make_char()
    low = consume_pill(char, "KimLuyenHuyetDan", 1)    # Hoàng
    char_b = _make_char()
    high = consume_pill(char_b, "KimLuyenHuyetDan", 4) # Thiên
    assert low.applied and high.applied
    # Exp boost should scale with implicit multiplier (1.0 vs 1.5)
    assert high.body_xp_delta > low.body_xp_delta


def test_consume_pill_thien_quality_reduces_toxicity_accrual():
    char = _make_char()
    hoan = consume_pill(char, "KimLuyenHuyetDan", 1)
    char_b = _make_char()
    thien = consume_pill(char_b, "KimLuyenHuyetDan", 4)
    assert thien.dan_doc_delta < hoan.dan_doc_delta


def test_consume_purification_pill_yields_negative_dan_doc_delta():
    """Pills with effect_key=reduce_toxicity should subtract from dan_doc."""
    # Find any pill marked with reduce_toxicity effect_key
    target = None
    for p in registry.items.values():
        if p.get("type") == "pill" and p.get("effect_key") == "reduce_toxicity":
            target = p["key"]
            break
    assert target is not None, "expected at least one purification pill in data"
    char = _make_char()
    effect = consume_pill(char, target, 1)
    assert effect.applied
    assert effect.dan_doc_delta < 0


def test_consume_unknown_pill_returns_not_applied():
    char = _make_char()
    effect = consume_pill(char, "DoesNotExist_123", 1)
    assert not effect.applied
