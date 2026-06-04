"""Constitution Process (Tiến Trình Thể Chất) — Phase 1 pure-core unit tests.

Mirrors the skill-mastery unit tests. Everything here exercises the pure
logic in ``src.game.systems.constitution_process`` over SYNTHETIC constitution
dicts — no real ``src/data`` file gains a ``process`` block in this phase — plus
one inertness check that the ``compute_constitution_bonuses`` seam with
``process_levels=None`` is byte-identical to the pre-existing call.

RNG is always injected (``roll`` args) so the breakthrough tests are
deterministic.
"""
from __future__ import annotations

import pytest

from src.game.constants.constitution_process import (
    BAND_LABELS,
    CEILINGS,
    GATES,
    MAX_LEVEL,
    band_of,
)
from src.game.systems import constitution_process as cp
from src.game.systems.cultivation import compute_constitution_bonuses
from src.utils.config import settings


# ── Fixtures ────────────────────────────────────────────────────────────────
def _flat_body() -> dict:
    """A constitution WITHOUT a process block — the ~242 existing-body shape."""
    return {
        "stat_bonuses": {
            "hp_pct": 0.03,
            "crit_rating": 60,
            "dot_dmg_bonus_by_kind": {"bleed": 0.10},
        }
    }


def _process_body() -> dict:
    """A synthetic constitution WITH a process block.

    Milestones 1/3/6/9 each layer additively; milestone 3 also stacks a nested
    ``dot_dmg_bonus_by_kind`` entry; ``per_level_growth`` adds 5 crit_rating per
    level.
    """
    return {
        "stat_bonuses": {
            "hp_pct": 0.03,
            "crit_rating": 60,
            "dot_dmg_bonus_by_kind": {"bleed": 0.10},
            "poison_immunity": True,
        },
        "process": {
            "milestones": [1, 3, 6, 9],
            "per_level_growth": {"crit_rating": 5},
            "levels": {
                "1": {"stat_bonuses": {"hp_pct": 0.01}, "effects": ["e_base"]},
                "3": {
                    "stat_bonuses": {
                        "crit_rating": 20,
                        "dot_dmg_bonus_by_kind": {"bleed": 0.05},
                    },
                    "effects": ["e_tempered"],
                },
                "6": {
                    "stat_bonuses": {"final_dmg_bonus": 0.10},
                    "effects": ["e_consummate", "e_base"],  # dup of e_base
                },
                "9": {"stat_bonuses": {"hp_pct": 0.05}, "effects": ["e_apex"]},
            },
        },
    }


# ── 1. Flag default ─────────────────────────────────────────────────────────
def test_flag_disabled_by_default():
    """Constitution Process ships OFF until content + persistence land."""
    assert settings.constitution_process_enabled is False


# ── 2. effective_stat_bonuses — no process block returns flat unchanged ──────
def test_effective_stat_bonuses_no_process_returns_flat():
    """A body with no ``process`` block yields its flat ``stat_bonuses`` as-is.

    This is the backward-compat contract that keeps every existing body
    byte-identical. The returned dict is a copy (mutating it must not corrupt
    the source).
    """
    body = _flat_body()
    flat = body["stat_bonuses"]
    for level in (1, 5, 9):
        out = cp.effective_stat_bonuses(body, level)
        assert out == flat
        out["hp_pct"] = 999  # mutate the result …
    assert body["stat_bonuses"]["hp_pct"] == 0.03  # … source untouched


# ── 3. effective_stat_bonuses — composition at milestone levels ─────────────
def test_effective_stat_bonuses_composition_at_milestones():
    body = _process_body()

    # L1 — only milestone 1 applies; growth 5×(1-1)=0 (the "L1 == flat" identity).
    l1 = cp.effective_stat_bonuses(body, 1)
    assert l1["hp_pct"] == pytest.approx(0.04)            # 0.03 + 0.01
    assert l1["crit_rating"] == 60                         # 60 + 5×0
    assert l1["dot_dmg_bonus_by_kind"]["bleed"] == pytest.approx(0.10)
    assert l1["poison_immunity"] is True                   # bool carried
    assert "final_dmg_bonus" not in l1

    # L3 — milestones 1 + 3; nested bleed stacks; growth 5×(3-1).
    l3 = cp.effective_stat_bonuses(body, 3)
    assert l3["hp_pct"] == pytest.approx(0.04)
    assert l3["crit_rating"] == 90                         # 60 + 20 + 5×2
    assert l3["dot_dmg_bonus_by_kind"]["bleed"] == pytest.approx(0.15)

    # L6 — milestones 1 + 3 + 6; growth 5×(6-1).
    l6 = cp.effective_stat_bonuses(body, 6)
    assert l6["crit_rating"] == 105                        # 60 + 20 + 5×5
    assert l6["final_dmg_bonus"] == pytest.approx(0.10)

    # L9 — all milestones; growth 5×(9-1); bleed only from milestone 3.
    l9 = cp.effective_stat_bonuses(body, 9)
    assert l9["hp_pct"] == pytest.approx(0.09)             # 0.03 + 0.01 + 0.05
    assert l9["crit_rating"] == 120                        # 60 + 20 + 5×8
    assert l9["dot_dmg_bonus_by_kind"]["bleed"] == pytest.approx(0.15)
    assert l9["final_dmg_bonus"] == pytest.approx(0.10)


def test_effective_stat_bonuses_between_milestones():
    """A level between milestones includes only milestones <= level."""
    body = _process_body()

    # L4 — milestones {1, 3} (not 6); growth 5×(4-1).
    l4 = cp.effective_stat_bonuses(body, 4)
    assert l4["crit_rating"] == 95                         # 60 + 20 + 5×3
    assert "final_dmg_bonus" not in l4                     # milestone 6 not yet

    # L5 (a ceiling) — still only {1, 3}; growth 5×(5-1).
    l5 = cp.effective_stat_bonuses(body, 5)
    assert l5["crit_rating"] == 100                        # 60 + 20 + 5×4
    assert "final_dmg_bonus" not in l5


def test_effective_stat_bonuses_is_pure_across_calls():
    """Repeated/interleaved calls never mutate the source or leak between them.

    Guards the deepcopy: ``_merge_bonus_dict`` mutates nested dicts in place,
    so a shallow base would corrupt the shared ``dot_dmg_bonus_by_kind``.
    """
    body = _process_body()
    first_l9 = cp.effective_stat_bonuses(body, 9)
    cp.effective_stat_bonuses(body, 4)        # interleave
    cp.effective_stat_bonuses(body, 6)
    second_l9 = cp.effective_stat_bonuses(body, 9)
    assert first_l9 == second_l9
    # Source nested dict is pristine.
    assert body["stat_bonuses"]["dot_dmg_bonus_by_kind"]["bleed"] == pytest.approx(0.10)
    assert body["stat_bonuses"]["hp_pct"] == 0.03


# ── 4. effective_effects ────────────────────────────────────────────────────
def test_effective_effects_dedup_union_up_to_level():
    body = _process_body()
    assert cp.effective_effects(body, 1) == ["e_base"]
    assert cp.effective_effects(body, 3) == ["e_base", "e_tempered"]
    # Milestone 6 re-lists e_base — must dedup, first-seen order preserved.
    assert cp.effective_effects(body, 6) == ["e_base", "e_tempered", "e_consummate"]
    assert cp.effective_effects(body, 9) == [
        "e_base",
        "e_tempered",
        "e_consummate",
        "e_apex",
    ]
    # No process block → no effects.
    assert cp.effective_effects(_flat_body(), 9) == []


# ── 5. xp_to_next ───────────────────────────────────────────────────────────
def test_xp_to_next_none_at_ceilings_and_max():
    for ceiling in CEILINGS:
        assert cp.xp_to_next(ceiling) is None
    assert cp.xp_to_next(MAX_LEVEL) is None
    # Within-band transitions present.
    assert cp.xp_to_next(1) == 8
    assert cp.xp_to_next(3) == 20
    assert cp.xp_to_next(4) == 30
    assert cp.xp_to_next(6) == 45
    assert cp.xp_to_next(7) == 65


def test_is_ceiling():
    assert [L for L in range(1, MAX_LEVEL + 1) if cp.is_ceiling(L)] == list(CEILINGS)


# ── 6. band_of / band_label ─────────────────────────────────────────────────
def test_band_of_maps_to_highest_milestone():
    assert [band_of(L) for L in range(0, 11)] == [1, 1, 1, 3, 3, 3, 6, 6, 6, 9, 9]


def test_band_label_matches_table():
    assert cp.band_label(1) == BAND_LABELS[1] == ("Sơ Khai", "Nascent")
    assert cp.band_label(4) == BAND_LABELS[3] == ("Tiểu Thành", "Tempered")
    assert cp.band_label(7) == BAND_LABELS[6] == ("Đại Thành", "Consummate")
    assert cp.band_label(9) == BAND_LABELS[9] == ("Viên Mãn", "Apex")


# ── 7. apply_combat_xp ──────────────────────────────────────────────────────
def test_apply_combat_xp_rolls_within_band():
    # L3 → needs 20 to reach 4; +25 → L4 with 5 leftover, not at ceiling.
    assert cp.apply_combat_xp(3, 0, 25) == (4, 5, False)
    # L1 + 5 (needs 8) → stays L1 with 5 xp.
    assert cp.apply_combat_xp(1, 0, 5) == (1, 5, False)


def test_apply_combat_xp_stops_at_ceilings_and_discards_overshoot():
    # L1 +999 → rolls 1→2 (ceiling 2). Overshoot discarded, xp 0, at_ceiling.
    assert cp.apply_combat_xp(1, 0, 999) == (2, 0, True)
    # L3 chain → 3→4→5 (ceiling). 20+30=50 needed; +999 stops at 5, xp 0.
    assert cp.apply_combat_xp(3, 0, 999) == (5, 0, True)
    # L6 chain → 6→7→8 (ceiling). 45+65=110 needed; +999 stops at 8, xp 0.
    assert cp.apply_combat_xp(6, 0, 999) == (8, 0, True)


def test_apply_combat_xp_stops_at_max():
    # Already at a ceiling: no XP transition exists → discard, at_ceiling True.
    assert cp.apply_combat_xp(2, 0, 50) == (2, 0, True)
    # At MAX 9: stays, overshoot discarded.
    assert cp.apply_combat_xp(MAX_LEVEL, 0, 50) == (MAX_LEVEL, 0, True)


# ── 8. combat_xp_gain ───────────────────────────────────────────────────────
def test_combat_xp_gain_grade_scaling_and_win_bonus():
    # trash scales to 0 regardless of win.
    assert cp.combat_xp_gain("trash", True) == 0
    assert cp.combat_xp_gain("trash", False) == 0
    # normal: (1 + win?1) × 1.0.
    assert cp.combat_xp_gain("normal", False) == 1
    assert cp.combat_xp_gain("normal", True) == 2
    # elite ×2.0.
    assert cp.combat_xp_gain("elite", False) == 2
    assert cp.combat_xp_gain("elite", True) == 4
    # boss ×3.0, world_boss ×4.0.
    assert cp.combat_xp_gain("boss", True) == 6
    assert cp.combat_xp_gain("world_boss", True) == 8
    # Unknown grade → 0.0 scale.
    assert cp.combat_xp_gain("mystery", True) == 0


# ── 9. breakthrough_chance ──────────────────────────────────────────────────
def test_breakthrough_chance_base_pity_talisman():
    # Gate 2: base 0.85, pity 0.05.
    assert cp.breakthrough_chance(2, 0, False) == pytest.approx(0.85)
    assert cp.breakthrough_chance(2, 2, False) == pytest.approx(0.95)
    # Hộ Thể Phù adds 0.20, clamped to 1.0.
    assert cp.breakthrough_chance(2, 0, True) == pytest.approx(1.0)  # 0.85+0.20→clamp
    # Gate 5: base 0.60, pity 0.08; 2 fails + talisman = 0.96.
    assert cp.breakthrough_chance(5, 2, True) == pytest.approx(0.96)
    # Gate 8: base 0.40, pity 0.10.
    assert cp.breakthrough_chance(8, 1, False) == pytest.approx(0.50)


# ── 10. resolve_breakthrough ────────────────────────────────────────────────
def test_resolve_breakthrough_success_consumes_and_advances():
    # Gate 5 chance 0.60; roll 0.0 < chance → success.
    out = cp.resolve_breakthrough(5, 0, False, False, 0.0)
    assert out["success"] is True
    assert out["new_level"] == 6
    assert out["new_fails"] == 0
    assert out["consumed_gate_qty"] == GATES[5]["qty"]  # 5
    assert out["refunded"] is False
    assert out["consumed_dinh_the_chau"] is False


def test_resolve_breakthrough_fail_consumes_stack_no_refund():
    # Gate 5 chance 0.60; roll 0.99 ≥ chance → fail, no Định Thể Châu.
    out = cp.resolve_breakthrough(5, 0, False, False, 0.99)
    assert out["success"] is False
    assert out["new_level"] == 5
    assert out["new_fails"] == 1
    assert out["consumed_gate_qty"] == GATES[5]["qty"]  # consumed
    assert out["refunded"] is False


def test_resolve_breakthrough_fail_refunds_with_dinh_the_chau():
    # Fail with Định Thể Châu → gate stack refunded (qty 0), Châu still consumed.
    out = cp.resolve_breakthrough(5, 1, False, True, 0.99)
    assert out["success"] is False
    assert out["new_level"] == 5
    assert out["new_fails"] == 2
    assert out["consumed_gate_qty"] == 0          # refunded
    assert out["refunded"] is True
    assert out["consumed_dinh_the_chau"] is True  # Châu consumed on the fail


def test_resolve_breakthrough_ho_the_phu_consumed_on_any_attempt():
    # Success: Hộ Thể Phù consumed, Định Thể Châu untouched.
    win = cp.resolve_breakthrough(2, 0, True, True, 0.0)
    assert win["consumed_ho_the_phu"] is True
    assert win["consumed_dinh_the_chau"] is False
    # Fail: Hộ Thể Phù still consumed.
    lose = cp.resolve_breakthrough(8, 0, True, False, 0.999)
    assert lose["consumed_ho_the_phu"] is True


# ── 11. progress_summary / breakthrough_preview ─────────────────────────────
def test_progress_summary_within_band_and_at_ceiling():
    mid = cp.progress_summary(1, 4)
    assert mid["band_vi"] == "Sơ Khai"
    assert mid["band_en"] == "Nascent"
    assert mid["level"] == 1
    assert mid["cap"] == MAX_LEVEL
    assert mid["xp_next"] == 8
    assert mid["at_ceiling"] is False
    assert mid["ratio"] == pytest.approx(0.5)  # 4 / 8

    ceiling = cp.progress_summary(5, 0)
    assert ceiling["xp_next"] is None
    assert ceiling["at_ceiling"] is True
    assert ceiling["ratio"] == 1.0


def test_breakthrough_preview_off_gate_and_on_gate():
    # A non-ceiling level → no gate.
    assert cp.breakthrough_preview(3, 0, 10, False, False) == {"at_gate": False}

    # Gate 5 with 5 owned (== required), 1 fail, talisman → ~68%.
    prev = cp.breakthrough_preview(5, 1, 5, True, False)
    assert prev["at_gate"] is True
    assert prev["gate_item_key"] == "ConsProcLuyenThe"
    assert prev["required_qty"] == 5
    assert prev["owned_qty"] == 5
    assert prev["has_enough"] is True
    assert prev["trial_tier"] == 2
    # 0.60 + 1×0.08 + 0.20 = 0.88 → 88.
    assert prev["success_pct"] == 88

    # Not enough materials.
    short = cp.breakthrough_preview(8, 0, 3, False, False)
    assert short["has_enough"] is False  # 3 < 8


# ── 12. Inertness of the compute_constitution_bonuses seam ──────────────────
def test_compute_constitution_bonuses_seam_inert_when_none():
    """``process_levels=None`` is byte-identical to the pre-existing call.

    Proves the new optional argument does not perturb the real constitution
    read path — the contract the Phase-0 guard relies on. Uses a REAL
    registered constitution so the whole Hỗn Độn amplification machinery runs.
    """
    from src.data.registry import registry

    const_key = "ConstitutionKimCotThe"
    assert registry.get_constitution(const_key) is not None

    pre_existing = compute_constitution_bonuses(const_key, "body", 5)
    with_none = compute_constitution_bonuses(const_key, "body", 5, process_levels=None)
    assert with_none == pre_existing
    assert pre_existing, "guard constitution must contribute a non-empty dict"
