"""Item grade system."""
from __future__ import annotations

from enum import IntEnum


class Grade(IntEnum):
    HOANG = 1   # Yellow — lowest
    HUYEN = 2   # Mystic
    DIA = 3     # Earth
    THIEN = 4   # Heaven — highest


GRADE_LABELS: dict[Grade, tuple[str, str]] = {
    Grade.HOANG: ("Hoàng Phẩm", "Yellow Grade"),
    Grade.HUYEN: ("Huyền Phẩm", "Mystic Grade"),
    Grade.DIA: ("Địa Phẩm", "Earth Grade"),
    Grade.THIEN: ("Thiên Phẩm", "Heaven Grade"),
}

# Payment currency per grade for P2P trades
# Hoàng/Huyền/Địa → Công Đức; Thiên → Hỗn Nguyên Thạch
GRADE_CURRENCY: dict[Grade, str] = {
    Grade.HOANG: "merit",
    Grade.HUYEN: "merit",
    Grade.DIA: "merit",
    Grade.THIEN: "primordial_stones",
}
