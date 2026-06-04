"""Constitution Process (Tiến Trình Thể Chất) constants and lookup tables.

Phase 1, pure core. A constitution progresses from level 1 to 9 across four
milestone bands. Within a band, levels advance via combat XP. Crossing a
ceiling (2, 5, 8) into a milestone level (3, 6, 9) is trial+material-gated,
not XP-gated — see GATES, CEILINGS and XP_TO_NEXT.

Structurally this mirrors ``src.game.constants.skill_mastery``: the numbers
below are tunable later, but the table shapes are locked.
"""
from __future__ import annotations

MAX_LEVEL = 9
MILESTONES = [1, 3, 6, 9]   # passive-unlock levels (L1 = base, gated tiers at 3/6/9)

# Breakthroughs are crossed AT ceilings 2/5/8 -> INTO milestone levels 3/6/9.
# These ceilings cap XP rollover until the player clears the trial+material gate.
CEILINGS = (2, 5, 8)

# XP to leave `level` for `level+1`, WITHIN a band only. Ceilings 2/5/8 (gates)
# and 9 (MAX) are deliberately absent — exactly like skill_mastery omits 5/10/15/20/25.
XP_TO_NEXT = {1: 8, 3: 20, 4: 30, 6: 45, 7: 65}

# Gates keyed by the CEILING level the body is stuck at; breakthrough -> level+1.
GATES = {
    2: {"item_key": "ConsProcThangThe", "qty": 3, "base_success": 0.85, "pity_per_fail": 0.05, "trial_tier": 1},
    5: {"item_key": "ConsProcLuyenThe", "qty": 5, "base_success": 0.60, "pity_per_fail": 0.08, "trial_tier": 2},
    8: {"item_key": "ConsProcDaoThe",   "qty": 8, "base_success": 0.40, "pity_per_fail": 0.10, "trial_tier": 3},
}

HO_THE_PHU_BONUS = 0.20
HO_THE_PHU_KEY = "ConsProcHoThePhu"
DINH_THE_CHAU_KEY = "ConsProcDinhTheChau"

# XP source: a constitution earns XP per completed PvE combat while active.
XP_PER_COMBAT = 1
XP_WIN_BONUS = 1
XP_GRADE_SCALE = {"trash": 0.0, "normal": 1.0, "elite": 2.0, "boss": 3.0, "world_boss": 4.0}

# (vietnamese, english) milestone band labels, keyed by milestone level.
BAND_LABELS: dict[int, tuple[str, str]] = {
    1: ("Sơ Khai", "Nascent"),
    3: ("Tiểu Thành", "Tempered"),
    6: ("Đại Thành", "Consummate"),
    9: ("Viên Mãn", "Apex"),
}


def band_of(level: int) -> int:
    """Map ``level`` to the highest milestone <= level (1/3/6/9).

    Clamped to [1, MAX_LEVEL]; a level below the first milestone still reads 1.
    """
    clamped = max(1, min(level, MAX_LEVEL))
    band = MILESTONES[0]
    for m in MILESTONES:
        if m <= clamped:
            band = m
        else:
            break
    return band
