"""Cultivation realm definitions — per-realm EXP tables.

Values sourced from ``docs/data.xlsx`` → sheet "Hệ Thống Tu Luyện".
Each realm has its own cumulative bậc-1..9 threshold table; rate scales
1..9 across realms. The final realm's bậc-9 threshold equals
``TRIBULATION_EXP_COST`` — one full Độ Kiếp attempt.

Threshold shape: quadratic gap growth (``_upward``). The XP gap to
advance into bậc N is ``step × N²``, so the bậc 8→9 gap is **81×** the
bậc 0→1 gap. Each successive layer of insight costs the square of its
position — the final few breakthroughs dominate the realm grind,
sharper than a linear gap curve. bậc 9 = 285 × step.
"""
from __future__ import annotations

from dataclasses import dataclass


LEVELS_PER_REALM = 9
MERIT_TO_FORMATION_EXP_RATIO = 10
# Endgame (realm 8) tribulation attempt cost = R8 bậc-9 threshold under the
# quadratic shape (``_upward(150_000)`` = 150_000 × 285 = 42_750_000). Sized
# so a Grade-9 Hoàn pill (level_exp_table[-1] // _TARGET_PILLS_PER_REALM[8]
# per ``alchemy._pill_xp_for_grade``) grants strictly more XP than a
# Grade-8 Hoàn pill, keeping the pill-grade hierarchy monotonic.
TRIBULATION_EXP_COST = 42_750_000


@dataclass(frozen=True)
class Realm:
    index: int
    key: str
    vi: str
    en: str
    base_exp_rate: int               # EXP per cultivation chu kỳ
    level_exp_table: tuple[int, ...]  # cumulative bậc-1..9 thresholds


def _upward(step: int) -> tuple[int, ...]:
    """Cumulative thresholds with quadratically-growing per-bậc gaps.

    Gap from bậc n-1 → n is ``step × n²`` (sum of squares), so the bậc 8→9
    gap is 81× the bậc 0→1 gap. The closed-form for the cumulative sum
    is ``step × n(n+1)(2n+1)/6``. The late bậc carries an overwhelming
    share of the realm — early bậc clear in a session, the final few
    become a multi-week wall before tribulation. bậc 9 = 285 × step.
    """
    return tuple(
        step * n * (n + 1) * (2 * n + 1) // 6
        for n in range(1, LEVELS_PER_REALM + 1)
    )


# Per-realm EXP tables — bậc 1..9 cumulative within the realm.
_R0 = _upward(133)                                                              # Luyện Khí (rate 1) — bậc 9 = 37,905
_R1 = _upward(800)                                                              # Trúc Cơ    (rate 2) — bậc 9 = 228,000
_R2 = _upward(2_400)                                                            # Kim Đan    (rate 3) — bậc 9 = 684,000
_R3 = _upward(6_400)                                                            # Nguyên Anh (rate 4) — bậc 9 = 1,824,000
_R4 = _upward(14_667)                                                           # Hóa Thần   (rate 5) — bậc 9 = 4,180,095
_R5 = _upward(28_800)                                                           # Luyện Hư   (rate 6) — bậc 9 = 8,208,000
_R6 = _upward(67_200)                                                           # Hợp Đạo    (rate 7) — bậc 9 = 19,152,000
_R7 = _upward(115_200)                                                          # Đại Thừa   (rate 8) — bậc 9 = 32,832,000
_R8 = _upward(150_000)                                                          # Đăng Tiên  (rate 9) — bậc 9 = 42,750,000 = TRIBULATION_EXP_COST

_LEVEL_TABLES: tuple[tuple[int, ...], ...] = (_R0, _R1, _R2, _R3, _R4, _R5, _R6, _R7, _R8)
_BASE_RATES:   tuple[int, ...]             = (1, 2, 3, 4, 5, 6, 7, 8, 9)


def _build(names: list[tuple[str, str, str]], rates: tuple[int, ...] = _BASE_RATES) -> list[Realm]:
    return [
        Realm(i, key, vi, en, rates[i], _LEVEL_TABLES[i])
        for i, (key, vi, en) in enumerate(names)
    ]


BODY_REALMS: list[Realm] = _build([
    ("luyen_huyet", "Luyện Huyết", "Blood Tempering"),
    ("luyen_bi",    "Luyện Bì",    "Skin Tempering"),
    ("luyen_can",   "Luyện Cân",   "Tendon Tempering"),
    ("luyen_cot",   "Luyện Cốt",   "Bone Tempering"),
    ("luyen_phu",   "Luyện Phủ",   "Organ Tempering"),
    ("phap_tuong",  "Pháp Tướng",  "Dharma Form"),
    ("kim_than",    "Kim Thân",    "Golden Body"),
    ("sieu_pham",   "Siêu Phàm",   "Transcendent"),
    ("nhap_thanh",  "Nhập Thánh",  "Saint Entry"),
])

QI_REALMS: list[Realm] = _build([
    ("luyen_khi",   "Luyện Khí",   "Qi Refining"),
    ("truc_co",     "Trúc Cơ",     "Foundation Establishment"),
    ("kim_dan",     "Kim Đan",     "Golden Core"),
    ("nguyen_anh",  "Nguyên Anh",  "Nascent Soul"),
    ("hoa_than",    "Hóa Thần",    "Spirit Transformation"),
    ("luyen_hu",    "Luyện Hư",    "Void Refinement"),
    ("hop_dao",     "Hợp Đạo",     "Dao Integration"),
    ("dai_thua",    "Đại Thừa",    "Mahayana"),
    ("dang_tien",   "Đăng Tiên",   "Immortal Ascension"),
])

# Trận Đạo: rate 0 on all realms — advances only via Công Đức (study_formation_with_merit).
_FORMATION_RATES: tuple[int, ...] = (0,) * LEVELS_PER_REALM

FORMATION_REALMS: list[Realm] = _build([
    ("khai_huyen",  "Khai Huyền",  "Mystery Opening"),
    ("nhap_huyen",  "Nhập Huyền",  "Mystery Entry"),
    ("luyen_huyen", "Luyện Huyền", "Mystery Tempering"),
    ("dung_huyen",  "Dung Huyền",  "Mystery Fusion"),
    ("tam_tran",    "Tâm Trận",    "Heart Formation"),
    ("thien_tran",  "Thiên Trận",  "Heavenly Formation"),
    ("than_tran",   "Thần Trận",   "Divine Formation"),
    ("thanh_tran",  "Thánh Trận",  "Saint Formation"),
    ("de_tran",     "Đế Trận",     "Emperor Formation"),
], rates=_FORMATION_RATES)


_AXIS_REALMS: dict[str, list[Realm]] = {
    "body":      BODY_REALMS,
    "qi":        QI_REALMS,
    "formation": FORMATION_REALMS,
}


def get_realm(axis: str, realm_idx: int) -> Realm | None:
    r_list = _AXIS_REALMS.get(axis)
    if not r_list or not (0 <= realm_idx < len(r_list)):
        return None
    return r_list[realm_idx]


def get_level_from_exp(exp: int, realm: Realm) -> int:
    """Return bậc 1..9 for the given xp within ``realm``'s table.

    Bậc 1 is the baseline (any xp ≥ 0 yields at least 1); crossing each
    successive threshold advances one bậc, up to 9.
    """
    level = 1
    for i, threshold in enumerate(realm.level_exp_table, start=1):
        if exp >= threshold:
            level = i
        else:
            break
    return min(level, LEVELS_PER_REALM)


def realm_label(axis: str, realm_idx: int, xp: int) -> str:
    """Display label e.g. ``'Luyện Khí (Bậc 5)'``."""
    realm = get_realm(axis, realm_idx)
    if realm is None:
        return "Vô Cảnh Giới"
    level = get_level_from_exp(xp, realm)
    return f"{realm.vi} (Bậc {level})"
