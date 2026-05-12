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


# ── Grade gating: pill grade vs. player cultivation grade ──────────────────


def _find_pill(effect_key: str, grade: int) -> str | None:
    """Locate a pill in the registry by effect_key + grade."""
    for p in registry.items.values():
        if (p.get("type") == "pill"
                and p.get("effect_key") == effect_key
                and int(p.get("grade", 0)) == grade):
            return p["key"]
    return None


def test_consume_exp_luyen_the_refused_when_body_realm_exceeds_pill_realm():
    """A realm-0 body XP pill must not absorb into a Cấp-2 (body_realm=1)
    player — the body has surpassed what the pill can offer."""
    pill_key = _find_pill("exp_luyen_the", 1)
    assert pill_key, "expected a Grade-1 exp_luyen_the pill"

    char = _make_char()
    char.body_realm = 1   # one tier above the pill's realm (grade 1 → realm 0)
    effect = consume_pill(char, pill_key, 1)
    assert not effect.applied
    assert effect.body_xp_delta == 0
    assert "vượt qua" in effect.message  # gated message wording


def test_consume_exp_qi_refused_when_qi_realm_exceeds_pill_grade():
    pill_key = _find_pill("exp_qi", 1)
    assert pill_key, "expected a Grade-1 exp_qi pill"

    char = _make_char(qi_realm=1)   # one tier above the pill
    effect = consume_pill(char, pill_key, 1)
    assert not effect.applied
    assert effect.qi_xp_delta == 0


def test_consume_reduce_toxicity_refused_when_either_axis_exceeds_pill_grade():
    """``reduce_toxicity`` is global, so ANY axis exceeding the pill grade
    triggers the gate — toxicity comes from both body + qi pill use."""
    pill_key = _find_pill("reduce_toxicity", 2)
    assert pill_key, "expected a Grade-2 reduce_toxicity pill"

    char = _make_char()
    char.body_realm = 2   # body alone exceeds grade-2 pill
    effect = consume_pill(char, pill_key, 1)
    assert not effect.applied
    assert effect.dan_doc_delta == 0   # gated → no delta on the refused effect


def test_consume_at_matching_grade_is_allowed():
    """Boundary: pill_grade == player_realm + 1 (i.e., player_realm < pill_grade)
    means the player is still within the pill's tier — consume goes through."""
    pill_key = _find_pill("exp_luyen_the", 3)
    assert pill_key, "expected a Grade-3 exp_luyen_the pill"

    char = _make_char()
    char.body_realm = 2   # Cấp 3 == pill grade → still inside the tier
    effect = consume_pill(char, pill_key, 1)
    assert effect.applied
    assert effect.body_xp_delta > 0


def test_pill_xp_scales_with_pill_grade_not_player_realm():
    """A Grade-3 pill should grant strictly more XP than a Grade-1 pill at
    Hoàng quality, regardless of the consumer's own realm — XP follows the
    pill, not the consumer."""
    g1 = _find_pill("exp_luyen_the", 1)
    g3 = _find_pill("exp_luyen_the", 3)
    assert g1 and g3

    # Same low-realm consumer — under both grades' gates.
    char_lo = _make_char()
    char_hi = _make_char()
    eff_lo = consume_pill(char_lo, g1, 1)
    eff_hi = consume_pill(char_hi, g3, 1)
    assert eff_lo.applied and eff_hi.applied
    assert eff_hi.body_xp_delta > eff_lo.body_xp_delta


def test_reduce_toxicity_magnitude_scales_with_pill_grade():
    """Higher-grade purifiers must remove more Đan Độc per Hoàn pill."""
    g2 = _find_pill("reduce_toxicity", 2)
    g5 = _find_pill("reduce_toxicity", 5)
    if not (g2 and g5):
        pytest.skip("need both Grade-2 and Grade-5 reduce_toxicity pills in data")

    char = _make_char()
    eff_lo = consume_pill(char, g2, 1)
    eff_hi = consume_pill(char, g5, 1)
    assert eff_lo.applied and eff_hi.applied
    # dan_doc_delta is negative for purifiers; "more removed" = "more negative".
    assert eff_hi.dan_doc_delta < eff_lo.dan_doc_delta


def test_pill_xp_per_grade_is_monotonic():
    """No grade should grant less XP than the grade below it.

    R8 (Đăng Tiên/Nhập Thánh) has an anomalously small bậc-9 threshold
    (= ``TRIBULATION_EXP_COST``), so a naive ``threshold // target_pills``
    formula gives Grade-9 pills ~270 XP vs Grade-8's ~1,264 — inverted.
    The function carries a running-max floor to keep the curve monotonic;
    pin it so a future refactor can't silently regress it.
    """
    from src.game.systems.alchemy import _pill_xp_for_grade
    for axis in ("body", "qi"):
        prev = 0
        for grade in range(1, 10):
            xp = _pill_xp_for_grade(axis, grade)
            assert xp >= prev, (
                f"axis={axis} grade={grade}: {xp} XP/pill < grade-{grade-1}'s {prev}"
            )
            prev = xp


# ── AlchemyResult.consumed shape ───────────────────────────────────────────


def test_craft_pill_consumed_matches_recipe_slots():
    """``AlchemyResult.consumed`` must list one IngredientPick per recipe slot,
    each with the correct (key, qty) and slot_role tag — the cog uses this
    list verbatim to deduct inventory rows."""
    random.seed(11)
    recipe = get_recipe("DanPhuongKimLuyenHuyetDan")
    expected = [(slot["role"], slot["options"][0]["key"], slot["options"][0]["qty"])
                for slot in recipe["ingredients"]]

    char = _make_char(merit=5_000)
    result = craft_pill(
        char, "DanPhuongKimLuyenHuyetDan", _default_inv(), _default_furnaces(),
    )
    assert result.success
    assert len(result.consumed) == len(expected)
    actual = [(p.slot_role, p.key, p.qty) for p in result.consumed]
    assert actual == expected


def test_craft_pill_returns_chosen_furnace_key():
    random.seed(11)
    char = _make_char(merit=5_000)
    result = craft_pill(
        char, "DanPhuongKimLuyenHuyetDan", _default_inv(),
        ["DanLoThuong_G1", "DanLoBachLuyen_G1"],
    )
    assert result.success
    # Best furnace is the unique one
    assert result.furnace_key == "DanLoBachLuyen_G1"


# ── apply_furnace_bonus ────────────────────────────────────────────────────


def test_apply_furnace_bonus_shifts_weights_and_compensates_hoan():
    from src.game.systems.alchemy import apply_furnace_bonus
    base = {"hoan": 0.70, "huyen": 0.20, "dia": 0.08, "thien": 0.02}
    furnace = {"quality_bonus": {"thien": 0.05, "dia": 0.05}}
    out = apply_furnace_bonus(base, furnace)
    # Bonuses added to their tiers
    assert out["thien"] == pytest.approx(0.07)
    assert out["dia"] == pytest.approx(0.13)
    # Hoàng pays for the added weight (0.10 reduction)
    assert out["hoan"] == pytest.approx(0.60)
    # Total approximately preserved
    assert sum(out.values()) == pytest.approx(sum(base.values()), abs=1e-9)


def test_apply_furnace_bonus_no_furnace_returns_copy_of_chances():
    from src.game.systems.alchemy import apply_furnace_bonus
    base = {"hoan": 0.7, "huyen": 0.3}
    out = apply_furnace_bonus(base, None)
    assert out == base
    assert out is not base   # must be a copy, not the same dict


# ── Comprehension stat boosts thien rate ───────────────────────────────────


def test_high_comprehension_shifts_quality_toward_thien():
    """Comprehension caps at +10% thien weight before renormalisation, so a
    high-comprehension character should hit Thiên measurably more often than
    a zero-comprehension one across many rolls (seeded for stability)."""
    def roll_n(comp: int, n: int = 1500) -> dict:
        random.seed(99)
        char = _make_char(merit=10 ** 7, comprehension=comp)
        counts = {"hoan": 0, "huyen": 0, "dia": 0, "thien": 0}
        for _ in range(n):
            char.merit = 10 ** 7
            r = craft_pill(char, "DanPhuongKimLuyenHuyetDan", _default_inv(), _default_furnaces())
            if r.success:
                counts[r.quality] += 1
        return counts

    low = roll_n(comp=0)
    high = roll_n(comp=200)   # well past the cap
    assert high["thien"] > low["thien"]


# ── consume_pill — additional effect pathways ──────────────────────────────


def _find_pill_by_effect(effect_key: str) -> str | None:
    for p in registry.items.values():
        if p.get("type") == "pill" and p.get("effect_key") == effect_key:
            return p["key"]
    return None


def test_consume_restore_hp_pill_returns_heal_delta():
    target = _find_pill_by_effect("restore_hp")
    if not target:
        pytest.skip("no restore_hp pill in data")
    char = _make_char()
    effect = consume_pill(char, target, 4)   # Thiên = max heal
    assert effect.applied
    assert effect.heal_delta > 0
    assert effect.body_xp_delta == 0
    assert effect.qi_xp_delta == 0


def test_consume_breakthrough_pill_grants_qi_xp_scaled_by_quality():
    """Breakthrough-tier pills push qi_xp; Thiên-quality should grant strictly
    more than Hoàng-quality due to the implicit multiplier."""
    target = _find_pill_by_effect("breakthrough_kim_dan")
    if not target:
        pytest.skip("no breakthrough_kim_dan pill in data")

    char_lo = _make_char()
    eff_lo = consume_pill(char_lo, target, 1)  # Hoàng
    char_hi = _make_char()
    eff_hi = consume_pill(char_hi, target, 4)  # Thiên

    assert eff_lo.applied and eff_hi.applied
    assert eff_hi.qi_xp_delta > eff_lo.qi_xp_delta > 0
    # Breakthrough pills don't grant body_xp
    assert eff_hi.body_xp_delta == 0
    # And Thiên reduces toxicity accrual
    assert eff_hi.dan_doc_delta < eff_lo.dan_doc_delta


# ── Best-furnace selection on craft ────────────────────────────────────────


def test_craft_picks_best_furnace_when_multiple_owned():
    """Owning a stronger unique furnace alongside a weaker one should yield
    the unique's quality bonus — verified via the AlchemyResult footprint."""
    random.seed(13)
    char = _make_char(merit=5_000)
    result = craft_pill(
        char, "DanPhuongKimLuyenHuyetDan", _default_inv(),
        ["DanLoThuong_G1", "DanLoBachLuyen_G1"],
    )
    assert result.success
    assert result.furnace_key == "DanLoBachLuyen_G1"


def test_craft_uses_only_the_furnace_passed():
    """The cog's furnace-picker passes ``[chosen_key]`` to the system; verify
    that constraint actually constrains the craft. Owning a stronger unique
    furnace should NOT override the user's pick of a weaker normal one."""
    random.seed(19)
    char = _make_char(merit=5_000)
    # Pretend the player picked the normal furnace even though they own
    # the better unique — only the chosen one gets passed through.
    result = craft_pill(
        char, "DanPhuongKimLuyenHuyetDan", _default_inv(),
        ["DanLoThuong_G1"],
    )
    assert result.success
    assert result.furnace_key == "DanLoThuong_G1"
