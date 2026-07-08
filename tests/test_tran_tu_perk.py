"""Tests for the cross-path balance pass: Trận Hộ Thuẫn + linh-căn fdr cap.

Benchmark context: Khí Tu (9 roots lv9) beat Trận Tu 24-0 — the linh-căn
layer stacked final_dmg_reduce to the 0.90 global cap while Trận Tu had no
defensive identity at all. Fix pair: cap the summed linh-căn fdr layer, and
give Trận Tu its archetype payoff (reserved formation MP → shield + per-slot
warding). Post-fix anchor: 14-10.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.constants.balance import (
    TRAN_TU_FDR_PER_ACTIVE_FORMATION,
    TRAN_TU_RESERVE_SHIELD_MULT,
    TRAN_TU_RESERVE_SHIELD_REGEN_PCT,
)
from src.game.constants.linh_can import (
    ALL_LINH_CAN,
    LINH_CAN_FDR_STACK_CAP,
    compute_linh_can_bonuses,
)
from src.game.models.character import Character, CharacterStats
from src.game.systems.character_stats import compute_combat_stats


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Linh-căn fdr stacking cap ────────────────────────────────────────────────


def test_nine_roots_fdr_is_capped():
    bonuses = compute_linh_can_bonuses({e: 9 for e in ALL_LINH_CAN})
    assert bonuses.get("final_dmg_reduce", 0.0) == pytest.approx(LINH_CAN_FDR_STACK_CAP)


def test_single_root_fdr_untouched():
    for elem in ALL_LINH_CAN:
        solo = compute_linh_can_bonuses({elem: 9})
        assert solo.get("final_dmg_reduce", 0.0) <= LINH_CAN_FDR_STACK_CAP


# ── Trận Hộ Thuẫn (formation-path barrier) ───────────────────────────────────


def _reserving_formation() -> str:
    """A real formation whose channel skill reserves MP."""
    for form in registry.formations.values():
        skill = registry.get_skill(form.get("formation_skill_key") or "")
        if skill and float(skill.get("reserved_mp_pct", 0.0)) > 0:
            return form["key"]
    pytest.skip("no formation with reserved_mp_pct in data")


def _char(axis: str, formation_key: str) -> Character:
    return Character(
        player_id=1, discord_id=1, name="t",
        body_realm=8, body_level=9, qi_realm=8, qi_level=9,
        formation_realm=8, formation_level=9,
        active_axis=axis, active_formation=formation_key,
        constitution_type="", stats=CharacterStats(),
    )


def test_tran_tu_reserved_mp_becomes_shield_and_warding():
    fk = _reserving_formation()
    on_path = compute_combat_stats(_char("formation", fk))
    assert on_path.mp_reserved > 0
    assert on_path.shield_max >= int(
        on_path.mp_reserved * TRAN_TU_RESERVE_SHIELD_MULT
    )
    assert on_path.shield_regen_pct >= TRAN_TU_RESERVE_SHIELD_REGEN_PCT
    assert on_path.final_dmg_reduce >= TRAN_TU_FDR_PER_ACTIVE_FORMATION


def test_off_path_formation_users_get_no_barrier():
    fk = _reserving_formation()
    off_path = compute_combat_stats(_char("qi", fk))
    # Same formation active, same reservation — but the barrier is the
    # Trận Tu archetype payoff, not a generic formation feature.
    assert off_path.mp_reserved > 0
    assert off_path.shield_max < int(
        off_path.mp_reserved * TRAN_TU_RESERVE_SHIELD_MULT
    )
    assert off_path.shield_regen_pct < TRAN_TU_RESERVE_SHIELD_REGEN_PCT


def test_no_formation_no_barrier():
    cs = compute_combat_stats(_char("formation", ""))
    assert cs.mp_reserved == 0
    assert cs.shield_max == 0
