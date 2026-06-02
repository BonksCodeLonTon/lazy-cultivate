"""Skill Mastery constants and lookup tables (Phase 1, pure core).

Mastery progresses a single skill from level 1 to 25 across five bands.
Within a band, levels advance via combat XP. Crossing a band ceiling
(5, 10, 15, 20) is item-gated, not XP-gated — see GATES and XP_TO_NEXT.
"""
from __future__ import annotations

from enum import IntEnum

MAX_LEVEL = 25
VISIBLE_MAX = 20  # levels 21-25 are hidden until the secret gate is revealed

# Power multiplier applied to skill output per mastery level. Locked values.
POWER_MULT: dict[int, float] = {
    1: 1.00,
    2: 1.02,
    3: 1.04,
    4: 1.06,
    5: 1.08,
    6: 1.13,
    7: 1.15,
    8: 1.17,
    9: 1.19,
    10: 1.22,
    11: 1.28,
    12: 1.30,
    13: 1.33,
    14: 1.36,
    15: 1.40,
    16: 1.47,
    17: 1.50,
    18: 1.53,
    19: 1.57,
    20: 1.62,
    21: 1.75,
    22: 1.79,
    23: 1.83,
    24: 1.87,
    25: 1.92,
}

# XP required to leave `level` for `level + 1`, WITHIN a band only.
# WHY: gate transitions 5->6, 10->11, 15->16, 20->21 are item-gated breakthroughs,
# not XP rollovers, so keys 5/10/15/20 (and the MAX ceiling 25) are deliberately absent.
XP_TO_NEXT: dict[int, int] = {
    # Band 1: So Khuy (1-5)
    1: 20,
    2: 30,
    3: 45,
    4: 60,
    # Band 2: Tieu Thanh (6-10)
    6: 90,
    7: 120,
    8: 160,
    9: 210,
    # Band 3: Dai Thanh (11-15)
    11: 280,
    12: 360,
    13: 460,
    14: 580,
    # Band 4: Vien Man (16-20)
    16: 720,
    17: 880,
    18: 1080,
    19: 1320,
    # Band 5: Dang Phong Tao Cuc (21-25)
    21: 1700,
    22: 2100,
    23: 2600,
    24: 3200,
}


class MasteryBand(IntEnum):
    SO_KHUY = 1
    TIEU_THANH = 2
    DAI_THANH = 3
    VIEN_MAN = 4
    DANG_PHONG_TAO_CUC = 5


# (vietnamese, english) display labels per band.
BAND_LABELS: dict[MasteryBand, tuple[str, str]] = {
    MasteryBand.SO_KHUY: ("Sơ Khuy", "Initiate"),
    MasteryBand.TIEU_THANH: ("Tiểu Thành", "Minor Mastery"),
    MasteryBand.DAI_THANH: ("Đại Thành", "Great Mastery"),
    MasteryBand.VIEN_MAN: ("Viên Mãn", "Consummation"),
    MasteryBand.DANG_PHONG_TAO_CUC: ("Đăng Phong Tạo Cực", "Transcendent Peak"),
}

# Inclusive (low, high) level range per band; a level maps to the band whose
# range contains it.
BAND_RANGES: dict[MasteryBand, tuple[int, int]] = {
    MasteryBand.SO_KHUY: (1, 5),
    MasteryBand.TIEU_THANH: (6, 10),
    MasteryBand.DAI_THANH: (11, 15),
    MasteryBand.VIEN_MAN: (16, 20),
    MasteryBand.DANG_PHONG_TAO_CUC: (21, 25),
}

# Breakthrough gates keyed by the ceiling level being crossed.
# base_success: chance with 0 fails and no talisman.
# pity_per_fail: additive success bonus per prior failed attempt at this gate.
# hidden: whether the gate is concealed (the 20->21 secret gate).
GATES: dict[int, dict] = {
    5: {
        "item_key": "MasteryLinhNgoPhu",
        "qty": 4,
        "base_success": 0.90,
        "pity_per_fail": 0.05,
        "hidden": False,
    },
    10: {
        "item_key": "MasteryTamDacNgoc",
        "qty": 6,
        "base_success": 0.70,
        "pity_per_fail": 0.08,
        "hidden": False,
    },
    15: {
        "item_key": "MasteryDaoVanTinh",
        "qty": 10,
        "base_success": 0.50,
        "pity_per_fail": 0.10,
        "hidden": False,
    },
    20: {
        "item_key": "MasteryThongThienDaoQua",
        "qty": 1,
        "base_success": 0.35,
        "pity_per_fail": 0.10,
        "hidden": True,
    },
}

# Talismans.
# Hộ Đạo Phù: additive success bonus, consumed on every breakthrough attempt.
HO_DAO_PHU_BONUS = 0.20
HO_DAO_PHU_KEY = "MasteryHoDaoPhu"
# Định Đạo Châu: refunds the gate stack on a failed attempt.
DINH_DAO_CHAU_KEY = "MasteryDinhDaoChau"
