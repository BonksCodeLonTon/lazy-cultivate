"""Pin Đan Độc penalty curve so a balance change can't accidentally
silently flatten the cultivation/combat hooks."""
from __future__ import annotations

import pytest

from src.game.systems.toxicity import (
    MIN_CULT_SPEED_MULT,
    TOXICITY_CULT_SPEED_PENALTY_MAX,
    TOXICITY_FINAL_DMG_PENALTY_MAX,
    TOXICITY_FULL,
    TOXICITY_HP_REGEN_PENALTY_MAX,
    cult_speed_penalty,
    final_dmg_penalty,
    hp_regen_multiplier,
    tier_label,
    toxicity_factor,
)


def test_factor_zero_when_clean():
    assert toxicity_factor(0) == 0.0
    assert toxicity_factor(-50) == 0.0


def test_factor_saturates_at_full():
    assert toxicity_factor(TOXICITY_FULL) == 1.0
    assert toxicity_factor(TOXICITY_FULL * 5) == 1.0


@pytest.mark.parametrize("ratio,expected", [
    (0.00, 0.0),
    (0.25, 0.25),
    (0.50, 0.50),
    (0.75, 0.75),
    (1.00, 1.0),
])
def test_factor_linear_below_saturation(ratio, expected):
    """Curve is linear regardless of where TOXICITY_FULL lands — phrase the
    test in fractions of saturation so a balance change to the constant
    doesn't break the invariant."""
    assert toxicity_factor(int(TOXICITY_FULL * ratio)) == pytest.approx(expected, abs=0.01)


def test_cult_speed_penalty_at_saturation_matches_max():
    assert cult_speed_penalty(TOXICITY_FULL) == pytest.approx(TOXICITY_CULT_SPEED_PENALTY_MAX)
    assert cult_speed_penalty(0) == 0.0


def test_final_dmg_penalty_at_saturation_matches_max():
    assert final_dmg_penalty(TOXICITY_FULL) == pytest.approx(TOXICITY_FINAL_DMG_PENALTY_MAX)


def test_hp_regen_multiplier_floors_correctly():
    # Clean: full regen.
    assert hp_regen_multiplier(0) == 1.0
    # Saturated: regen reduced by max penalty (multiplicative).
    assert hp_regen_multiplier(TOXICITY_FULL) == pytest.approx(
        1.0 - TOXICITY_HP_REGEN_PENALTY_MAX
    )
    # Multiplier never goes negative.
    assert hp_regen_multiplier(TOXICITY_FULL * 100) >= 0.0


def test_tier_labels_cover_full_range():
    """Each tier label maps to a contiguous, ordered band of dan_doc."""
    assert tier_label(0) == "Thanh Khiết"
    assert tier_label(1) == "Khinh Độc"
    assert tier_label(int(TOXICITY_FULL * 0.30)) == "Trúng Độc"
    assert tier_label(int(TOXICITY_FULL * 0.60)) == "Trầm Độc"
    assert tier_label(TOXICITY_FULL) == "Mãn Độc"
    assert tier_label(TOXICITY_FULL * 5) == "Mãn Độc"


def test_pill_exp_multiplier_zeros_out_at_saturation():
    """Pill XP must hit zero at Mãn Độc — no floor like cultivation has."""
    from src.game.systems.toxicity import pill_exp_multiplier
    assert pill_exp_multiplier(0) == 1.0
    assert pill_exp_multiplier(TOXICITY_FULL // 2) == pytest.approx(0.5)
    assert pill_exp_multiplier(TOXICITY_FULL) == 0.0
    assert pill_exp_multiplier(TOXICITY_FULL * 5) == 0.0


def test_consume_pill_zero_xp_at_max_dan_doc():
    """End-to-end: an exp_luyen_the pill at max dan_doc grants 0 body_xp."""
    from src.game.models.character import Character, CharacterStats
    from src.game.systems.alchemy import consume_pill
    from src.data.registry import GameRegistry
    reg = GameRegistry.get()
    # Pick a Grade-1 pill so body_realm=0 stays under the grade gate; the
    # grade-vs-axis check fires before the toxicity multiplier and would
    # otherwise refuse the consume entirely, hiding the dan_doc effect we
    # actually want to test here.
    pill = next(
        (k for k, v in reg.items.items()
         if v.get("effect_key") == "exp_luyen_the" and int(v.get("grade", 1)) == 1),
        None,
    )
    assert pill, "expected at least one Grade-1 exp_luyen_the pill"

    char = Character(
        player_id=1, discord_id=1, name="X",
        stats=CharacterStats(),
        body_realm=0, qi_realm=0,
        dan_doc=TOXICITY_FULL,
    )
    res = consume_pill(char, pill, quality_tier=1)
    assert res.applied, "consumption still goes through (toxicity itself accrues)"
    assert res.body_xp_delta == 0, "max dan_doc must zero out pill EXP"
    # Reality check: the SAME pill on a clean character grants > 0.
    char_clean = Character(
        player_id=1, discord_id=1, name="X",
        stats=CharacterStats(),
        body_realm=0, qi_realm=0,
        dan_doc=0,
    )
    res_clean = consume_pill(char_clean, pill, quality_tier=1)
    assert res_clean.body_xp_delta > 0


def test_min_cult_speed_mult_floor_is_above_zero():
    """Sanity: a max-toxicity player still earns *some* EXP — the floor is
    a non-zero multiplier, so detoxing is recoverable instead of stuck."""
    assert MIN_CULT_SPEED_MULT > 0.0
    # And it sits below the saturated penalty, otherwise the floor would
    # make the penalty meaningless.
    assert MIN_CULT_SPEED_MULT < 1.0 - TOXICITY_CULT_SPEED_PENALTY_MAX + 0.01


def test_cultivation_xp_drops_with_toxicity():
    """End-to-end: equal turn counts produce strictly less EXP at higher
    toxicity. Pins the wiring in cultivation.py without coupling to the
    default constitution's speed bonus.
    """
    from src.game.models.character import Character, CharacterStats
    from src.game.systems.cultivation import advance_cultivation_xp

    def _xp(dan_doc: int) -> int:
        char = Character(
            player_id=1, discord_id=1, name="X",
            stats=CharacterStats(),
            active_axis="qi", qi_realm=0, qi_level=1, qi_xp=0,
            dan_doc=dan_doc,
        )
        return advance_cultivation_xp(char, turns=1000)["exp_gained"]

    clean = _xp(0)
    half = _xp(TOXICITY_FULL // 2)
    full = _xp(TOXICITY_FULL)

    assert clean > half > full, f"toxicity should monotonically slow EXP: {clean=} {half=} {full=}"
    # Floor still grants meaningful progress, never zero.
    assert full > 0
    assert full >= int(1000 * MIN_CULT_SPEED_MULT)
